"""Queue transport with status/event changes delegated to the lifecycle service."""
from ..lifecycle.models import StageStatus
from ...adapters.queue.postgresql import PostgreSQLQueueStore


class RuntimeQueueStore(PostgreSQLQueueStore):
    def __init__(self, sessions, config):
        super().__init__(sessions, config)
        self.states = None  # Set once by compose_runtime, before any work.
        self.replay_captured_results = False

    @staticmethod
    def _pipeline_stage(session, run, attempt):
        if attempt.stage_id in {'analysis','architecture'}:
            return True
        from .models import RunRuntimeState
        state = session.get(RunRuntimeState, run.id)
        source = next((a for a in (state.payload.get('attempts', []) if state else [])
                       if a['attempt_id'] == str(attempt.id)), None)
        inputs = source.get('input_state', {}) if source else {}
        task = inputs.get('task', {})
        return (inputs.get('schema_version') == 'generation-task/1' and task.get('stage_id') == attempt.stage_id
                and task in inputs.get('plan', {}).get('tasks', []))

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
        # A durably captured pipeline response is replayable within the same
        # attempt. Fencing still revokes the old execution; no domain retry or
        # duplicate LLM dispatch is needed to finish its checkpoint/acceptance.
        from ..artifacts.models import ExecutionReceipt, ModelCall
        from sqlalchemy import select
        if self.replay_captured_results:
            for call in session.scalars(select(ModelCall).where(ModelCall.attempt_id == attempt.id)):
                if call.payload['status'] == 'reserved':
                    call.payload = {**call.payload, 'status': 'in_doubt'}
        accepted = session.get(ExecutionReceipt, (attempt.id, 'result')) if self.replay_captured_results else None
        captured = (session.scalar(select(ModelCall.call_id).where(ModelCall.attempt_id == attempt.id,
            ModelCall.payload['response'].is_not(None))) if self.replay_captured_results else None)
        if (self.replay_captured_results and self._pipeline_stage(session, run, attempt) and attempt.status == 'running'
                and run.status == 'running' and (accepted is not None or captured is not None)):
            row.reason = reason
            self._clear_lease(row, 'lost')
            with self.states.transaction(session, run) as tx:
                current = next(a for a in tx.snapshot.attempts if a.attempt_id == attempt.id)
                tx.fence_attempt(attempt_id=attempt.id, generation=current.generation)
            return
        row.reason = reason
        self._clear_lease(row, "lost")
        if attempt.status == "running" and run.status in {"running", "cancelling"}:
            with self.states.transaction(session, run) as tx:
                current = next(a for a in tx.snapshot.attempts if a.attempt_id == attempt.id)
                generation = tx.fence_attempt(attempt_id=attempt.id, generation=current.generation)
                tx.transition_attempt(attempt_id=attempt.id, expected="running", target="failed",
                                      generation=generation, reason_code=reason)

    @staticmethod
    def _assert_recovery_outcome(session, run, attempt):
        if attempt.status in {'succeeded', 'waiting_for_input'} and RuntimeQueueStore._pipeline_stage(session, run, attempt):
            from ..artifacts.models import ExecutionReceipt
            from hashlib import sha256
            import json
            result = session.get(ExecutionReceipt, (attempt.id, 'result'))
            checkpoint = session.get(ExecutionReceipt, (attempt.id, 'checkpoint'))
            if (result is not None and checkpoint is not None and result.payload['status'] == attempt.status
                    and checkpoint.payload.get('result_checksum') == sha256(json.dumps(result.payload,
                        sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()):
                return
        return PostgreSQLQueueStore._assert_recovery_outcome(session, run, attempt)

    def _recovery_exhausted(self, session, run, attempt):
        if run.status not in {"running", "cancelling"}:
            return
        with self.states.transaction(session, run) as tx:
            cancelling = tx.snapshot.status == "cancelling"
            for current in tx.snapshot.attempts:
                if current.attempt_id == attempt.id and current.status == 'running' and self.replay_captured_results:
                    tx.transition_attempt(attempt_id=current.attempt_id, expected='running', target='failed',
                        generation=current.generation, reason_code='RECOVERY_INFRA_EXHAUSTED')
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
