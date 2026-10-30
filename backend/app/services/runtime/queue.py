"""Queue transport with status/event changes delegated to the lifecycle service."""
from ..lifecycle.models import StageStatus
from ...adapters.queue.postgresql import PostgreSQLQueueStore


class RuntimeQueueStore(PostgreSQLQueueStore):
    def __init__(self, sessions, config):
        super().__init__(sessions, config)
        self.states = None  # Set once by compose_runtime, before any work.

    def _start_attempt(self, session, run, attempt):
        with self.states.transaction(session, run) as tx:
            if tx.snapshot.status == "queued":
                tx.transition_run(expected="queued", target="running", reason_code="WORKER_STARTED")
            current = next(a for a in tx.snapshot.attempts if a.attempt_id == attempt.id)
            tx.transition_attempt(attempt_id=attempt.id, expected=StageStatus.PENDING,
                                  target=StageStatus.RUNNING, generation=current.generation,
                                  reason_code="WORKER_STARTED")
            tx.update_data(lambda data: data["run"].update(current_stage=attempt.stage_id))

    def _mark_lost(self, session, row, run, attempt, reason):
        row.reason = reason
        self._clear_lease(row, "lost")
        if attempt.status == "running" and run.status in {"running", "cancelling"}:
            with self.states.transaction(session, run) as tx:
                current = next(a for a in tx.snapshot.attempts if a.attempt_id == attempt.id)
                generation = tx.fence_attempt(attempt_id=attempt.id, generation=current.generation)
                tx.transition_attempt(attempt_id=attempt.id, expected="running", target="failed",
                                      generation=generation, reason_code=reason)

    def _recovery_exhausted(self, session, run, attempt):
        if run.status not in {"running", "cancelling"}:
            return
        with self.states.transaction(session, run) as tx:
            cancelling = tx.snapshot.status == "cancelling"
            for pending in tx.snapshot.attempts:
                if pending.status == "pending":
                    tx.transition_attempt(attempt_id=pending.attempt_id, expected="pending",
                                          target="cancelled" if cancelling else "failed",
                                          generation=pending.generation, reason_code="RECOVERY_INFRA_EXHAUSTED")
            if cancelling:
                if not any(a.status == "running" for a in tx.snapshot.attempts):
                    tx.transition_run(expected="cancelling", target="cancelled", reason_code="RUN_CANCELLED")
            elif any(a.status == "running" for a in tx.snapshot.attempts):
                tx.update_data(lambda data: data["saved_state"].update(terminal_intent={
                    "target": "failed", "reason_code": "RECOVERY_INFRA_EXHAUSTED", "stage_id": attempt.stage_id}))
            else:
                tx.transition_run(expected="running", target="failed", reason_code="RECOVERY_INFRA_EXHAUSTED")
