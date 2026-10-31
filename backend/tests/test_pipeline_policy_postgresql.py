from copy import deepcopy
from uuid import UUID
from sqlalchemy import select
from fastapi.testclient import TestClient

from test_runtime_recovery_postgresql import db
from test_pipeline_execution import setup, execute_next
from app.models import Run, StageAttempt
from app.services.artifacts.models import ExecutionReceipt, ModelCall


def test_unconfigured_web_run_freezes_derived_policy_and_replays(db):
    app, factory, calls, requirements = setup(db, waiting=False, profile='web-fastapi-angular', configured=False)
    with TestClient(app) as client:
        project = client.post('/api/v1/projects', json={'name': 'Automatic policy', 'requirements_text': requirements}).json()
        response = client.post(f'/api/v1/projects/{project["id"]}/runs', headers={'Idempotency-Key': 'auto-policy'},
            json={'requirements_text': requirements, 'profile_id': 'web-fastapi-angular'})
        assert response.status_code == 202
        run_id = UUID(response.json()['run_id'])
        worker = factory()
        worker.stage_worker.orchestrator.runtime.intake.dispatch()
        first = execute_next(db, worker)
        assert [name for name, _ in calls] == ['analysis', 'policies']
        with db() as session:
            analysis = session.scalar(select(StageAttempt).where(StageAttempt.run_id == run_id, StageAttempt.stage_id == 'analysis'))
            receipt = session.get(ExecutionReceipt, (analysis.id, 'architecture-policy'))
            assert receipt.payload['schema_version'] == 'source-bound-policy/1'
            frozen = deepcopy(receipt.payload)
        # A fresh worker uses the recorded policy and accepted requirements.
        second = execute_next(db, factory())
        with db() as session:
            stages = list(session.scalars(select(StageAttempt).where(StageAttempt.run_id == run_id)))
            assert all(s.status == 'succeeded' for s in stages if s.stage_id in {'analysis', 'architecture'})
            assert any(s.stage_id == 'frontend-template' and s.status == 'pending' for s in stages)
            assert session.get(ExecutionReceipt, (analysis.id, 'architecture-policy')).payload == frozen
            assert len(list(session.scalars(select(ModelCall).where(ModelCall.run_id == run_id)))) == 6
        before = len(calls)
        for command in (first, second):
            assert factory().tasks['rwb.stage.execute.v1'].apply(args=[str(command)]).get() == 'already_handled'
        assert len(calls) == before
