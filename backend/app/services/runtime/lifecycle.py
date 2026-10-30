"""Adapt the existing lifecycle service to the shared SQL transaction."""
from contextlib import contextmanager
from copy import deepcopy

from sqlalchemy import select

from ...models import Project, StageAttempt
from ..lifecycle import LifecycleService
from ..lifecycle.models import Attempt, Event, LifecycleError, RunRecord
from ..run_control.models import Record
from .models import RunRuntimeState


def execution_guard(record, change):
    """Baseline admission only; output acceptance needs the domain integration."""
    if record.context["profile_id"] != "web-fastapi-angular":
        raise LifecycleError(409, "UNSUPPORTED_RUN_PROFILE", "Historical Run is read-only")
    if change.kind == "run" and (change.source, change.target) not in {
        ("queued", "running"), ("running", "failed"), ("cancelling", "cancelled"),
    }:
        raise LifecycleError(409, "DOMAIN_GUARD_REQUIRED", "A domain transition guard is required")
    if change.kind == "attempt" and change.target not in {"running", "failed", "cancelled"}:
        raise LifecycleError(409, "DOMAIN_GUARD_REQUIRED", "An accepted result requires a domain guard")
    if change.kind == "register_attempt":
        sources = record.data["attempts"]
        if not any(a["attempt_id"] == str(change.attempt_id) and a["input_state"] for a in sources):
            raise LifecycleError(409, "RESUME_STATE_UNAVAILABLE", "Frozen attempt input is required")


class BoundLifecycleRepository:
    def __init__(self, repository, session, run):
        self.repository, self.session, self.run = repository, session, run

    def insert(self, record):
        raise RuntimeError("Runs are created through RunCreationService")

    @contextmanager
    def transaction(self, *, owner_id, run_id):
        session, row = self.session, self.run
        if run_id != row.id:
            raise LifecycleError(404, "NOT_FOUND", "Run not found")
        with self.repository.bound(session, row) as control:
            if control.owner_id != owner_id:
                raise LifecycleError(404, "NOT_FOUND", "Run not found")
            session.flush()
            state = session.get(RunRuntimeState, row.id)
            attempts = list(session.scalars(select(StageAttempt).where(StageAttempt.run_id == row.id)))
            frozen = self.repository.frozen_input(session, row)
            life = RunRecord(
                run_id=row.id, owner_id=owner_id, status=control.run.status, version=state.version,
                last_seq=control.events[-1]["seq"] if control.events else 0,
                created_at=row.created_at, updated_at=row.updated_at, stopped_at=row.stopped_at,
                context={**frozen, "profile_id": row.profile_id},
                data=control.model_dump(mode="json", exclude={"events"}),
                attempts=[Attempt(attempt_id=a.id, stage_id=a.stage_id, attempt_no=a.attempt_no,
                                  contract_version=a.contract_version, input_hash=a.input_hash,
                                  status=a.status, generation=state.generations.get(str(a.id), 0),
                                  started_at=a.started_at, finished_at=a.finished_at, reason_code=a.reason)
                          for a in attempts],
                events=[Event.model_validate(e) for e in control.events],
            )
            original = life.model_copy(deep=True)
            yield life
            if life == original:
                return
            if life.context != original.context:
                raise LifecycleError(409, "RUN_STATE_CONFLICT", "Frozen context changed")
            updated = Record(**deepcopy(life.data), events=[
                e.model_dump(mode="json", exclude_none=True) for e in life.events])
            updated.run.status, updated.run.updated_at = str(life.status), life.updated_at
            by_id = {a.attempt_id: a for a in updated.attempts}
            by_key = {(a.stage_id, a.attempt_no): a for a in updated.run.stages}
            from ..run_control.models import StageAttempt as PublicAttempt
            for a in life.attempts:
                if a.attempt_id not in by_id:
                    raise LifecycleError(409, "RESUME_STATE_UNAVAILABLE", "Attempt input is unavailable")
                public = by_key.get((a.stage_id, a.attempt_no))
                if public is None:
                    public = PublicAttempt(stage_id=a.stage_id, attempt_no=a.attempt_no,
                                           contract_version=a.contract_version, status=str(a.status))
                    updated.run.stages.append(public)
                public.status, public.reason = str(a.status), a.reason_code
                public.started_at, public.finished_at = a.started_at, a.finished_at
            control.run, control.attempts = updated.run, updated.attempts
            for name in Record.model_fields:
                if name not in {"run", "attempts"}:
                    setattr(control, name, getattr(updated, name))
            state.generations = {str(a.attempt_id): a.generation for a in life.attempts}


class RunStateService:
    def __init__(self, repository, *, guard=execution_guard, checkpoint_confirmed=None):
        self.repository = repository
        self.guard, self.checkpoint_confirmed = guard, checkpoint_confirmed

    @contextmanager
    def transaction(self, session, run):
        """Caller holds Run FOR UPDATE; commit belongs to caller (or worker lease)."""
        repository = BoundLifecycleRepository(self.repository, session, run)
        session.flush()
        owner_id = session.scalar(select(Project.owner_id).where(Project.id == run.project_id))
        service = LifecycleService(repository, guard=self.guard, checkpoint_confirmed=self.checkpoint_confirmed)
        # Bootstrap private metadata under the same lock, without copying Run rows.
        _, state = self.repository.load(session, run)
        with service.transaction(owner_id=owner_id, run_id=run.id, expected_version=state.version) as tx:
            yield tx
