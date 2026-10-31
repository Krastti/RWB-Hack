"""Disposable PostgreSQL integration checks for runtime result persistence."""
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from app.models import User, Project, RequirementRevision, Run, StageAttempt, RunEvent
from app.models.run_creation import RunInputSnapshot
from app.services.lifecycle.models import LifecycleError
from app.services.run_control.models import AttemptState, ControlError, Limits
from app.services.runtime import compose_runtime
from app.services.runtime.models import RunRuntimeState


@pytest.fixture
def db():
    url = os.environ.get("RWB_TEST_DATABASE_URL")
    if not url:
        pytest.skip("requires disposable PostgreSQL")
    admin = create_engine(url)
    schema = "review140_" + uuid4().hex
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema},public"})
    with engine.begin() as conn:
        for path in sorted((Path(__file__).resolve().parents[1] / "migrations").glob("V*__*.sql")):
            conn.exec_driver_sql(path.read_text())
    yield sessionmaker(engine)
    engine.dispose()
    with admin.begin() as conn:
        conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    admin.dispose()


from datetime import datetime, timedelta, timezone
from app.adapters.queue.models import WorkerCommand
from app.workers.execution import LeasedExecution
from app.services.runtime.recovery import RuntimeRecovery

@pytest.mark.parametrize("count, expected", [(0, "running"), (2, "failed")])
def test_lost_attempt_recovery(db, count, expected):
    owner, project, revision, run_id, attempt_id = [uuid4() for _ in range(5)]
    text_input = "requirements"
    source = AttemptState(attempt_id=attempt_id, stage_id="analysis", attempt_no=1,
                          input_hash="a" * 64, input_state={"input": "frozen"})
    with db.begin() as s:
        s.add(User(id=owner, login="user", password_hash="hash")); s.flush()
        s.add(Project(id=project, owner_id=owner, name="project")); s.flush()
        s.add(RequirementRevision(id=revision, project_id=project, number=1,
                                  requirements_text=text_input, hash="a" * 64)); s.flush()
        s.add(Run(id=run_id, project_id=project, revision_id=revision, profile_id="web-fastapi-angular",
                  status="running", limits={"max_calls": 10, "max_output_tokens": 1000})); s.flush()
        s.add(StageAttempt(id=attempt_id, run_id=run_id, stage_id="analysis", attempt_no=1,
                           contract_version="1.0", input_hash="a" * 64, status="running"))
        s.add(RunInputSnapshot(run_id=run_id, payload={"revision_id": str(revision),
              "profile": {"profile_id": "web-fastapi-angular"}, "requirements_text": text_input,
              "requirements_hash": sha256(text_input.encode()).hexdigest()}))
        s.add(RunRuntimeState(run_id=run_id, version=1, generations={}, payload={
            "attempts": [source.model_dump(mode="json")], "saved_state": {"auto_retry_counts": {"analysis": count}}}))
    command_id = uuid4()
    with db.begin() as s:
        s.add(WorkerCommand(id=command_id, attempt_id=attempt_id, state="running", generation=1,
                            owner_token=uuid4(), expires_at=datetime.now(timezone.utc)-timedelta(seconds=30)))
    runtime = compose_runtime(db, retry_limits=Limits(max_calls=10, max_output_tokens=1000))
    lease = runtime.queue.claim(command_id)
    assert lease.recovery
    RuntimeRecovery(runtime.states, runtime.queue)(lease.command, LeasedExecution(runtime.queue, lease))
    if count == 0:
        with db.begin() as s:
            s.get(WorkerCommand, command_id).expires_at = datetime.now(timezone.utc)-timedelta(seconds=1)
        lease = runtime.queue.claim(command_id)
        RuntimeRecovery(runtime.states, runtime.queue)(lease.command, LeasedExecution(runtime.queue, lease))
    runtime.queue.complete(lease)
    with db() as s:
        assert s.get(Run, run_id).status == expected
        stages = list(s.scalars(select(StageAttempt).where(StageAttempt.run_id == run_id).order_by(StageAttempt.attempt_no)))
        assert stages[0].status == "failed"
        if count == 0:
            assert len(stages) == 2 and stages[1].status == "pending"
            assert s.scalar(select(WorkerCommand).where(WorkerCommand.attempt_id == stages[1].id)) is not None
        else:
            assert len(stages) == 1


def prepare_recovery(db, count=0):
    owner, project, revision, run_id, attempt_id = [uuid4() for _ in range(5)]
    text_input = "requirements"
    source = AttemptState(attempt_id=attempt_id, stage_id="analysis", attempt_no=1,
                          input_hash="a" * 64, input_state={"input": "frozen"})
    with db.begin() as s:
        s.add(User(id=owner, login="user", password_hash="hash")); s.flush()
        s.add(Project(id=project, owner_id=owner, name="project")); s.flush()
        s.add(RequirementRevision(id=revision, project_id=project, number=1,
                                  requirements_text=text_input, hash="a" * 64)); s.flush()
        s.add(Run(id=run_id, project_id=project, revision_id=revision, profile_id="web-fastapi-angular",
                  status="running", limits={"max_calls": 10, "max_output_tokens": 1000})); s.flush()
        s.add(StageAttempt(id=attempt_id, run_id=run_id, stage_id="analysis", attempt_no=1,
                           contract_version="1.0", input_hash="a" * 64, status="running"))
        s.add(RunInputSnapshot(run_id=run_id, payload={"revision_id": str(revision),
              "profile": {"profile_id": "web-fastapi-angular"}, "requirements_text": text_input,
              "requirements_hash": sha256(text_input.encode()).hexdigest()}))
        s.add(RunRuntimeState(run_id=run_id, version=1, generations={}, payload={
            "attempts": [source.model_dump(mode="json")], "saved_state": {"auto_retry_counts": {"analysis": count}}}))
    command_id = uuid4()
    with db.begin() as s:
        s.add(WorkerCommand(id=command_id, attempt_id=attempt_id, state="running", generation=1,
                            owner_token=uuid4(), expires_at=datetime.now(timezone.utc)-timedelta(seconds=30)))
    runtime = compose_runtime(db, retry_limits=Limits(max_calls=10, max_output_tokens=1000))
    lease = runtime.queue.claim(command_id)
    assert lease.recovery
    return runtime, lease, run_id, attempt_id, command_id


def test_recovery_confirmation_requires_successor_command(db):
    runtime, lease, run_id, attempt_id, command_id = prepare_recovery(db)
    with pytest.raises(RuntimeError, match="successor command"):
        with LeasedExecution(runtime.queue, lease).transaction(recovery_complete=True):
            pass
    with pytest.raises(RuntimeError, match="acknowledge"):
        runtime.queue.complete(lease)
    with db() as s:
        assert s.get(WorkerCommand, command_id).state == "recovering"
        assert s.get(StageAttempt, attempt_id).status == "failed"
        assert s.get(Run, run_id).status == "running"
        events = list(s.scalars(select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.seq)))
        assert [e.seq for e in events] == list(range(1, len(events) + 1))
        assert not any(e.event_type == "retry.scheduled" for e in events)


def test_enqueue_failure_rolls_back_recovery_and_can_retry(db, monkeypatch):
    runtime, lease, run_id, attempt_id, command_id = prepare_recovery(db)
    with db() as s:
        before = s.get(Run, run_id).metrics["attempts"]
        state_before = s.get(RunRuntimeState, run_id).payload
        seq_before = [(e.seq, e.event_type) for e in s.scalars(
            select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.seq))]
    enqueue = runtime.queue.enqueue
    def fail_after_enqueue(session, successor_id):
        enqueue(session, successor_id)
        raise RuntimeError("injected enqueue failure")
    monkeypatch.setattr(runtime.queue, "enqueue", fail_after_enqueue)
    handler = RuntimeRecovery(runtime.states, runtime.queue)
    with pytest.raises(RuntimeError, match="injected enqueue failure"):
        handler(lease.command, LeasedExecution(runtime.queue, lease))
    with db() as s:
        assert s.get(Run, run_id).status == "running"
        assert s.get(Run, run_id).metrics["attempts"] == before
        assert s.get(StageAttempt, attempt_id).status == "failed"
        assert s.get(RunRuntimeState, run_id).payload == state_before
        assert len(list(s.scalars(select(StageAttempt).where(StageAttempt.run_id == run_id)))) == 1
        assert len(list(s.scalars(select(WorkerCommand)))) == 1
        assert s.get(WorkerCommand, command_id).state == "recovering"
        events = [(e.seq, e.event_type) for e in s.scalars(
            select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.seq))]
        assert events == seq_before
        assert not any(kind == "retry.scheduled" for _, kind in events)
    monkeypatch.setattr(runtime.queue, "enqueue", enqueue)
    handler(lease.command, LeasedExecution(runtime.queue, lease))
    runtime.queue.complete(lease)
    with db() as s:
        assert s.get(WorkerCommand, command_id).state == "completed"
        assert len(list(s.scalars(select(StageAttempt).where(StageAttempt.run_id == run_id)))) == 2


def test_recovery_failure_waits_for_other_active_attempt(db):
    runtime, lease, run_id, attempt_id, command_id = prepare_recovery(db, count=2)
    other_id = uuid4()
    source = AttemptState(attempt_id=other_id, stage_id="design", attempt_no=1,
                          input_hash="b" * 64, input_state={"input": "design"})
    with db.begin() as s:
        s.add(StageAttempt(id=other_id, run_id=run_id, stage_id="design", attempt_no=1,
                           contract_version="1.0", input_hash="b" * 64, status="running"))
        state = s.get(RunRuntimeState, run_id)
        payload = dict(state.payload)
        payload["attempts"] = [*payload["attempts"], source.model_dump(mode="json")]
        state.payload = payload
    RuntimeRecovery(runtime.states, runtime.queue)(lease.command, LeasedExecution(runtime.queue, lease))
    with db() as s:
        assert s.get(Run, run_id).status == "running"
        assert s.get(StageAttempt, other_id).status == "running"
        assert s.get(RunRuntimeState, run_id).payload["saved_state"]["terminal_intent"]


def _check_terminal_finalization(db, restart=False):
    runtime, lease, run_id, _, _ = prepare_recovery(db, count=2)
    other_id = uuid4()
    source = AttemptState(attempt_id=other_id, stage_id="design", attempt_no=1,
                          input_hash="b" * 64, input_state={"input": "design"})
    with db.begin() as s:
        s.add(StageAttempt(id=other_id, run_id=run_id, stage_id="design", attempt_no=1,
                           contract_version="1.0", input_hash="b" * 64, status="running"))
        state = s.get(RunRuntimeState, run_id)
        payload = dict(state.payload)
        payload["attempts"] = [*payload["attempts"], source.model_dump(mode="json")]
        state.payload = payload
    RuntimeRecovery(runtime.states, runtime.queue)(lease.command, LeasedExecution(runtime.queue, lease))
    if restart:
        del runtime
        runtime = compose_runtime(db, retry_limits=Limits(max_calls=10, max_output_tokens=1000))
    runtime.repository.guard = runtime.states.guard = lambda *_: None
    with db.begin() as s:
        row = s.scalar(select(Run).where(Run.id == run_id).with_for_update())
        owner = s.get(Project, row.project_id).owner_id
        runtime.repository.control_in(s, row).finish_attempt(
            owner_id=owner, run_id=run_id, stage_id="design", attempt_id=other_id,
            accepted_result={"status": "succeeded", "run_id": str(run_id), "stage_id": "design",
                             "attempt": 1, "contract_version": "1.0"},
            save_boundary=lambda record: {**record.saved_state, "artifact": "accepted"})
    with db() as s:
        assert s.get(Run, run_id).status == "failed"
        assert s.get(StageAttempt, other_id).status == "succeeded"
        state = s.get(RunRuntimeState, run_id).payload
        assert state["saved_state"]["artifact"] == "accepted"
        events = list(s.scalars(select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.seq)))
        assert [e.seq for e in events] == list(range(1, len(events) + 1))
        assert sum(e.event_type == "run.status_changed" and e.data.get("to") == "failed" for e in events) == 1


def test_terminal_barrier_finalizes_after_last_attempt(db):
    _check_terminal_finalization(db)


def test_terminal_barrier_survives_worker_restart(db):
    _check_terminal_finalization(db, restart=True)


def test_terminal_barrier_resolves_pending_attempts(db):
    runtime, lease, run_id, _, _ = prepare_recovery(db, count=2)
    pending_id = uuid4()
    source = AttemptState(attempt_id=pending_id, stage_id="design", attempt_no=1,
                          input_hash="b" * 64, input_state={"input": "design"})
    with db.begin() as s:
        s.add(StageAttempt(id=pending_id, run_id=run_id, stage_id="design", attempt_no=1,
                           contract_version="1.0", input_hash="b" * 64, status="pending"))
        state = s.get(RunRuntimeState, run_id)
        payload = dict(state.payload)
        payload["attempts"] = [*payload["attempts"], source.model_dump(mode="json")]
        state.payload = payload
        s.flush()
        pending_command = runtime.queue.enqueue(s, pending_id)
    RuntimeRecovery(runtime.states, runtime.queue)(lease.command, LeasedExecution(runtime.queue, lease))
    with db() as s:
        assert s.get(Run, run_id).status == "failed"
        assert s.get(StageAttempt, pending_id).status == "failed"
        assert s.get(StageAttempt, pending_id).reason == "RECOVERY_EXHAUSTED"
    assert runtime.queue.claim(pending_command) is None


def test_terminal_intent_survives_stale_saved_state(db):
    runtime, lease, run_id, _, _ = prepare_recovery(db, count=2)
    other_id = uuid4()
    source = AttemptState(attempt_id=other_id, stage_id="design", attempt_no=1,
                          input_hash="b" * 64, input_state={"input": "design"})
    with db.begin() as s:
        s.add(StageAttempt(id=other_id, run_id=run_id, stage_id="design", attempt_no=1,
                           contract_version="1.0", input_hash="b" * 64, status="running"))
        state = s.get(RunRuntimeState, run_id)
        payload = dict(state.payload)
        stale = dict(payload["saved_state"])
        payload["attempts"] = [*payload["attempts"], source.model_dump(mode="json")]
        state.payload = payload
    RuntimeRecovery(runtime.states, runtime.queue)(lease.command, LeasedExecution(runtime.queue, lease))
    runtime.repository.guard = runtime.states.guard = lambda *_: None
    with db.begin() as s:
        row = s.scalar(select(Run).where(Run.id == run_id).with_for_update())
        owner = s.get(Project, row.project_id).owner_id
        runtime.repository.control_in(s, row).finish_attempt(
            owner_id=owner, run_id=run_id, stage_id="design", attempt_id=other_id,
            accepted_result={"status": "succeeded", "run_id": str(run_id), "stage_id": "design",
                             "attempt": 1, "contract_version": "1.0"},
            save_boundary=lambda record: {**stale, "artifact": "accepted"})
    with db() as s:
        assert s.get(Run, run_id).status == "failed"
        assert s.get(StageAttempt, other_id).status == "succeeded"
        assert s.get(RunRuntimeState, run_id).payload["saved_state"]["artifact"] == "accepted"

    with db.begin() as s:
        row = s.scalar(select(Run).where(Run.id == run_id).with_for_update())
        with runtime.repository.bound(s, row) as record:
            assert "terminal_intent" not in record.saved_state
            receipt = dict(record.saved_state["terminal_intent_finalized"])
            record.saved_state = {"artifact": "accepted", "terminal_intent": receipt}
    with db() as s:
        saved = s.get(RunRuntimeState, run_id).payload["saved_state"]
        assert "terminal_intent" not in saved
        assert saved["terminal_intent_finalized"] == receipt


def test_terminal_barrier_concurrent_attempt_completion(db):
    from threading import local
    from sqlalchemy import event
    from app.adapters.queue.contracts import Lease

    runtime, recovery_lease, run_id, _, _ = prepare_recovery(db, count=2)
    leases = []
    with db.begin() as s:
        state = s.get(RunRuntimeState, run_id)
        payload = dict(state.payload)
        private = list(payload["attempts"])
        for stage in ("design", "testing"):
            attempt_id = uuid4()
            source = AttemptState(attempt_id=attempt_id, stage_id=stage, attempt_no=1,
                                  input_hash="b" * 64, input_state={"stage": stage})
            private.append(source.model_dump(mode="json"))
            attempt = StageAttempt(id=attempt_id, run_id=run_id, stage_id=stage, attempt_no=1,
                                   contract_version="1.0", input_hash="b" * 64, status="running")
            s.add(attempt)
            s.flush()
            command = WorkerCommand(id=uuid4(), attempt_id=attempt_id, state="running", generation=1,
                                    owner_token=uuid4(), expires_at=datetime.now(timezone.utc)+timedelta(seconds=60))
            s.add(command)
            s.flush()
            leases.append(Lease(runtime.queue._command(command, attempt), command.owner_token,
                                command.generation, command.expires_at))
        payload["attempts"] = private
        state.payload = payload
    RuntimeRecovery(runtime.states, runtime.queue)(recovery_lease.command, LeasedExecution(runtime.queue, recovery_lease))
    runtime.repository.guard = runtime.states.guard = lambda *_: None
    rendezvous = Barrier(2)
    threads = local()
    connections = set()

    def synchronize(conn, cursor, statement, parameters, context, executemany):
        if "FOR UPDATE" in statement and "FROM runs" in statement and not getattr(threads, "met", False):
            threads.met = True
            connections.add(id(conn))
            rendezvous.wait(timeout=5)

    def finish(lease):
        execution = LeasedExecution(runtime.queue, lease)
        with execution.transaction() as s:
            row = s.get(Run, run_id)
            owner = s.get(Project, row.project_id).owner_id
            def save(record):
                record.run.metrics.tokens_out += 7
                return {**record.saved_state, lease.command.stage_id: "accepted"}
            runtime.repository.control_in(s, row).finish_attempt(
                owner_id=owner, run_id=run_id, stage_id=lease.command.stage_id,
                attempt_id=lease.command.attempt_id,
                accepted_result={"status": "succeeded", "run_id": str(run_id),
                                 "stage_id": lease.command.stage_id, "attempt": 1, "contract_version": "1.0"},
                save_boundary=save)
        runtime.queue.complete(lease)

    event.listen(db.kw["bind"], "before_cursor_execute", synchronize)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(finish, lease) for lease in leases]
            for future in futures:
                future.result(timeout=15)
    finally:
        event.remove(db.kw["bind"], "before_cursor_execute", synchronize)
    assert len(connections) == 2
    with db() as s:
        assert s.get(Run, run_id).status == "failed"
        assert s.get(Run, run_id).metrics["tokens_out"] == 14
        payload = s.get(RunRuntimeState, run_id).payload
        assert payload["saved_state"]["design"] == payload["saved_state"]["testing"] == "accepted"
        assert "terminal_intent" not in payload["saved_state"]
        for lease in leases:
            assert s.get(StageAttempt, lease.command.attempt_id).status == "succeeded"
            result = next(a for a in payload["attempts"] if a["attempt_id"] == str(lease.command.attempt_id))
            assert result["accepted_result"]["status"] == "succeeded"
        events = list(s.scalars(select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.seq)))
        assert [e.seq for e in events] == list(range(1, len(events) + 1))
        assert sum(e.event_type == "run.status_changed" and e.data.get("to") == "failed" for e in events) == 1
