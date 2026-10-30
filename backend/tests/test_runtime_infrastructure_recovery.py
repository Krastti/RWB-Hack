from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from test_runtime_recovery_postgresql import db, prepare_recovery
from app.adapters.queue.contracts import LeaseBusy, LeaseLost
from app.adapters.queue.models import WorkerCommand
from app.models import Run, StageAttempt
from app.services.run_control.models import AttemptState, Limits
from app.services.runtime import compose_runtime
from app.services.runtime.models import RunRuntimeState
from app.services.runtime.recovery import RuntimeRecovery
from app.workers.execution import LeasedExecution
from uuid import uuid4


def due(db, command_id):
    with db.begin() as s:
        s.get(WorkerCommand, command_id).next_delivery_at = datetime.now(timezone.utc)-timedelta(seconds=1)


def test_recovery_failures_are_persisted_and_bounded(db):
    runtime, lease, run_id, _, command_id = prepare_recovery(db)
    for failures in range(1, 4):
        runtime.queue.abandon(lease)
        with pytest.raises(LeaseLost):
            runtime.queue.abandon(lease)
        runtime = compose_runtime(db, retry_limits=Limits(max_calls=10, max_output_tokens=1000))
        with db() as s:
            command = s.get(WorkerCommand, command_id)
            assert command.recovery_failures == failures
            assert command.recovery_error == "RECOVERY_HANDLER_FAILED"
            assert s.get(RunRuntimeState, run_id).payload["saved_state"]["auto_retry_counts"]["analysis"] == 0
        if failures < 3:
            due(db, command_id)
            lease = runtime.queue.claim(command_id)
    assert runtime.queue.claim(command_id) is None
    assert command_id not in runtime.queue.reserve_due()
    with db() as s:
        assert s.get(Run, run_id).status == "failed"
        assert s.get(WorkerCommand, command_id).state == "obsolete"


def test_recovery_backoff_prevents_early_redelivery(db):
    runtime, lease, _, _, command_id = prepare_recovery(db)
    runtime.queue.abandon(lease)
    runtime = compose_runtime(db, retry_limits=Limits(max_calls=10, max_output_tokens=1000))
    with pytest.raises(LeaseBusy):
        runtime.queue.claim(command_id)
    assert command_id not in runtime.queue.reserve_due()
    due(db, command_id)
    assert command_id in runtime.queue.reserve_due()
    assert runtime.queue.claim(command_id).recovery


def test_recovery_exhaustion_respects_terminal_barrier(db):
    runtime, lease, run_id, _, command_id = prepare_recovery(db)
    other = AttemptState(stage_id="design", attempt_no=1, input_hash="b" * 64, input_state={"design": True})
    with db.begin() as s:
        s.add(StageAttempt(id=other.attempt_id, run_id=run_id, stage_id="design", attempt_no=1,
                           input_hash=other.input_hash, contract_version="1.0", status="running"))
        state = s.get(RunRuntimeState, run_id)
        state.payload = {**state.payload, "attempts": [*state.payload["attempts"], other.model_dump(mode="json")]}
    for failures in range(3):
        runtime.queue.abandon(lease)
        if failures < 2:
            due(db, command_id)
            lease = runtime.queue.claim(command_id)
    with db() as s:
        assert s.get(Run, run_id).status == "running"
        assert s.get(RunRuntimeState, run_id).payload["saved_state"]["terminal_intent"]["reason_code"] == "RECOVERY_INFRA_EXHAUSTED"
    with db.begin() as s:
        row = s.scalar(select(Run).where(Run.id == run_id).with_for_update())
        with runtime.states.transaction(s, row) as tx:
            tx.transition_attempt(attempt_id=other.attempt_id, expected="running", target="failed", generation=0, reason_code="TEST_FAILED")
    with db() as s:
        assert s.get(Run, run_id).status == "failed"


def test_successful_recovery_does_not_consume_domain_retry_on_redelivery(db):
    runtime, lease, run_id, _, command_id = prepare_recovery(db)
    RuntimeRecovery(runtime.states, runtime.queue)(lease.command, LeasedExecution(runtime.queue, lease))
    with db.begin() as s:
        s.get(WorkerCommand, command_id).expires_at = datetime.now(timezone.utc)-timedelta(seconds=1)
    runtime = compose_runtime(db, retry_limits=Limits(max_calls=10, max_output_tokens=1000))
    lease = runtime.queue.claim(command_id)
    RuntimeRecovery(runtime.states, runtime.queue)(lease.command, LeasedExecution(runtime.queue, lease))
    runtime.queue.complete(lease)
    with db() as s:
        assert s.get(WorkerCommand, command_id).recovery_failures == 0
        assert s.get(RunRuntimeState, run_id).payload["saved_state"]["auto_retry_counts"]["analysis"] == 1
        assert len(list(s.scalars(select(StageAttempt).where(StageAttempt.run_id == run_id)))) == 2


def test_recovery_cancellation_waits_for_safe_boundary_and_fences_owner(db):
    from app.models import Project, RunEvent
    runtime, lease, run_id, _, command_id = prepare_recovery(db)
    other = AttemptState(stage_id="design", attempt_no=1, input_hash="b" * 64, input_state={"design": True})
    with db.begin() as s:
        s.add(StageAttempt(id=other.attempt_id, run_id=run_id, stage_id="design", attempt_no=1,
                           input_hash=other.input_hash, contract_version="1.0", status="running"))
        state = s.get(RunRuntimeState, run_id)
        state.payload = {**state.payload, "attempts": [*state.payload["attempts"], other.model_dump(mode="json")]}
        row = s.get(Run, run_id)
        owner = s.get(Project, row.project_id).owner_id
    runtime.control.cancel(owner_id=owner, run_id=run_id)
    handler = RuntimeRecovery(runtime.states, runtime.queue)
    handler(lease.command, LeasedExecution(runtime.queue, lease))
    runtime.queue.complete(lease)
    with pytest.raises(LeaseLost):
        with LeasedExecution(runtime.queue, lease).transaction():
            pytest.fail("stale owner entered execution transaction")
    with db() as s:
        assert s.get(Run, run_id).status == "cancelling"
        assert len(list(s.scalars(select(StageAttempt).where(StageAttempt.run_id == run_id)))) == 2
    with db.begin() as s:
        row = s.scalar(select(Run).where(Run.id == run_id).with_for_update())
        runtime.repository.control_in(s, row).finish_attempt(
            owner_id=owner, run_id=run_id, stage_id="design", attempt_id=other.attempt_id,
            accepted_result={"status": "cancelled", "run_id": str(run_id), "stage_id": "design",
                             "attempt": 1, "contract_version": "1.0"},
            save_boundary=lambda record: {**record.saved_state, "cancelled_design": True})
    assert runtime.queue.claim(command_id) is None
    with db() as s:
        assert s.get(Run, run_id).status == "cancelled"
        assert s.get(Run, run_id).metrics["model_calls"] == 0
        events = list(s.scalars(select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.seq)))
        assert [e.seq for e in events] == list(range(1, len(events)+1))
        assert sum(e.event_type == "run.status_changed" and e.data.get("to") == "cancelled" for e in events) == 1
        assert not any(e.event_type == "retry.scheduled" or e.data.get("to") == "failed" for e in events if e.event_type != "stage.status_changed")


def test_expired_recovery_lease_is_fenced_and_technical_retries_exhaust(db):
    runtime, lease, run_id, _, command_id = prepare_recovery(db)
    for expected in range(1, 4):
        with db.begin() as s:
            s.get(WorkerCommand, command_id).expires_at = datetime.now(timezone.utc)-timedelta(seconds=1)
        with pytest.raises(LeaseLost):
            with LeasedExecution(runtime.queue, lease).transaction(recovery_complete=True):
                pytest.fail("expired recovery lease was accepted")
        assert runtime.queue.claim(command_id) is None
        with db() as s:
            assert s.get(WorkerCommand, command_id).recovery_failures == expected
            assert s.get(RunRuntimeState, run_id).payload["saved_state"]["auto_retry_counts"]["analysis"] == 0
        runtime = compose_runtime(db, retry_limits=Limits(max_calls=10, max_output_tokens=1000))
        if expected < 3:
            with pytest.raises(LeaseBusy):
                runtime.queue.claim(command_id)
            assert command_id not in runtime.queue.reserve_due()
            due(db, command_id)
            lease = runtime.queue.claim(command_id)
    assert runtime.queue.claim(command_id) is None
    with db() as s:
        assert s.get(Run, run_id).status == "failed"
        assert s.get(WorkerCommand, command_id).state == "obsolete"
        assert len(list(s.scalars(select(StageAttempt).where(StageAttempt.run_id == run_id)))) == 1


def test_recovery_accepts_already_completed_cancellation(db):
    from app.models import Project
    runtime, lease, run_id, _, _ = prepare_recovery(db)
    with db() as s:
        owner = s.get(Project, s.get(Run, run_id).project_id).owner_id
    runtime.control.cancel(owner_id=owner, run_id=run_id)
    RuntimeRecovery(runtime.states, runtime.queue)(lease.command, LeasedExecution(runtime.queue, lease))
    runtime.queue.complete(lease)
    with db() as s:
        assert s.get(Run, run_id).status == "cancelled"
        assert len(list(s.scalars(select(StageAttempt).where(StageAttempt.run_id == run_id)))) == 1
