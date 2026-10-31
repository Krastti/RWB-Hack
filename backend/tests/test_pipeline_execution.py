"""HTTP → Celery task → StageWorker → real adapters/graphs → PostgreSQL.

Controlled HTTPX model transport only. No accepted results or artifacts are seeded.
"""
import json
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text, func
from test_runtime_recovery_postgresql import db
from app.main import create_app
from app.api.auth import current_user
from app.models.dto.auth import CurrentUser
from app.config.auth import AuthConfig
from app.models import User, Run, StageAttempt, RunEvent
from app.adapters.queue.models import WorkerCommand
from app.services.artifacts.models import ArtifactObject, ModelCall, ExecutionReceipt
from app.workers.pipeline import create_pipeline_worker
from app.agents.llm import LLMConfig

ROOT = Path(__file__).resolve().parents[2]

def read(path):
    return json.loads((ROOT / path).read_text())

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def setup(db, *, waiting, profile, configured=True, fail_first=False):
    owner = uuid4()
    with db.begin() as s:
        s.add(User(id=owner, login=str(owner), password_hash='test'))
    answered = read('templates/analysis/examples/draft.answered.json')
    original = read('templates/analysis/examples/draft.json')
    requirements_text = read('templates/analysis/examples/analysis-input.json')['requirements_text']
    if not waiting:
        requirements_text += ' Удаление запрещено всем ролям.'
        answered['requirements']['questions'] = []
        def replace_evidence(value):
            if isinstance(value, list):
                for item in value: replace_evidence(item)
            elif isinstance(value, dict):
                if value.get('source') == 'answer':
                    value.update(source='requirements', question_id=None)
                for v in value.values(): replace_evidence(v)
        replace_evidence(answered)
        for note in answered['design_notes'].values():
            note['question_ids'] = []
    checksum = sha256(canonical(answered['requirements']).encode()).hexdigest()
    policies = {'api_policy': read('backend/app/agents/generators/openapi/examples/api-policy.json'),
                'naming_policy': read('backend/app/agents/graph/architecture/examples/naming-policy.json')}
    for p in policies.values(): p['requirements_checksum'] = checksum
    with db() as s:
        schema = s.scalar(text('select current_schema()'))
    url = db.kw['bind'].url.update_query_dict({'options': '-csearch_path=' + schema + ',public'}).render_as_string(hide_password=False)
    app = create_app(database_url=url, auth_config=AuthConfig(secret_key='test' * 16), execution_policies=policies if configured else {})
    app.dependency_overrides[current_user] = lambda: CurrentUser(owner, 'test')
    calls = []
    def transport(node):
        def reply(request):
            payload = json.loads(json.loads(request.content)['messages'][1]['content'])
            # The external model call is made with no PostgreSQL Run lock held.
            with db.begin() as unlocked:
                unlocked.execute(select(Run).with_for_update(nowait=True)).all()
            calls.append((node, payload))
            if fail_first and len(calls) <= int(fail_first):
                return httpx.Response(429, json={'error': {'message': 'controlled rate limit'}})
            if node == 'analysis':
                draft = answered if payload['business_input']['answers'] or not waiting else original
            elif node == 'proposal':
                a = read('contracts/agents/examples/architecture.json')
                draft = {k: a[k] for k in ('components', 'decisions', 'integrations')}
                if profile == 'russtech-fullstack':
                    for c in draft['components']:
                        c['output_directories'] = ['backend/src' if d == 'backend' else d.replace('backend/app', 'backend/src') for d in c['output_directories']]
            elif node == 'openapi':
                draft = read('backend/app/agents/generators/openapi/examples/draft.json')
                draft['assumptions'] = [a for a in payload['requirements']['assumptions'] if a['id'] == 'ASM-002']
                for name in ('Ticket', 'TicketStatusUpdate'):
                    draft['schemas'][name]['properties']['status']['maxLength'] = 32
            elif node == 'data_model':
                draft = read('backend/app/agents/generators/data_model/examples/data-model.json')
                draft.update(profile_id=profile, contract_version=payload['context']['contract_version'],
                             requirements_artifact_id=payload['requirements_artifact_id'])
            else:
                draft = read('backend/app/agents/graph/architecture/examples/ui-draft.json')
            return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(draft)}}],
                'usage': {'prompt_tokens': 100, 'completion_tokens': 200, 'total_tokens': 300}})
        return httpx.MockTransport(reply)
    def worker():
        return create_pipeline_worker(sessions=db, config=LLMConfig(base_url='https://controlled.invalid/v1', model='test',
            api_key='controlled-test', max_output_tokens=8000), broker_url='memory://',
            transports={k: transport(k) for k in ('analysis', 'proposal', 'openapi', 'data_model', 'ui_spec')})
    return app, worker, calls, requirements_text


def execute_next(db, worker):
    with db() as s:
        command = s.scalar(select(WorkerCommand).where(WorkerCommand.state == 'pending').order_by(WorkerCommand.created_at))
        assert command is not None
        identity = command.id
    # Registered Celery task calls the actual StageWorker/LeasedExecution.
    result = worker.tasks['rwb.stage.execute.v1'].apply(args=[str(identity)]).get()
    assert result == 'completed', result
    return identity


@pytest.mark.parametrize('profile', ['web-fastapi-angular', 'russtech-fullstack'])
def test_http_analysis_architecture(db, profile):
    app, worker_factory, calls, requirements = setup(db, waiting=False, profile=profile)
    with TestClient(app) as client:
        project = client.post('/api/v1/projects', json={'name': 'Requests', 'requirements_text': requirements})
        assert project.status_code == 201, project.text
        created = client.post(f'/api/v1/projects/{project.json()["id"]}/runs',
            headers={'Idempotency-Key': 'pipeline'}, json={'requirements_text': requirements, 'profile_id': profile})
        assert created.status_code == 202, created.text
        run_id = UUID(created.json()['run_id'])
        worker = worker_factory()
        assert worker.queue_adapter.store.states.repository is not None
        # Existing intake dispatch, no manual attempts or accepted-result INSERTs.
        runtime = worker.stage_worker.orchestrator.runtime
        assert runtime.intake.dispatch()['accepted'] == 1
        first = execute_next(db, worker)
        second = execute_next(db, worker)
        with db() as debug:
            diagnostics = [r.payload['diagnostics'] for r in debug.scalars(select(ExecutionReceipt).where(ExecutionReceipt.key == 'result'))]
        assert len(calls) == 5, diagnostics
        if profile == 'russtech-fullstack':
            assert calls[0][1]['profile_policy']['stack']['backend'] == ['ASP.NET Core 10', 'C# 14', 'EF Core 10']
            assert calls[1][1]['profile_policy']['stack']['frontend'][0] == 'Angular 20.1.6'
            assert calls[3][1]['profile']['stack']['migrations'] == 'EF Core migrations'
        assert worker.tasks['rwb.stage.execute.v1'].apply(args=[str(first)]).get() == 'already_handled'
        assert worker.tasks['rwb.stage.execute.v1'].apply(args=[str(second)]).get() == 'already_handled'
        assert len(calls) == 5
        with db() as s:
            attempts = list(s.scalars(select(StageAttempt).where(StageAttempt.run_id == run_id)))
            assert {a.stage_id: a.status for a in attempts} == {'analysis': 'succeeded', 'architecture': 'succeeded'}
            objects = list(s.scalars(select(ArtifactObject).where(ArtifactObject.run_id == run_id)))
            assert len(objects) == 9  # private input plus eight public artifacts
            for obj in objects:
                assert sha256(obj.content).hexdigest() == obj.metadata_json['checksum']
                if obj.metadata_json['author_agent'] != 'orchestrator':
                    downloaded = client.get(f'/api/v1/runs/{run_id}/artifacts/{obj.artifact_id}')
                    assert downloaded.status_code == 200 and downloaded.content == obj.content
            assert len(list(s.scalars(select(ModelCall).where(ModelCall.run_id == run_id)))) == 5
            physical_ids = {str(r.call_id) for r in s.scalars(select(ModelCall).where(ModelCall.run_id == run_id))}
            accepted_ids = {c['call_id'] for a in attempts for c in
                s.get(ExecutionReceipt, (a.id, 'result')).payload['usage']['model_calls']}
            assert accepted_ids == physical_ids
            assert s.get(Run, run_id).status == 'running'
            assert s.scalar(select(func.count()).select_from(RunEvent).where(RunEvent.run_id == run_id)) > 5
            assert s.scalar(text('select count(*) from pipeline_checkpoints')) > 0
            for a in attempts:
                assert s.get(ExecutionReceipt, (a.id, 'checkpoint')) is not None
        fresh = worker_factory()
        assert fresh.tasks['rwb.stage.execute.v1'].apply(args=[str(second)]).get() == 'already_handled'
        assert len(calls) == 5
        assert client.get(f'/api/v1/runs/{run_id}').json()['metrics']['model_calls'] == 5


def test_http_clarification_resume_same_run(db):
    app, worker_factory, calls, requirements = setup(db, waiting=True, profile='russtech-fullstack')
    with TestClient(app) as client:
        project = client.post('/api/v1/projects', json={'name': 'Requests', 'requirements_text': requirements}).json()
        created = client.post(f'/api/v1/projects/{project["id"]}/runs', headers={'Idempotency-Key': 'resume'},
            json={'requirements_text': requirements, 'profile_id': 'russtech-fullstack'})
        assert created.status_code == 202, created.text
        run_id = created.json()['run_id']
        worker = worker_factory()
        worker.stage_worker.orchestrator.runtime.intake.dispatch()
        first = execute_next(db, worker)
        waiting = client.get(f'/api/v1/runs/{run_id}').json()
        assert waiting['status'] == 'waiting_for_input', waiting
        q = waiting['questions'][0]
        body = {'questions_revision': waiting['questions_revision'],
                'answers': [{'question_id': q['question_id'], 'value': 'Удаление запрещено всем ролям.'}]}
        response = client.post(f'/api/v1/runs/{run_id}/answers', headers={'Idempotency-Key': 'answer'}, json=body)
        assert response.status_code == 202, response.text
        duplicate = client.post(f'/api/v1/runs/{run_id}/answers', headers={'Idempotency-Key': 'answer'}, json=body)
        assert duplicate.status_code == 202
        # New worker/services, same PostgreSQL LangGraph checkpoint and accepted answers.
        worker = worker_factory()
        resume = execute_next(db, worker)
        architecture = execute_next(db, worker)
        assert len(calls) == 6
        assert calls[1][1]['business_input']['answers'] == body['answers']
        with db() as s:
            attempts = list(s.scalars(select(StageAttempt).where(StageAttempt.run_id == UUID(run_id))))
            assert sorted((a.stage_id, a.attempt_no, a.status) for a in attempts) == [
                ('analysis', 1, 'waiting_for_input'), ('analysis', 2, 'succeeded'), ('architecture', 1, 'succeeded')]
            objects = list(s.scalars(select(ArtifactObject).where(ArtifactObject.run_id == UUID(run_id))))
            assert len(objects) == 13  # two immutable private inputs, 6 analysis files, 5 architecture files
            assert s.get(Run, UUID(run_id)).status == 'running'
        for command in (first, resume, architecture):
            assert worker.tasks['rwb.stage.execute.v1'].apply(args=[str(command)]).get() == 'already_handled'
        assert len(calls) == 6


@pytest.mark.parametrize('fault', ['checkpoint', 'accept', 'lease'])
def test_accepted_analysis_checkpoint_failure_recovers_without_call(db, monkeypatch, fault):
    from app.services.pipeline.checkpoints import PostgreSQLCheckpoints
    from app.services.artifacts import PostgreSQLArtifactStore
    app, worker_factory, calls, requirements = setup(db, waiting=False, profile='russtech-fullstack')
    with TestClient(app) as client:
        project = client.post('/api/v1/projects', json={'name': 'Recovery', 'requirements_text': requirements}).json()
        response = client.post(f'/api/v1/projects/{project["id"]}/runs', headers={'Idempotency-Key': 'recovery'},
            json={'requirements_text': requirements, 'profile_id': 'russtech-fullstack'})
        assert response.status_code == 202
        run_id = UUID(response.json()['run_id'])
        worker = worker_factory()
        worker.stage_worker.orchestrator.runtime.intake.dispatch()
        with db() as s:
            identity = s.scalar(select(WorkerCommand.id))
        put = PostgreSQLCheckpoints.put
        failed = []
        def flaky(self, config, checkpoint, metadata, versions):
            if not failed and self.artifacts.result(str(run_id), str(self.artifacts.execution.lease.command.attempt_id)):
                failed.append(True)
                raise OSError('injected checkpoint persistence outage')
            return put(self, config, checkpoint, metadata, versions)
        if fault == 'checkpoint':
            monkeypatch.setattr(PostgreSQLCheckpoints, 'put', flaky)
        else:
            accept = PostgreSQLArtifactStore.accept
            def fault_accept(self, run, attempt, result):
                if not failed and result['stage_id'] == 'analysis':
                    failed.append(True)
                    if fault == 'accept':
                        raise OSError('injected result storage outage')
                    accept(self, run, attempt, result)
                    from datetime import datetime, timedelta, timezone
                    with db.begin() as s:
                        s.get(WorkerCommand, identity).expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
                    return
                return accept(self, run, attempt, result)
            monkeypatch.setattr(PostgreSQLArtifactStore, 'accept', fault_accept)
        assert worker.tasks['rwb.stage.execute.v1'].apply(args=[str(identity)]).get() == ('fenced' if fault == 'lease' else 'recovery_pending')
        assert failed and len(calls) == 1
        fresh = worker_factory()
        assert fresh.tasks['rwb.stage.execute.v1'].apply(args=[str(identity)]).get() == 'recovered'
        assert len(calls) == 1
        execute_next(db, fresh)
        assert len(calls) == 5
        with db() as s:
            assert len(list(s.scalars(select(StageAttempt).where(StageAttempt.run_id == run_id)))) == 2
            assert s.get(Run, run_id).status == 'running'


def test_missing_trusted_policy_fails_without_architecture_model_call(db):
    app, worker_factory, calls, requirements = setup(db, waiting=False, profile='russtech-fullstack', configured=False)
    with TestClient(app) as client:
        project = client.post('/api/v1/projects', json={'name': 'No policy', 'requirements_text': requirements}).json()
        created = client.post(f'/api/v1/projects/{project["id"]}/runs', headers={'Idempotency-Key': 'policy'},
            json={'requirements_text': requirements, 'profile_id': 'russtech-fullstack'})
        assert created.status_code == 202
        worker = worker_factory()
        worker.stage_worker.orchestrator.runtime.intake.dispatch()
        execute_next(db, worker)
        execute_next(db, worker)
        assert len(calls) == 1
        with db() as s:
            run = s.get(Run, UUID(created.json()['run_id']))
            assert run.status == 'failed'
            attempt = s.scalar(select(StageAttempt).where(StageAttempt.run_id == run.id, StageAttempt.stage_id == 'architecture'))
            receipt = s.get(ExecutionReceipt, (attempt.id, 'result'))
            assert receipt.payload['diagnostics'][0]['code'] == 'ARCHITECTURE_POLICIES_REQUIRED'


def test_http_retry_after_model_rate_limit(db):
    app, factory, calls, requirements = setup(db, waiting=False, profile='russtech-fullstack', fail_first=True)
    with TestClient(app) as client:
        project = client.post('/api/v1/projects', json={'name': 'Retry', 'requirements_text': requirements}).json()
        created = client.post(f'/api/v1/projects/{project["id"]}/runs',
            headers={'Idempotency-Key': 'initial'}, json={'requirements_text': requirements, 'profile_id': 'russtech-fullstack'})
        run_id = created.json()['run_id']
        worker = factory()
        worker.stage_worker.orchestrator.runtime.intake.dispatch()
        first = execute_next(db, worker)
        assert client.get(f'/api/v1/runs/{run_id}').json()['status'] == 'failed'
        response = client.post(f'/api/v1/runs/{run_id}/retry', headers={'Idempotency-Key': 'retry'}, json={})
        assert response.status_code == 202, response.text
        fresh = factory()
        second = execute_next(db, fresh)
        execute_next(db, fresh)
        with db() as s:
            attempts = list(s.scalars(select(StageAttempt).where(StageAttempt.run_id == UUID(run_id))))
            assert sorted((a.stage_id, a.attempt_no, a.status) for a in attempts) == [
                ('analysis', 1, 'failed'), ('analysis', 2, 'succeeded'), ('architecture', 1, 'succeeded')]
        assert len(calls) == 6
        assert fresh.tasks['rwb.stage.execute.v1'].apply(args=[str(second)]).get() == 'already_handled'
        assert len(calls) == 6


def test_unknown_rate_limit_usage_stops_at_durable_budget(db):
    app, factory, calls, requirements = setup(db, waiting=False, profile='web-fastapi-angular', fail_first=20)
    with TestClient(app) as client:
        project = client.post('/api/v1/projects', json={'name': 'Budget', 'requirements_text': requirements}).json()
        created = client.post(f'/api/v1/projects/{project["id"]}/runs',
            headers={'Idempotency-Key': 'budget-start'}, json={'requirements_text': requirements})
        run_id = created.json()['run_id']
        worker = factory()
        worker.stage_worker.orchestrator.runtime.intake.dispatch()
        for index in range(10):
            execute_next(db, worker)
            run = client.get(f'/api/v1/runs/{run_id}').json()
            if run['status'] == 'budget_exceeded':
                break
            assert run['status'] == 'failed', run
            retried = client.post(f'/api/v1/runs/{run_id}/retry', headers={'Idempotency-Key': f'budget-retry-{index}'}, json={})
            assert retried.status_code == 202, retried.text
        assert run['status'] == 'budget_exceeded', run
        assert run['stop_reason'] == 'MAX_INPUT_TOKENS'
        assert run['stages'][-1]['reason'] == 'BUDGET_MAX_INPUT_TOKENS'
        with db() as s:
            rows = list(s.scalars(select(ModelCall).where(ModelCall.run_id == UUID(run_id))))
            assert len(rows) == len(calls)
            assert all(r.payload['usage']['input_tokens'] is None for r in rows)
            assert sum(r.payload['reservation']['input'] for r in rows) <= 300000
        again = client.post(f'/api/v1/runs/{run_id}/retry', headers={'Idempotency-Key': 'budget-no-blind-retry'}, json={})
        assert again.status_code == 409


def test_registered_retry_after_pre_result_worker_failure(db, monkeypatch):
    from app.services.artifacts.budget import PostgreSQLBudgetLedger
    app, factory, calls, requirements = setup(db, waiting=False, profile='web-fastapi-angular')
    with TestClient(app) as client:
        project = client.post('/api/v1/projects', json={'name': 'Crash', 'requirements_text': requirements}).json()
        created = client.post(f'/api/v1/projects/{project["id"]}/runs',
            headers={'Idempotency-Key': 'crash'}, json={'requirements_text': requirements})
        run_id = created.json()['run_id']
        worker = factory()
        worker.stage_worker.orchestrator.runtime.intake.dispatch()
        with db() as s:
            identity = s.scalar(select(WorkerCommand.id))
        def outage(*args, **kwargs):
            raise OSError('pre-dispatch outage')
        with monkeypatch.context() as patch:
            patch.setattr(PostgreSQLBudgetLedger, 'reserve', outage)
            assert worker.tasks['rwb.stage.execute.v1'].apply(args=[str(identity)]).get() == 'recovery_pending'
        assert not calls
        fresh = factory()
        assert fresh.tasks['rwb.stage.execute.v1'].apply(args=[str(identity)]).get() == 'recovered'
        current = client.get(f'/api/v1/runs/{run_id}').json()
        if current['status'] == 'failed':
            response = client.post(f'/api/v1/runs/{run_id}/retry', headers={'Idempotency-Key': 'retry-crash'}, json={})
            assert response.status_code == 202, response.text
        execute_next(db, fresh)
        execute_next(db, fresh)
        assert len(calls) == 5
        current = client.get(f'/api/v1/runs/{run_id}').json()
        assert current['stages'][-1]['status'] == 'succeeded'
