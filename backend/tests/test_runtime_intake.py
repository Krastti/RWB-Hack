from datetime import datetime, timedelta, timezone
from hashlib import sha256
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import select

from test_runtime_recovery_postgresql import db
from app.models import User, Project, RequirementRevision, Run, StageAttempt, RunEvent
from app.models.run_creation import RunInputSnapshot, RunQueueJob
from app.adapters.queue.models import WorkerCommand
from app.services.runtime import compose_runtime
from app.services.run_control.models import Limits


def seed(db, poison=False):
    owner, project, revision, run_id, job_id = [uuid4() for _ in range(5)]
    with db.begin() as s:
        s.add(User(id=owner, login=owner.hex, password_hash="hash")); s.flush()
        s.add(Project(id=project, owner_id=owner, name="intake")); s.flush()
        s.add(RequirementRevision(id=revision, project_id=project, number=1, requirements_text="text", hash=sha256(b'text').hexdigest())); s.flush()
        s.add(Run(id=run_id, project_id=project, revision_id=revision, profile_id="web-fastapi-angular", status="queued", limits={"max_calls":10,"max_output_tokens":1000})); s.flush()
        if not poison:
            s.add(RunInputSnapshot(run_id=run_id, payload={"revision_id":str(revision), "profile":{"profile_id":"web-fastapi-angular"}, "requirements_text":"text", "requirements_hash":sha256(b'text').hexdigest(), "contracts":{"agents":"1.0"}}))
        s.add(RunQueueJob(id=job_id, run_id=run_id))
    return run_id, job_id


def runtime(db):
    return compose_runtime(db, retry_limits=Limits(max_calls=10, max_output_tokens=1000))


def test_poison_before_valid_limit_one_survives_restart(db):
    poison, job_id = seed(db, poison=True)
    valid, _ = seed(db)
    assert runtime(db).intake.dispatch(limit=1) == {"accepted":0,"failed":1}
    with db() as s:
        job = s.get(RunQueueJob, job_id)
        assert job.state == "rejected" and job.failures == 1
        assert "RESUME_STATE_UNAVAILABLE" in job.failure_code
    assert runtime(db).intake.dispatch(limit=1) == {"accepted":1,"failed":0}
    assert runtime(db).intake.dispatch(limit=1) == {"accepted":0,"failed":0}
    with db() as s:
        assert len(list(s.scalars(select(StageAttempt).where(StageAttempt.run_id==valid)))) == 1
        assert len(list(s.scalars(select(WorkerCommand)))) == 1
        assert not list(s.scalars(select(StageAttempt).where(StageAttempt.run_id==poison)))


def test_transient_rollback_backoff_exhaustion_and_restart(db, monkeypatch):
    run_id, job_id = seed(db)
    rt = runtime(db)
    enqueue = rt.queue.enqueue
    def broken(s, attempt_id):
        enqueue(s, attempt_id)
        raise RuntimeError("injected failure with secret text")
    for failures in range(1,4):
        monkeypatch.setattr(rt.queue, 'enqueue', broken)
        assert rt.intake.dispatch() == {"accepted":0,"failed":1}
        with db() as s:
            job = s.get(RunQueueJob, job_id)
            assert job.failures == failures
            assert "secret" not in job.failure_code
            assert not list(s.scalars(select(StageAttempt)))
            assert not list(s.scalars(select(WorkerCommand)))
            assert not any(e.event_type == "retry.scheduled" for e in s.scalars(select(RunEvent)))
        assert runtime(db).intake.dispatch() == {"accepted":0,"failed":0}
        if failures < 3:
            with db.begin() as s:
                s.get(RunQueueJob,job_id).next_attempt_at=datetime.now(timezone.utc)-timedelta(seconds=1)
            rt=runtime(db)
    with db() as s:
        assert s.get(RunQueueJob,job_id).state == "rejected"
        assert s.get(Run,run_id).status == "failed"


def test_concurrent_dispatch_and_idempotent_replay(db):
    run_id, job_id = seed(db)
    barrier=Barrier(2)
    def dispatch(_):
        barrier.wait(timeout=5)
        return runtime(db).intake.dispatch(limit=1)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(dispatch,range(2)))
    assert sum(r['accepted'] for r in results)==1
    assert runtime(db).intake.dispatch()=={"accepted":0,"failed":0}
    with db() as s:
        assert s.get(RunQueueJob,job_id) is None
        assert len(list(s.scalars(select(StageAttempt))))==1
        assert len(list(s.scalars(select(WorkerCommand))))==1


def test_permanent_validation_failure_terminalizes_once(db, monkeypatch):
    from app.services.run_control.models import ControlError
    run_id, job_id = seed(db)
    rt = runtime(db)
    def invalid(*args):
        raise ControlError(422,"VALIDATION_ERROR","invalid frozen input")
    monkeypatch.setattr(rt.intake,'_register',invalid)
    assert rt.intake.dispatch()=={"accepted":0,"failed":1}
    with db() as s:
        assert s.get(Run,run_id).status=="failed"
        assert s.get(RunQueueJob,job_id).state=="rejected"
        events=list(s.scalars(select(RunEvent).order_by(RunEvent.seq)))
        assert [e.seq for e in events]==list(range(1,len(events)+1))
        assert [e.data['to'] for e in events if e.event_type=='run.status_changed']==['running','failed']
    assert runtime(db).intake.dispatch()=={"accepted":0,"failed":0}
    with db() as s:
        assert len(list(s.scalars(select(RunEvent))))==len(events)
