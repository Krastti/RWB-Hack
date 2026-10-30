"""C-03 state machine; all effects and seq allocation share a repository lock."""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from functools import wraps
import json
import re
from typing import Callable, Iterator, Literal
from uuid import UUID

from .models import (
    Attempt, Event, EventPage, LifecycleError, RunRecord, RunStatus, StageStatus,
    conflict, utcnow,
)
from .repository import LifecycleRepository


RUN_TRANSITIONS = frozenset({
    ("queued", "running"), ("running", "waiting_for_input"),
    ("waiting_for_input", "running"), ("running", "completed"),
    ("running", "failed"), ("running", "budget_exceeded"),
    ("queued", "cancelling"), ("running", "cancelling"),
    ("waiting_for_input", "cancelling"), ("cancelling", "cancelled"),
    ("failed", "queued"), ("budget_exceeded", "queued"),
})
STAGE_TRANSITIONS = frozenset({
    ("pending", "running"), ("pending", "failed"),
    ("pending", "cancelled"), ("pending", "skipped"),
    ("running", "succeeded"), ("running", "waiting_for_input"),
    ("running", "failed"), ("running", "cancelled"), ("running", "skipped"),
})
FINAL_RUNS = {RunStatus.COMPLETED, RunStatus.CANCELLED}
ACTIVE_ATTEMPTS = {StageStatus.PENDING, StageStatus.RUNNING}


@dataclass(frozen=True)
class Change:
    """Domain guard input. No lease tokens are put into public Event.data."""
    kind: Literal["create", "run", "register_attempt", "attempt", "event"]
    source: str | None = None
    target: str | None = None
    attempt_id: UUID | None = None
    stage_id: str | None = None
    event_type: str | None = None
    event_data: dict | None = None


# Mandatory server-side integration: checks real accepted results, checkpoint,
# profile, required questions/answers, budget, policy, outbox and idempotency.
# Called inside the transaction on a COPY of current authoritative state.
TransitionGuard = Callable[[RunRecord, Change], None]


def _json(value) -> None:
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise LifecycleError(422, "VALIDATION_ERROR", "Payload must contain finite JSON values") from exc


def _reason(value: str) -> None:
    if not isinstance(value, str) or re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", value) is None:
        raise LifecycleError(422, "VALIDATION_ERROR", "Invalid safe reason code")


def _command(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        if not self._open or self._failed:
            raise conflict("Lifecycle transaction is closed or aborted")
        try:
            return method(self, *args, **kwargs)
        except BaseException:
            self._failed = True
            raise
    return wrapped


class LifecycleTransaction:
    """Use only through LifecycleService.transaction; snapshots are detached."""
    def __init__(self, record: RunRecord, guard: TransitionGuard):
        self._record, self._guard = record, guard
        self._open, self._failed, self._dirty = True, False, False

    @property
    def snapshot(self) -> RunRecord:
        return self._record.model_copy(deep=True)

    def _mutable(self):
        if self._record.status in FINAL_RUNS:
            raise conflict("Completed and cancelled runs are immutable")

    def _check(self, change: Change):
        if self._guard(self.snapshot, deepcopy(change)) is not None:
            raise LifecycleError(422, "VALIDATION_ERROR", "Guard must approve with None or raise an error")

    def _event(self, event_type: str, data: dict, stage_id: str | None = None) -> Event:
        _json(data)
        record = self._record
        event = Event(run_id=record.run_id, seq=record.last_seq + 1,
                      event_type=event_type, timestamp=utcnow(), stage_id=stage_id,
                      data=deepcopy({**data, "schema_version": "1.0"}))
        record.events.append(event)
        record.last_seq = event.seq
        record.updated_at = event.timestamp
        self._dirty = True
        return event.model_copy(deep=True)

    def _attempt(self, attempt_id: UUID, generation: int) -> Attempt:
        attempts = [a for a in self._record.attempts if a.attempt_id == attempt_id]
        if len(attempts) != 1:
            raise conflict("Unknown attempt")
        attempt = attempts[0]
        latest = max((a.attempt_no for a in self._record.attempts if a.stage_id == attempt.stage_id))
        if (attempt.attempt_no != latest or type(generation) is not int
                or generation != attempt.generation):
            raise conflict("Stale attempt or execution generation")
        return attempt

    @_command
    def update_data(self, update: Callable[[dict], None]) -> None:
        """Stage domain effects for this transaction; callback receives only data.

        Save actual artifact refs/counters/checkpoint intents/outbox/replay here,
        then perform the guarded transition. Callback failure aborts everything.
        No external calls: non-transactional writes cannot be rolled back here.
        """
        self._mutable()
        candidate = deepcopy(self._record.data)
        update(candidate)
        _json(candidate)
        if candidate != self._record.data:
            self._record.data = deepcopy(candidate)
            self._dirty = True

    @_command
    def transition_run(self, *, expected: RunStatus, target: RunStatus, reason_code: str) -> None:
        self._mutable()
        _reason(reason_code)
        record = self._record
        if record.status != expected or (expected, target) not in RUN_TRANSITIONS:
            raise conflict("Unexpected or forbidden run transition")
        if target in {"waiting_for_input", "completed", "failed", "budget_exceeded", "cancelled"}:
            if any(a.status in ACTIVE_ATTEMPTS for a in record.attempts):
                raise conflict("Active attempts have not reached a safe boundary")
        if target == "completed":
            latest = {a.stage_id: max((b for b in record.attempts if b.stage_id == a.stage_id),
                                     key=lambda b: b.attempt_no) for a in record.attempts}
            if not latest or any(a.status not in {"succeeded", "skipped"} for a in latest.values()):
                raise conflict("Unresolved stage results prevent completion")
        if expected in {"failed", "budget_exceeded", "waiting_for_input"} and target in {"queued", "running"}:
            previous_status = "waiting_for_input" if expected == "waiting_for_input" else "failed"
            if not any(a.status == "pending" and a.attempt_no > 1 and any(
                b.stage_id == a.stage_id and b.attempt_no == a.attempt_no - 1 and b.status == previous_status
                for b in record.attempts) for a in record.attempts):
                raise conflict("Resume requires a new pending attempt, not rewriting history")
        self._check(Change(kind="run", source=str(expected), target=str(target)))
        source = record.status
        record.status = target
        if target in {"completed", "cancelled", "failed", "budget_exceeded"}:
            record.stopped_at = utcnow()
        elif target in {"queued", "running"}:
            record.stopped_at = None
        self._event("run.status_changed", {"from": str(source), "to": str(record.status),
                                           "reason_code": reason_code})

    @_command
    def register_attempt(self, *, stage_id: str, contract_version: str, input_hash: str,
                         reason_code: str, attempt_id: UUID | None = None) -> Attempt:
        self._mutable()
        _reason(reason_code)
        record = self._record
        if record.status == "cancelling":
            raise conflict("Cancellation forbids new attempts")
        if not all(isinstance(v, str) and v for v in (stage_id, contract_version, input_hash)):
            raise LifecycleError(422, "VALIDATION_ERROR", "Attempt identity and input are required")
        if (re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", stage_id) is None
                or len(contract_version) > 128 or not contract_version.strip()
                or re.fullmatch(r"[a-f0-9]{64}", input_hash) is None
                or (attempt_id is not None and not isinstance(attempt_id, UUID))):
            raise LifecycleError(422, "VALIDATION_ERROR", "Invalid attempt identity or contract version")
        history = [a for a in record.attempts if a.stage_id == stage_id]
        previous = max(history, key=lambda a: a.attempt_no) if history else None
        if previous and previous.status not in {"failed", "waiting_for_input"}:
            raise conflict("Attempt is active or its successful result must be preserved")
        if record.status in {"failed", "budget_exceeded", "waiting_for_input"}:
            required = "waiting_for_input" if record.status == "waiting_for_input" else "failed"
            if previous is None or previous.status != required:
                raise conflict("Stopped run requires retry/resume of its unsuccessful stage")
        if previous and (previous.contract_version != contract_version or previous.input_hash != input_hash):
            # Answers are allowed to create a new immutable input; the guard
            # verifies that only confirmed answers changed it, not requirements.
            if previous.status != "waiting_for_input" or previous.contract_version != contract_version:
                raise conflict("Retry must preserve the original input and contract")
        attempt = Attempt(stage_id=stage_id, contract_version=contract_version, input_hash=input_hash,
                          attempt_no=previous.attempt_no + 1 if previous else 1,
                          **({"attempt_id": attempt_id} if attempt_id is not None else {}))
        if any(a.attempt_id == attempt.attempt_id for a in record.attempts):
            raise conflict("Attempt identity already exists")
        # Give the domain guard the proposed attempt without modifying history.
        proposed = self.snapshot
        proposed.attempts.append(attempt.model_copy(deep=True))
        if self._guard(proposed, Change(kind="register_attempt", target="pending",
                                       attempt_id=attempt.attempt_id, stage_id=stage_id)) is not None:
            raise LifecycleError(422, "VALIDATION_ERROR", "Guard must approve with None or raise an error")
        record.attempts.append(attempt)
        record.attempts.sort(key=lambda a: (a.stage_id, a.attempt_no))
        self._event("stage.status_changed", {"from": None, "to": "pending",
                    "attempt_no": attempt.attempt_no, "contract_version": contract_version,
                    "reason_code": reason_code}, stage_id)
        return attempt.model_copy(deep=True)

    @_command
    def transition_attempt(self, *, attempt_id: UUID, expected: StageStatus, target: StageStatus,
                           generation: int, reason_code: str) -> Attempt:
        self._mutable()
        _reason(reason_code)
        record = self._record
        attempt = self._attempt(attempt_id, generation)
        if attempt.status != expected or (expected, target) not in STAGE_TRANSITIONS:
            raise conflict("Unexpected or forbidden attempt transition")
        if target == "running" and record.status != "running":
            raise conflict("Run does not admit new execution")
        if target != "running" and record.status not in {"running", "cancelling"}:
            raise conflict("Attempt result cannot be applied to this run")
        if record.status == "cancelling" and expected == "pending" and target != "cancelled":
            raise conflict("Unstarted attempts must be cancelled")
        self._check(Change(kind="attempt", source=str(expected), target=str(target),
                           attempt_id=attempt_id, stage_id=attempt.stage_id))
        source = attempt.status
        attempt.status, attempt.reason_code = target, reason_code
        if target == "running":
            attempt.started_at = utcnow()
            attempt.generation += 1
        else:
            attempt.finished_at = utcnow()
        self._event("stage.status_changed", {"from": str(source), "to": str(attempt.status),
                    "attempt_no": attempt.attempt_no, "contract_version": attempt.contract_version,
                    "reason_code": reason_code}, attempt.stage_id)
        return attempt.model_copy(deep=True)

    @_command
    def fence_attempt(self, *, attempt_id: UUID, generation: int) -> int:
        """Revoke an active generation before recording WORKER_LOST/timeout.

        The lease owner performs this through the common repository. Tokens are
        private and never included in events. This is not permission to re-run an
        old attempt: recovery registers N+1 according to C-03.
        """
        self._mutable()
        attempt = self._attempt(attempt_id, generation)
        if self._record.status not in {"running", "cancelling"} or attempt.status != "running":
            raise conflict("No active execution to fence")
        attempt.generation += 1
        self._dirty = True
        return attempt.generation

    @_command
    def publish(self, *, event_type: str, data: dict, stage_id: str | None = None) -> Event:
        """Append a domain event once; transport redelivery uses the stored Event."""
        self._mutable()
        if (not isinstance(event_type, str) or re.fullmatch(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+", event_type) is None
                or event_type in {"run.created", "run.status_changed", "stage.status_changed"}
                or not isinstance(data, dict) or "schema_version" in data):
            raise LifecycleError(422, "VALIDATION_ERROR", "Invalid or reserved domain event")
        if event_type.startswith(("stage.", "retry.", "usage.")) and not stage_id:
            raise LifecycleError(422, "VALIDATION_ERROR", "Stage identity is required")
        _json(data)
        self._check(Change(kind="event", event_type=event_type, event_data=deepcopy(data), stage_id=stage_id))
        return self._event(event_type, data, stage_id)


class LifecycleService:
    def __init__(self, repository: LifecycleRepository, *, guard: TransitionGuard):
        if not callable(guard):
            raise TypeError("A server-side transition guard is required")
        self.repository, self.guard = repository, guard

    def create(self, *, owner_id: UUID, run_id: UUID, context: dict, data: dict) -> RunRecord:
        """B-05 supplies validated immutable snapshot and initial outbox/replay.

        This is an internal insertion seam, not a replacement for POST /runs.
        """
        if not isinstance(context, dict) or not context or not isinstance(data, dict):
            raise LifecycleError(422, "VALIDATION_ERROR", "Immutable run context is required")
        _json(context)
        _json(data)
        try:
            UUID(context["revision_id"])
            if not isinstance(context["profile_id"], str) or not context["profile_id"]:
                raise ValueError("Missing profile")
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise LifecycleError(422, "VALIDATION_ERROR", "Run context requires revision_id UUID and profile_id") from exc
        record = RunRecord(owner_id=owner_id, run_id=run_id, context=deepcopy(context), data=deepcopy(data))
        if self.guard(record.model_copy(deep=True), Change(kind="create", target="queued")) is not None:
            raise LifecycleError(422, "VALIDATION_ERROR", "Guard must approve with None or raise an error")
        tx = LifecycleTransaction(record, self.guard)
        tx._event("run.created", {"status": "queued", "revision_id": context["revision_id"],
                                   "profile_id": context["profile_id"]})
        tx._event("metrics.updated", {"metrics": {"tokens_in": 0, "tokens_out": 0, "model_calls": 0,
                   "attempts": 0, "manual_clarifications": 0, "duration_ms": 0},
                   "tokens_complete": {"tokens_in": True, "tokens_out": True}})
        self.repository.insert(record)
        return record.model_copy(deep=True)

    @contextmanager
    def transaction(self, *, owner_id: UUID, run_id: UUID, expected_version: int) -> Iterator[LifecycleTransaction]:
        with self.repository.transaction(owner_id=owner_id, run_id=run_id) as record:
            if type(expected_version) is not int or record.version != expected_version:
                raise conflict("Stale run version")
            tx = LifecycleTransaction(record, self.guard)
            try:
                yield tx
                if tx._failed:
                    raise conflict("Lifecycle transaction was aborted")
                if record.status in {"waiting_for_input", "completed", "failed", "budget_exceeded", "cancelled"}:
                    if any(a.status in ACTIVE_ATTEMPTS for a in record.attempts):
                        raise conflict("Stopped run cannot commit active attempts")
                if tx._dirty:
                    record.version += 1
                    record.updated_at = utcnow()
            finally:
                tx._open = False

    def get(self, *, owner_id: UUID, run_id: UUID) -> RunRecord:
        """Internal snapshot, not a C-01 Run response (contains private fields)."""
        with self.repository.transaction(owner_id=owner_id, run_id=run_id) as record:
            return record.model_copy(deep=True)

    def events(self, *, owner_id: UUID, run_id: UUID, after_seq: int = 0, limit: int = 100) -> EventPage:
        if type(after_seq) is not int or after_seq < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise LifecycleError(422, "VALIDATION_ERROR", "Invalid event cursor or limit")
        with self.repository.transaction(owner_id=owner_id, run_id=run_id) as record:
            visible = [e for e in record.events if e.seq > after_seq]
            items = [e.model_copy(deep=True) for e in visible[:limit]]
            return EventPage(items=items, next_after_seq=items[-1].seq if items else after_seq,
                             has_more=len(visible) > limit)
