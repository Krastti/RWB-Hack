"""Regression checks from review #152, using PostgreSQL and controlled transport."""
from copy import deepcopy
from uuid import UUID
from sqlalchemy import select, text
from fastapi.testclient import TestClient
import pytest

import test_pipeline_execution as existing
from test_runtime_recovery_postgresql import db
from app.models import Run, StageAttempt
from app.services.artifacts.models import ModelCall


def create(client, requirements, key):
    project = client.post('/api/v1/projects', json={'name': key, 'requirements_text': requirements}).json()
    response = client.post(f'/api/v1/projects/{project["id"]}/runs',
        headers={'Idempotency-Key': key}, json={'requirements_text': requirements})
    assert response.status_code == 202, response.text
    return response.json()['run_id']


def test_cancel_blocks_analyst_repair_call(db, monkeypatch):
    app, factory, calls, requirements = existing.setup(db, waiting=False,
        profile='web-fastapi-angular', invalid_first=True)
    from app.agents.llm import LLMAdapter
    generate = LLMAdapter._generate
    with TestClient(app) as client:
        run_id = create(client, requirements, 'cancel-between-calls')
        async def cancelled_during_response(self, **kwargs):
            result = await generate(self, **kwargs)
            if len(calls) == 1:
                response = client.post(f'/api/v1/runs/{run_id}/cancel')
                assert response.status_code == 200, response.text
            return result
        monkeypatch.setattr(LLMAdapter, '_generate', cancelled_during_response)
        worker = factory()
        worker.stage_worker.orchestrator.runtime.intake.dispatch()
        existing.execute_next(db, worker)
        assert len(calls) == 1, f'Provider received {len(calls)} calls despite persisted cancellation'


@pytest.mark.parametrize('reason,env_name,value,new_limits,insufficient', [
    ('MAX_CALLS', 'RUN_MAX_CALLS', '4', {'max_calls': 8, 'max_output_tokens': 180000},
     {'max_calls': 5, 'max_output_tokens': 180000}),
    ('MAX_CALLS', 'RUN_MAX_CALLS', '1', {'max_calls': 5, 'max_output_tokens': 180000},
     {'max_calls': 2, 'max_output_tokens': 180000}),
    ('MAX_OUTPUT_TOKENS', 'RUN_MAX_OUTPUT_TOKENS', '1000', {'max_calls': 60, 'max_output_tokens': 40000},
     {'max_calls': 60, 'max_output_tokens': 2000}),
])
def test_budget_retry_can_increase_limit(db, monkeypatch, reason, env_name, value, new_limits, insufficient):
    from app.services.run_control.models import Limits
    real_factory = existing.create_app
    monkeypatch.setattr(existing, 'create_app', lambda **kwargs: real_factory(
        **kwargs, retry_limits=Limits(max_calls=60, max_output_tokens=180000)))
    monkeypatch.setenv(env_name, value)
    app, factory, calls, requirements = existing.setup(db, waiting=False, profile='web-fastapi-angular')
    monkeypatch.delenv(env_name)
    with TestClient(app) as client:
        run_id = create(client, requirements, 'calls-budget')
        worker = factory()
        worker.stage_worker.orchestrator.runtime.intake.dispatch()
        existing.execute_next(db, worker)
        existing.execute_next(db, worker)
        stopped = client.get(f'/api/v1/runs/{run_id}').json()
        assert stopped['status'] == 'budget_exceeded', stopped
        assert stopped['stop_reason'] == reason, stopped
        denied = client.post(f'/api/v1/runs/{run_id}/retry', headers={'Idempotency-Key': 'too-small'},
            json={'limits': insufficient})
        assert denied.status_code == 409
        assert client.get(f'/api/v1/runs/{run_id}').json() == stopped
        response = client.post(f'/api/v1/runs/{run_id}/retry', headers={'Idempotency-Key': 'increase'},
            json={'limits': new_limits})
        assert response.status_code == 202, response.text
        repeat = client.post(f'/api/v1/runs/{run_id}/retry', headers={'Idempotency-Key': 'increase'},
            json={'limits': new_limits})
        assert repeat.json() == response.json()
        last = existing.execute_next(db, worker)
        with db() as session:
            stages = list(session.scalars(select(StageAttempt).where(StageAttempt.run_id == UUID(run_id))))
            assert sorted((a.stage_id, a.attempt_no, a.status) for a in stages) == [
                ('analysis', 1, 'succeeded'), ('architecture', 1, 'failed'), ('architecture', 2, 'succeeded'),
                ('frontend-template', 1, 'pending')]
            assert session.get(Run, UUID(run_id)).limits == new_limits
        count = len(calls)
        assert worker.tasks['rwb.stage.execute.v1'].apply(args=[str(last)]).get() == 'already_handled'
        assert len(calls) == count


def test_retry_can_recover_failure_before_initial_graph_checkpoint(db, monkeypatch):
    from app.services.artifacts import PostgreSQLArtifactStore
    app, factory, calls, requirements = existing.setup(db, waiting=False, profile='web-fastapi-angular')
    put = PostgreSQLArtifactStore.put
    failed = []
    def fail_first_put(self, *args, **kwargs):
        if not failed:
            failed.append(True)
            raise OSError('test failure before initial checkpoint')
        return put(self, *args, **kwargs)
    monkeypatch.setattr(PostgreSQLArtifactStore, 'put', fail_first_put)
    with TestClient(app) as client:
        run_id = create(client, requirements, 'pre-checkpoint')
        worker = factory()
        worker.stage_worker.orchestrator.runtime.intake.dispatch()
        from app.adapters.queue.models import WorkerCommand
        with db() as session:
            command_id = session.scalar(select(WorkerCommand.id))
        assert worker.tasks['rwb.stage.execute.v1'].apply(args=[str(command_id)]).get() == 'recovery_pending'
        assert worker.tasks['rwb.stage.execute.v1'].apply(args=[str(command_id)]).get() == 'recovered'
        assert not calls
        assert client.get(f'/api/v1/runs/{run_id}').json()['status'] == 'failed'
        response = client.post(f'/api/v1/runs/{run_id}/retry', headers={'Idempotency-Key': 'retry'}, json={})
        assert response.status_code == 202, response.text
        existing.execute_next(db, worker)
        assert len(calls) == 1, 'Registered retry must bootstrap from immutable input without an old checkpoint'
        existing.execute_next(db, worker)
        assert len(calls) == 5
        with db() as session:
            stages = list(session.scalars(select(StageAttempt).where(StageAttempt.run_id == UUID(run_id))))
            assert sorted((a.stage_id, a.attempt_no, a.status) for a in stages) == [
                ('analysis', 1, 'failed'), ('analysis', 2, 'succeeded'), ('architecture', 1, 'succeeded'),
                ('frontend-template', 1, 'pending')]
        fresh = factory()
        assert fresh.tasks['rwb.stage.execute.v1'].apply(args=[str(command_id)]).get() == 'already_handled'
        assert len(calls) == 5


def test_cancel_during_provider_error_finishes_normally(db, monkeypatch):
    app, factory, calls, requirements = existing.setup(db, waiting=False,
        profile='web-fastapi-angular', fail_first=True)
    from app.agents.llm import LLMAdapter
    generate = LLMAdapter._generate
    with TestClient(app) as client:
        run_id = create(client, requirements, 'cancel-error')
        async def cancel(self, **kwargs):
            result = await generate(self, **kwargs)
            assert client.post(f'/api/v1/runs/{run_id}/cancel').status_code == 200
            return result
        monkeypatch.setattr(LLMAdapter, '_generate', cancel)
        worker = factory()
        worker.stage_worker.orchestrator.runtime.intake.dispatch()
        existing.execute_next(db, worker)
        assert client.get(f'/api/v1/runs/{run_id}').json()['status'] == 'cancelled'
        assert len(calls) == 1


def test_cancel_blocks_next_architecture_node(db, monkeypatch):
    app, factory, calls, requirements = existing.setup(db, waiting=False, profile='web-fastapi-angular')
    # RuntimeNodes imports the canonical package; analysis uses app.*.
    from backend.app.agents.llm import LLMAdapter
    generate = LLMAdapter._generate
    with TestClient(app) as client:
        run_id = create(client, requirements, 'cancel-architecture')
        async def cancel(self, **kwargs):
            result = await generate(self, **kwargs)
            if len(calls) == 2:
                assert client.post(f'/api/v1/runs/{run_id}/cancel').status_code == 200
            return result
        monkeypatch.setattr(LLMAdapter, '_generate', cancel)
        worker = factory()
        worker.stage_worker.orchestrator.runtime.intake.dispatch()
        existing.execute_next(db, worker)
        existing.execute_next(db, worker)
        assert [node for node, _ in calls] == ['analysis', 'proposal']
        assert client.get(f'/api/v1/runs/{run_id}').json()['status'] == 'cancelled'


def test_terminal_intent_blocks_new_calls_but_settles_dispatched_usage(db, monkeypatch):
    app, factory, calls, requirements = existing.setup(db, waiting=False,
        profile='web-fastapi-angular', invalid_first=True)
    from app.agents.llm import LLMAdapter
    from app.services.runtime.models import RunRuntimeState
    generate = LLMAdapter._generate
    with TestClient(app) as client:
        run_id = create(client, requirements, 'terminal-intent')
        async def stop(self, **kwargs):
            result = await generate(self, **kwargs)
            if len(calls) == 1:
                with db.begin() as session:
                    row = session.get(RunRuntimeState, UUID(run_id))
                    payload = deepcopy(row.payload)
                    payload['saved_state']['terminal_intent'] = {
                        'target': 'failed', 'reason_code': 'RECOVERY_EXHAUSTED', 'stage_id': 'analysis'}
                    row.payload = payload
            return result
        monkeypatch.setattr(LLMAdapter, '_generate', stop)
        worker = factory()
        worker.stage_worker.orchestrator.runtime.intake.dispatch()
        existing.execute_next(db, worker)
        assert len(calls) == 1
        with db() as session:
            rows = list(session.scalars(select(ModelCall).where(ModelCall.run_id == UUID(run_id))))
            assert len(rows) == 1 and rows[0].payload['usage']['output_tokens'] is not None
        assert client.get(f'/api/v1/runs/{run_id}').json()['status'] == 'failed'


def test_missing_checkpoint_does_not_bootstrap_over_a_recorded_call(db, monkeypatch):
    from app.services.artifacts import PostgreSQLArtifactStore
    from app.adapters.queue.models import WorkerCommand
    app, factory, calls, requirements = existing.setup(db, waiting=False, profile='web-fastapi-angular')
    accept = PostgreSQLArtifactStore.accept
    failed = []
    def storage_failure(self, *args, **kwargs):
        if not failed:
            failed.append(True)
            raise OSError('Storage failure after the accounted model response')
        return accept(self, *args, **kwargs)
    monkeypatch.setattr(PostgreSQLArtifactStore, 'accept', storage_failure)
    with TestClient(app) as client:
        run_id = create(client, requirements, 'lost-checkpoint-with-call')
        worker = factory()
        worker.stage_worker.orchestrator.runtime.intake.dispatch()
        with db() as session:
            command_id = session.scalar(select(WorkerCommand.id))
        assert worker.tasks['rwb.stage.execute.v1'].apply(args=[str(command_id)]).get() == 'recovery_pending'
        assert len(calls) == 1
        with db.begin() as session:
            session.execute(text('DELETE FROM pipeline_checkpoint_writes WHERE run_id=:id'), {'id': UUID(run_id)})
            session.execute(text('DELETE FROM pipeline_checkpoints WHERE run_id=:id'), {'id': UUID(run_id)})
        fresh = factory()
        assert fresh.tasks['rwb.stage.execute.v1'].apply(args=[str(command_id)]).get() == 'recovery_pending'
        assert len(calls) == 1
        with db() as session:
            rows = list(session.scalars(select(ModelCall).where(ModelCall.run_id == UUID(run_id))))
            assert len(rows) == 1 and rows[0].payload['response'] is not None
