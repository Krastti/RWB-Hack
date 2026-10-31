"""Helpers for bounded recovery of lost stage executions."""


def automatic_retry_count(saved_state, events, stage_id):
    """Reconstruct legacy counters from durable events without resetting usage."""
    counts = saved_state.get("auto_retry_counts", {})
    if not isinstance(counts, dict):
        raise ValueError("Invalid automatic retry counters")
    stored = counts.get(stage_id, 0)
    if type(stored) is not int or stored < 0:
        raise ValueError("Invalid automatic retry count")
    historical = 0
    for event in events:
        if event.get("event_type") != "retry.scheduled" or event.get("stage_id") != stage_id:
            continue
        data = event.get("data")
        if not isinstance(data, dict) or data.get("mode") not in {"automatic", "manual"}:
            raise ValueError("Ambiguous historical retry event")
        if data["mode"] == "automatic":
            historical += 1
    return max(stored, historical)


class RuntimeRecovery:
    """Recover a fenced stage through the same transaction as its queue lease."""

    def __init__(self, states, queue):
        self.states = states
        self.queue = queue

    def __call__(self, command, execution):
        from copy import deepcopy
        from sqlalchemy import select
        from ...models import Run, StageAttempt
        from ..run_control.models import AttemptState

        with execution.transaction(recovery_complete=True) as session:
            run = session.scalar(select(Run).where(Run.id == command.run_id).with_for_update())
            successor_id = None
            with self.states.transaction(session, run) as tx:
                snapshot = tx.snapshot
                attempts = [a for a in snapshot.attempts if a.stage_id == command.stage_id]
                latest = max(attempts, key=lambda a: a.attempt_no)
                if latest.attempt_id != command.attempt_id:
                    from ...adapters.queue.postgresql import PostgreSQLQueueStore
                    old = session.get(StageAttempt, command.attempt_id)
                    PostgreSQLQueueStore._assert_recovery_outcome(session, run, old)
                    return
                if latest.status != "failed":
                    raise RuntimeError("Recovery attempt is no longer current")
                if snapshot.status in {"cancelled", "completed", "failed", "budget_exceeded"}:
                    return
                if snapshot.status == "cancelling":
                    for a in snapshot.attempts:
                        if a.status == "pending":
                            tx.transition_attempt(attempt_id=a.attempt_id, expected="pending", target="cancelled",
                                                  generation=a.generation, reason_code="RUN_CANCELLED")
                    if not any(a.status == "running" for a in tx.snapshot.attempts):
                        tx.transition_run(expected="cancelling", target="cancelled", reason_code="RUN_CANCELLED")
                    return
                if snapshot.status != "running":
                    raise RuntimeError("Recovery requires an active run")
                data = snapshot.data
                source = next((a for a in data["attempts"] if a["attempt_id"] == str(command.attempt_id)), None)
                events = [e.model_dump(mode="json") for e in snapshot.events]
                count = automatic_retry_count(data["saved_state"], events, command.stage_id)
                metrics, limits = data["run"]["metrics"], data["run"]["limits"]
                complete = data.get("tokens_complete", {})
                eligible = (source is not None and bool(source.get("input_state"))
                            and source.get("input_hash") == command.input_hash
                            and latest.contract_version == command.contract_version
                            and latest.reason_code == "WORKER_LOST" and count < 2
                            and complete.get("tokens_out") is True
                            and metrics["model_calls"] < limits["max_calls"]
                            and metrics["tokens_out"] < limits["max_output_tokens"])
                if not eligible:
                    for pending in snapshot.attempts:
                        if pending.status == "pending":
                            tx.transition_attempt(attempt_id=pending.attempt_id, expected="pending", target="failed",
                                                  generation=pending.generation, reason_code="RECOVERY_EXHAUSTED")
                    active = any(a.status in {"pending", "running"} for a in tx.snapshot.attempts)
                    if active:
                        tx.update_data(lambda data: data["saved_state"].update(
                            terminal_intent={"target": "failed", "reason_code": "RECOVERY_EXHAUSTED",
                                             "stage_id": command.stage_id}))
                    else:
                        tx.transition_run(expected="running", target="failed", reason_code="RECOVERY_EXHAUSTED")
                    return
                private = AttemptState(stage_id=command.stage_id, attempt_no=command.attempt_no + 1,
                                       input_hash=command.input_hash, input_state=deepcopy(source["input_state"]))
                successor_id = private.attempt_id
                def update(data):
                    data["attempts"].append(private.model_dump(mode="json"))
                    data["saved_state"].setdefault("auto_retry_counts", {})[command.stage_id] = count + 1
                    data["run"]["metrics"]["attempts"] += 1
                tx.update_data(update)
                tx.register_attempt(stage_id=command.stage_id, contract_version=command.contract_version,
                                    input_hash=command.input_hash, attempt_id=successor_id,
                                    reason_code="WORKER_LOST")
                tx.publish(event_type="retry.scheduled", stage_id=command.stage_id,
                           data={"mode": "automatic", "previous_attempt_no": command.attempt_no,
                                 "next_attempt_no": command.attempt_no + 1, "reason_code": "WORKER_LOST"})
                tx.publish(event_type="metrics.updated", data={"metrics": tx.snapshot.data["run"]["metrics"],
                           "tokens_complete": tx.snapshot.data["tokens_complete"]})
            if successor_id is not None:
                self.queue.enqueue(session, successor_id)
