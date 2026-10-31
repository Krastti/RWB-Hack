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
        for path in sorted((Path(__file__).resolve().parents[1] / "migrations").glob("V*__*.sql"), key=lambda p: int(p.name.split("__")[0][1:])):
            conn.exec_driver_sql(path.read_text())
    yield sessionmaker(engine)
    engine.dispose()
    with admin.begin() as conn:
        conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    admin.dispose()


@pytest.mark.parametrize("target", ["succeeded", "skipped", "waiting_for_input"])
def test_guard_rejection_rolls_back_result_and_events(db, target):
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
            "attempts": [source.model_dump(mode="json")], "saved_state": {}}))
    runtime = compose_runtime(db, retry_limits=Limits(max_calls=10, max_output_tokens=1000))
    result = {"status": target, "run_id": str(run_id), "stage_id": "analysis", "attempt": 1,
              "contract_version": "1.0"}
    def boundary(record):
        record.run.stages[0].reason = "ACCEPTED_RESULT"
        return {"checkpoint": "saved"}

    def finish():
        with db.begin() as s:
            row = s.scalar(select(Run).where(Run.id == run_id).with_for_update())
            runtime.repository.control_in(s, row).finish_attempt(owner_id=owner, run_id=run_id,
                stage_id="analysis", attempt_id=attempt_id, accepted_result=result,
                save_boundary=boundary)
    with pytest.raises(LifecycleError):
        finish()
    with db() as s:
        assert s.get(Run, run_id).status == "running"
        assert s.get(StageAttempt, attempt_id).status == "running"
        assert list(s.scalars(select(RunEvent).where(RunEvent.run_id == run_id))) == []
    runtime.repository.guard = lambda state, change: None
    barrier = Barrier(2)
    def concurrent_finish():
        barrier.wait(timeout=5)
        try:
            finish()
            return "saved"
        except ControlError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: concurrent_finish(), range(2)))
    assert sorted(results) == ["RUN_STATE_CONFLICT", "saved"]
    with db() as s:
        assert s.get(StageAttempt, attempt_id).status == target
        assert len(list(s.scalars(select(RunEvent).where(RunEvent.run_id == run_id)))) == 1
    with pytest.raises(ControlError) as exc:
        finish()
    assert exc.value.code == "RUN_STATE_CONFLICT"
