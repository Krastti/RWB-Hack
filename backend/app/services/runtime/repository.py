"""One PostgreSQL write boundary for control, lifecycle and worker composition."""
from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select

from ...models import Project, Run as RunRow, RunEvent, StageAttempt, User
from ...models.run_creation import RunInputSnapshot
from ..lifecycle.models import Event
from ..run_control.models import ControlError, Record, Run, now
from .models import RunRuntimeState


PRIVATE_FIELDS = {"owner_id", "run", "events"}
ACTIVE = {"queued", "running", "waiting_for_input", "cancelling"}


class PostgreSQLRuntimeRepository:
    def __init__(self, sessions, queue):
        self.sessions, self.queue = sessions, queue

    @contextmanager
    def transaction(self, owner_id: UUID, run_id: UUID):
        # Same admission lock/order as createRun: User -> Run. Workers only lock
        # Run and never acquire User afterwards.
        with self.sessions.begin() as session:
            user = session.scalar(select(User).where(User.id == owner_id).with_for_update())
            if user is None or user.status != "active":
                raise ControlError(401, "UNAUTHORIZED", "Authentication required")
            run = session.scalar(select(RunRow).join(Project).where(
                RunRow.id == run_id, Project.owner_id == owner_id,
            ).with_for_update(of=RunRow))
            if run is None:
                raise ControlError(404, "NOT_FOUND", "Run not found")
            with self.bound(session, run) as record:
                yield record

    @contextmanager
    def bound(self, session, run):
        """Use only while the caller holds Run FOR UPDATE in this transaction."""
        record, state = self.load(session, run)
        original = record.model_copy(deep=True)
        yield record
        if record != original:
            self.save(session, run, state, record, original)

    def load(self, session, run):
        try:
            return self._load(session, run)
        except (ValidationError, KeyError, TypeError, ValueError) as exc:
            raise ControlError(500, "INTERNAL_ERROR", "Invalid stored runtime state") from exc

    def _load(self, session, run):
        owner_id = session.scalar(select(Project.owner_id).where(Project.id == run.project_id))
        stages = list(session.scalars(select(StageAttempt).where(
            StageAttempt.run_id == run.id,
        ).order_by(StageAttempt.stage_id, StageAttempt.attempt_no)))
        events = [Event.model_validate(e, from_attributes=True).model_dump(mode="json", exclude_none=True)
                  for e in session.scalars(select(RunEvent).where(
                      RunEvent.run_id == run.id).order_by(RunEvent.seq))]
        state = session.get(RunRuntimeState, run.id)
        payload = deepcopy(state.payload) if state is not None else {"attempts": [], "saved_state": {}}
        # Historical inputs/receipts are never reconstructed from public DTOs.
        public = Run.model_validate({
            "run_id": run.id, **{name: getattr(run, name) for name in Run.model_fields
                                   if name not in {"run_id", "stages"}},
            "stages": [dict(stage_id=a.stage_id, attempt_no=a.attempt_no,
                            contract_version=a.contract_version, status=a.status,
                            started_at=a.started_at, finished_at=a.finished_at,
                            reason=a.reason, error=a.error) for a in stages],
        })
        record = Record(owner_id=owner_id, run=public, events=events, **payload)
        if state is None:
            state = RunRuntimeState(run_id=run.id, version=1, payload=payload, generations={})
            session.add(state)
        return record, state

    def control_in(self, session, run):
        """Control hooks inside execution.transaction(); never open another Session."""
        repository = self

        class BoundStore:
            @contextmanager
            def transaction(self, owner_id, run_id):
                if run_id != run.id:
                    raise ControlError(404, "NOT_FOUND", "Run not found")
                with repository.bound(session, run) as record:
                    if record.owner_id != owner_id:
                        raise ControlError(404, "NOT_FOUND", "Run not found")
                    yield record

        # A worker cannot admit public retries while holding a lease/Run lock.
        def deny_retry(*_):
            raise ControlError(409, "RUN_STATE_CONFLICT", "Retry requires HTTP admission")

        from ..run_control import RunControlService
        return RunControlService(BoundStore(), authorize_retry=deny_retry)

    def save(self, session, row, state, record, original):
        if (record.owner_id != original.owner_id or any(
                getattr(record.run, name) != getattr(original.run, name)
                for name in ("run_id", "project_id", "revision_id", "profile_id", "created_at"))):
            raise ControlError(409, "RUN_STATE_CONFLICT", "Immutable Run identity changed")
        if record.events[:len(original.events)] != original.events:
            raise ControlError(409, "RUN_STATE_CONFLICT", "Event history is append-only")
        if original.run.status in {"completed", "cancelled"}:
            raise ControlError(409, "RUN_STATE_CONFLICT", "Final Run is immutable")
        if original.run.status not in ACTIVE and record.run.status in ACTIVE:
            active = session.scalar(select(RunRow.id).join(Project).where(
                Project.owner_id == record.owner_id, RunRow.id != row.id,
                RunRow.status.in_(ACTIVE),
            ).limit(1))
            if active is not None:
                # Slot lifetime is unknown; do not invent Retry-After.
                from ..runs.dto import RunCreationError
                raise RunCreationError(429, "RATE_LIMIT_EXCEEDED", "An active run already exists")
        private = {(a.stage_id, a.attempt_no): a for a in record.attempts}
        old_private = {a.attempt_id: a for a in original.attempts}
        existing = {(a.stage_id, a.attempt_no): a for a in session.scalars(
            select(StageAttempt).where(StageAttempt.run_id == row.id))}
        for attempt in record.run.stages:
            key = (attempt.stage_id, attempt.attempt_no)
            source = private.get(key)
            saved = existing.get(key)
            if saved is None:
                if source is None or not source.input_state:
                    raise ControlError(500, "RESUME_STATE_UNAVAILABLE", "Attempt input is unavailable")
                saved = StageAttempt(id=source.attempt_id, run_id=row.id,
                                     input_hash=source.input_hash, **attempt.model_dump())
                session.add(saved)
            else:
                if source is not None and (source.attempt_id != saved.id or source.input_hash != saved.input_hash):
                    raise ControlError(409, "RUN_STATE_CONFLICT", "Attempt identity changed")
                for name, value in attempt.model_dump().items():
                    setattr(saved, name, value)
            if source is not None and source.attempt_id in old_private:
                old = old_private[source.attempt_id]
                if source.input_state != old.input_state or source.input_hash != old.input_hash:
                    raise ControlError(409, "RUN_STATE_CONFLICT", "Attempt input changed")
        for name in ("status", "current_stage", "stop_reason", "questions_revision"):
            setattr(row, name, getattr(record.run, name))
        row.limits = record.run.limits.model_dump(mode="json")
        row.metrics = record.run.metrics.model_dump(mode="json")
        row.questions = [q.model_dump(mode="json", exclude_none=True) for q in record.run.questions]
        row.updated_at = record.run.updated_at
        if row.status in {"completed", "cancelled", "failed", "budget_exceeded"}:
            if original.run.status != row.status or row.stopped_at is None:
                row.stopped_at = now()
        else:
            row.stopped_at = None
        for item in record.events[len(original.events):]:
            event = Event.model_validate(item)
            session.add(RunEvent(**event.model_dump()))
        state.payload = record.model_dump(mode="json", exclude=PRIVATE_FIELDS)
        state.version += 1
        session.flush()
        for item in record.outbox[len(original.outbox):]:
            if item["kind"] == "retry":
                self.queue.enqueue(session, UUID(item["attempt_id"]))
            elif item["kind"] != "cancel":
                raise ControlError(500, "INTERNAL_ERROR", "Unsupported runtime intent")

    @staticmethod
    def frozen_input(session, run):
        source = session.get(RunInputSnapshot, run.id)
        if source is None:
            raise ControlError(500, "RESUME_STATE_UNAVAILABLE", "Frozen Run input is unavailable")
        value = deepcopy(source.payload)
        if (value.get("revision_id") != str(run.revision_id)
                or value.get("profile", {}).get("profile_id") != run.profile_id
                or not isinstance(value.get("requirements_text"), str)
                or value.get("requirements_hash") != sha256(value["requirements_text"].encode()).hexdigest()):
            raise ControlError(500, "RESUME_STATE_UNAVAILABLE", "Frozen Run context is inconsistent")
        return value
