"""Cancel/retry commands and worker safe-boundary hooks, C-03 v1.0."""
from ..lifecycle.events import EventSequenceError, next_event
from ..lifecycle.service import RUN_TRANSITIONS, STAGE_TRANSITIONS
from copy import deepcopy
from hashlib import sha256
import json
import re
from typing import Callable
from uuid import UUID, uuid4

from .models import (
    AttemptState, Checkpoint, ControlError, Limits, Record, Retry, Run,
    StageAttempt, conflict, now,
)
from .store import ControlStore


class RunControlService:
    def __init__(self, store: ControlStore, *, authorize_retry: Callable[[Record, Limits], None]):
        self.store = store
        # Политика проверяет владельца, лимиты и один активный Run в транзакции.
        # При отказе 429 она передаёт рассчитанный retry_after в ControlError.
        self.authorize_retry = authorize_retry

    @staticmethod
    def _event(record: Record, event_type: str, *, stage_id: str | None = None, **data):
        try:
            event = next_event(run_id=record.run.run_id, events=record.events,
                               event_type=event_type, data=data, stage_id=stage_id)
        except EventSequenceError as exc:
            raise conflict(str(exc)) from exc
        record.events.append(event.model_dump(mode="json", exclude_none=True))
        record.run.updated_at = now()

    def _status(self, record: Record, status: str):
        previous = record.run.status
        if (previous, status) not in RUN_TRANSITIONS:
            raise conflict("Forbidden lifecycle transition")
        record.run.status = status
        self._event(record, "run.status_changed", **{"from": previous, "to": status,
                                                     "reason_code": "RUN_CONTROL"})

    def _stage_event(self, record: Record, attempt: StageAttempt, previous: str | None):
        if previous is not None and (previous, attempt.status) not in STAGE_TRANSITIONS:
            raise conflict("Forbidden lifecycle stage transition")
        self._event(record, "stage.status_changed", stage_id=attempt.stage_id,
                    attempt_no=attempt.attempt_no, contract_version=attempt.contract_version,
                    **{"from": previous, "to": attempt.status, "reason_code": "RUN_CONTROL"})

    @staticmethod
    def _latest(record: Record, stage_id: str) -> StageAttempt:
        attempts = [a for a in record.run.stages if a.stage_id == stage_id]
        if not attempts:
            raise conflict("No attempt for this execution stage")
        return max(attempts, key=lambda a: a.attempt_no)

    @staticmethod
    def _private(record: Record, attempt: StageAttempt) -> AttemptState:
        matches = [a for a in record.attempts
                   if (a.stage_id, a.attempt_no) == (attempt.stage_id, attempt.attempt_no)]
        if len(matches) != 1:
            raise ControlError(500, "RESUME_STATE_UNAVAILABLE", "Attempt input is unavailable")
        return matches[0]

    def _cancel_boundary(self, record: Record):
        if record.run.status != "cancelling":
            return
        for attempt in record.run.stages:
            if attempt.status == "pending":
                attempt.status, attempt.finished_at = "cancelled", now()
                attempt.reason = "RUN_CANCELLED"
                self._stage_event(record, attempt, "pending")
        if not any(a.status == "running" for a in record.run.stages):
            # Фиксируем длительность на последнем безопасном барьере отмены.
            stopped_at = now()
            record.run.metrics.duration_ms = max(
                0, int((stopped_at - record.run.created_at).total_seconds() * 1000),
            )
            self._status(record, "cancelled")
            self._event(record, "metrics.updated", metrics=record.run.metrics.model_dump(),
                        tokens_complete=record.tokens_complete)

    @staticmethod
    def _profile(record):
        if record.run.profile_id != "web-fastapi-angular":
            raise ControlError(409, "UNSUPPORTED_RUN_PROFILE", "Historical Run is read-only")

    def cancel(self, *, owner_id: UUID, run_id: UUID) -> Run:
        with self.store.transaction(owner_id, run_id) as record:
            self._profile(record)
            if record.run.status in {"queued", "running", "waiting_for_input"}:
                self._status(record, "cancelling")
                # A worker/barrier dispatcher observes the durable intent even
                # when there is no active call. Do not lose it on API restart.
                record.outbox.append({"id": str(uuid4()), "kind": "cancel", "run_id": str(run_id)})
                self._cancel_boundary(record)
            return record.run.model_copy(deep=True)

    def _next_unstarted_stage(self, record: Record) -> tuple[str, str, dict]:
        """Resolve only a frozen, ready, never-executed task from trusted saved state.

        Producers retain generation_plan and pending_inputs before the budget stop.
        This is not an orchestrator: it cannot invent inputs or skip dependencies.
        """
        from contracts.agents.validate import Contracts
        try:
            plan = record.saved_state["generation_plan"]
            Contracts().validate("generation-plan", plan)
            if plan["profile_id"] != record.run.profile_id:
                raise ValueError()
            by_id = {task["task_id"]: task for task in plan["tasks"]}
            latest = {a.stage_id: self._latest(record, a.stage_id) for a in record.run.stages}
            if any(a.status not in {"succeeded", "skipped"} for a in latest.values()):
                raise ValueError()
            ready = [task for task in plan["tasks"] if task["stage_id"] not in latest and all(
                by_id[parent]["stage_id"] in latest for parent in task["depends_on"])]
            task = next(iter(ready))
            if task["depends_on"]:
                cp = record.checkpoint
                if (cp is None or cp.run_id != record.run.run_id or cp.revision_id != record.run.revision_id
                        or cp.state_version != record.state_version or cp.state != record.saved_state
                        or cp.contract_version != plan["contract_version"]
                        or cp.input_hash not in {a.input_hash for a in record.attempts if a.accepted_result}):
                    raise ValueError()
                for parent in task["depends_on"]:
                    accepted = latest[by_id[parent]["stage_id"]]
                    receipt = self._private(record, accepted)
                    result = receipt.accepted_result or {}
                    Contracts().validate("agent-result", result)
                    if (result.get("status") != accepted.status or result.get("run_id") != str(record.run.run_id)
                            or result.get("stage_id") != accepted.stage_id
                            or result.get("attempt") != accepted.attempt_no
                            or result.get("contract_version") != accepted.contract_version):
                        raise ValueError()
            source = record.saved_state["pending_inputs"][task["stage_id"]]
            request = source["input_state"]
            Contracts().validate("agent-input", request)
            if (not re.fullmatch(r"[a-f0-9]{64}", source["input_hash"])
                    or request["stage_id"] != task["stage_id"] or request["attempt"] != 1
                    or request["run_id"] != str(record.run.run_id)
                    or request["profile_id"] != record.run.profile_id
                    or request["contract_version"] != plan["contract_version"]
                    or set(request["input_artifact_ids"]) != set(task["input_artifact_ids"])):
                raise ValueError()
            return task["stage_id"], plan["contract_version"], deepcopy(source)
        except (ValueError, KeyError, TypeError, StopIteration):
            raise ControlError(500, "RESUME_STATE_UNAVAILABLE",
                               "Frozen plan, next-stage inputs or confirmed dependency state are unavailable") from None

    def retry(self, *, owner_id: UUID, run_id: UUID, key: str, body: Retry) -> Run:
        if not isinstance(key, str) or re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", key) is None:
            raise ControlError(422, "VALIDATION_ERROR", "Invalid Idempotency-Key")
        fingerprint = sha256(json.dumps(body.model_dump(mode="json", exclude_none=True),
                                        sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        with self.store.transaction(owner_id, run_id) as record:
            self._profile(record)
            replay = record.replays.get(key)  # Scope: owner + run + retry.
            if replay is not None:
                if replay["hash"] != fingerprint:
                    raise ControlError(409, "IDEMPOTENCY_KEY_CONFLICT", "Key was used with a different request")
                return Run.model_validate(replay["response"])
            if record.run.status not in {"failed", "budget_exceeded"}:
                raise conflict()
            if (record.run.status == "budget_exceeded" and
                    record.run.stop_reason not in {"MAX_CALLS", "MAX_OUTPUT_TOKENS"}):
                raise ControlError(409, "RETRY_NOT_SUPPORTED", "Retry cannot increase the exhausted limit")
            if any(a.status in {"running", "pending"} for a in record.run.stages):
                raise conflict("Run has unresolved executions")
            failed = sorted({a.stage_id for a in record.run.stages
                             if self._latest(record, a.stage_id).status == "failed"})
            stage_id = record.run.current_stage if record.run.current_stage in failed else next(iter(failed), None)
            previous = None
            if stage_id is None:
                if record.run.status == "budget_exceeded":
                    stage_id, contract_version, source = self._next_unstarted_stage(record)
                    attempt_no = 1
                    input_hash, input_state = source["input_hash"], source["input_state"]
                else:
                    latest = {a.stage_id: self._latest(record, a.stage_id) for a in record.run.stages}
                    if latest and all(a.status in {"succeeded", "skipped"} for a in latest.values()):
                        raise ControlError(409, "RECOVERY_REQUIRED", "Accepted results require internal checkpoint or assembly recovery")
                    raise conflict("Run has no failed execution stage")
            else:
                previous = self._latest(record, stage_id)
                source = self._private(record, previous)
                if not record.saved_state or not source.input_state or not source.input_hash:
                    raise ControlError(500, "RESUME_STATE_UNAVAILABLE", "Saved recovery state is unavailable")
                contract_version, attempt_no = previous.contract_version, previous.attempt_no + 1
                input_hash, input_state = source.input_hash, source.input_state
            limits = body.limits or record.run.limits
            old = record.run.limits
            if limits.max_calls < old.max_calls or limits.max_output_tokens < old.max_output_tokens:
                raise conflict("Retry cannot reduce limits")
            if record.run.status == "budget_exceeded":
                required = record.required_limits
                if (body.limits is None or required is None
                        or (limits.max_calls == old.max_calls and limits.max_output_tokens == old.max_output_tokens)
                        or (record.run.stop_reason == "MAX_CALLS" and limits.max_calls <= old.max_calls)
                        or (record.run.stop_reason == "MAX_OUTPUT_TOKENS" and limits.max_output_tokens <= old.max_output_tokens)
                        or limits.max_calls <= record.run.metrics.model_calls
                        or limits.max_output_tokens <= record.run.metrics.tokens_out
                        or limits.max_calls < required.max_calls
                        or limits.max_output_tokens < required.max_output_tokens):
                    raise conflict("Exhausted limits must be increased enough for next work")
            self.authorize_retry(record, limits.model_copy(deep=True))
            attempt = StageAttempt(stage_id=stage_id, attempt_no=attempt_no,
                                   contract_version=contract_version, status="pending")
            private = AttemptState(stage_id=stage_id, attempt_no=attempt.attempt_no,
                                   input_hash=input_hash, input_state=deepcopy(input_state))
            record.run.stages.append(attempt)
            record.run.stages.sort(key=lambda a: (a.stage_id, a.attempt_no))
            record.attempts.append(private)
            record.run.metrics.attempts += 1
            record.run.limits = limits.model_copy(deep=True)
            if limits != old:
                self._event(record, "budget.updated", previous_limits=old.model_dump(),
                            limits=limits.model_dump(), reason_code="MANUAL_RETRY")
            record.run.current_stage = stage_id
            record.run.stop_reason = None
            self._stage_event(record, attempt, None)
            if previous is not None:
                self._event(record, "retry.scheduled", stage_id=stage_id, mode="manual",
                            previous_attempt_no=previous.attempt_no, next_attempt_no=attempt.attempt_no,
                            reason_code="MANUAL_RETRY")
            self._status(record, "queued")
            self._event(record, "metrics.updated", metrics=record.run.metrics.model_dump(),
                        tokens_complete=record.tokens_complete)
            record.outbox.append({"id": str(uuid4()), "kind": "retry", "run_id": str(run_id),
                                  "stage_id": stage_id, "attempt_id": str(private.attempt_id),
                                  "attempt_no": attempt.attempt_no})
            response = record.run.model_copy(deep=True)
            record.replays[key] = {"hash": fingerprint, "response": response.model_dump(mode="json")}
            return response

    def start_attempt(self, *, owner_id: UUID, run_id: UUID, stage_id: str,
                      attempt_id: UUID) -> dict:
        """Worker admission immediately before work; return checkpoint or saved state.

        Worker must hold its external lease, validate inputs/versions and reserve
        budget. Every additional LLM call needs a new cancellation admission via
        the common run transaction; this hook admits one stage invocation.
        """
        with self.store.transaction(owner_id, run_id) as record:
            self._profile(record)
            if record.run.status not in {"queued", "running"}:
                raise conflict()
            attempt = self._latest(record, stage_id)
            private = self._private(record, attempt)
            if private.attempt_id != attempt_id or attempt.status != "pending":
                raise conflict("Stale or already claimed attempt")
            cp = record.checkpoint
            if (cp is not None and cp.run_id == run_id and cp.revision_id == record.run.revision_id
                    and cp.state_version == record.state_version
                    and cp.input_hash == private.input_hash
                    and cp.contract_version == attempt.contract_version):
                state = cp.state
            else:
                state = record.saved_state
            if not state:
                raise ControlError(500, "RESUME_STATE_UNAVAILABLE", "Saved recovery state is unavailable")
            if record.run.status == "queued":
                self._status(record, "running")
            attempt.status, attempt.started_at = "running", now()
            self._stage_event(record, attempt, "pending")
            return deepcopy(state)

    def admit_call(self, *, owner_id: UUID, run_id: UUID, stage_id: str,
                   attempt_id: UUID, reserve_budget: Callable[[Record], None]) -> None:
        """Serialize each LLM admission/reservation against cancel, before dispatch.

        reserve_budget records the real call/reservation in the locked aggregate.
        An admitted call is active work and must later report a result/deadline,
        even if cancellation arrives before the provider responds.
        """
        with self.store.transaction(owner_id, run_id) as record:
            self._profile(record)
            attempt = self._latest(record, stage_id)
            private = self._private(record, attempt)
            if (record.run.status != "running" or attempt.status != "running"
                    or private.attempt_id != attempt_id):
                raise conflict("New calls are forbidden for this execution")
            reserve_budget(record)
            # Каждый успешный допуск публикует снимок вместе с резервированием.
            self._event(record, "metrics.updated", metrics=record.run.metrics.model_dump(),
                        tokens_complete=record.tokens_complete)

    def finish_attempt(self, *, owner_id: UUID, run_id: UUID, stage_id: str,
                       attempt_id: UUID, accepted_result: dict,
                       save_boundary: Callable[[Record], dict]) -> Run:
        """Safe boundary AFTER validation/usage accounting by worker, BEFORE scheduling.

        accepted_result is the real validated C-02 AgentResult. save_boundary
        merges artifacts/usage into the locked record and returns a full graph
        snapshot, preserving other workers' accepted results. It runs in this
        transaction; failure rolls back everything, including attempt completion.
        """
        with self.store.transaction(owner_id, run_id) as record:
            self._profile(record)
            if record.run.status not in {"running", "cancelling"}:
                raise conflict()
            attempt = self._latest(record, stage_id)
            private = self._private(record, attempt)
            if private.attempt_id != attempt_id or attempt.status != "running":
                raise conflict("Stale or already finished attempt")
            status = accepted_result.get("status")
            if (status not in {"succeeded", "failed", "waiting_for_input", "cancelled", "skipped"}
                    or accepted_result.get("run_id") != str(run_id)
                    or accepted_result.get("stage_id") != stage_id
                    or accepted_result.get("attempt") != attempt.attempt_no
                    or accepted_result.get("contract_version") != attempt.contract_version):
                raise ControlError(422, "VALIDATION_ERROR", "Result identity or recovery state is invalid")
            private.accepted_result = deepcopy(accepted_result)
            attempt.status, attempt.finished_at = status, now()
            previous_metrics = record.run.metrics.model_dump()
            previous_completeness = dict(record.tokens_complete)
            saved_state = save_boundary(record)
            if not isinstance(saved_state, dict) or not saved_state:
                raise ControlError(500, "RESUME_STATE_UNAVAILABLE", "Saved recovery state is unavailable")
            record.saved_state = deepcopy(saved_state)
            record.state_version += 1
            record.checkpoint = Checkpoint(run_id=run_id, revision_id=record.run.revision_id,
                                           contract_version=attempt.contract_version,
                                           input_hash=private.input_hash,
                                           state_version=record.state_version, state=deepcopy(saved_state))
            self._stage_event(record, attempt, "running")
            if (record.run.metrics.model_dump() != previous_metrics
                    or record.tokens_complete != previous_completeness):
                self._event(record, "metrics.updated", metrics=record.run.metrics.model_dump(),
                            tokens_complete=record.tokens_complete)
            self._cancel_boundary(record)
            # Normal completion/error/question/budget policy belongs to the
            # orchestrator. In cancelling it may ONLY finish the cancel barrier.
            return record.run.model_copy(deep=True)

    def reach_boundary(self, *, owner_id: UUID, run_id: UUID) -> Run:
        """Dispatcher hook when no call is active (including waiting_for_input)."""
        with self.store.transaction(owner_id, run_id) as record:
            self._profile(record)
            self._cancel_boundary(record)
            return record.run.model_copy(deep=True)
