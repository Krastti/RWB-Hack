"""Real PostgreSQL + existing intake/lease; no in-memory persistence stand-ins."""
from copy import deepcopy
from hashlib import sha256
from uuid import uuid4
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from test_runtime_recovery_postgresql import db
from test_runtime_intake import seed, runtime
from app.models import Project, Run
from app.adapters.queue.models import WorkerCommand
from app.adapters.queue import LeaseLost
from app.workers.execution import LeasedExecution
from app.agents.profiles import ProfileLoader
from app.services.artifacts import PostgreSQLArtifactStore, PostgreSQLBatchStore, ArtifactError
from app.services.artifacts.models import ArtifactObject, ExecutionReceipt
from app.services.artifacts.budget import PostgreSQLBudgetLedger
from app.agents.generation import GenerationError
from app.models.dto.llm import LLMResult, LLMUsage


def setup_store(db):
    run_id, _ = seed(db)
    rt = runtime(db)
    assert rt.intake.dispatch()['accepted'] == 1
    with db() as s:
        command = s.scalar(select(WorkerCommand))
        command_id = command.id
        owner = s.scalar(select(Project.owner_id).join(Run, Run.project_id == Project.id).where(Run.id == run_id))
    lease = rt.queue.claim(command_id)
    store = PostgreSQLArtifactStore(db, owner_id=owner, execution=LeasedExecution(rt.queue, lease),
                                    profile=ProfileLoader().load('web-fastapi-angular'))
    return store, run_id, lease, rt


def artifact(lease, path='docs/system-analysis.md', content='Subject analysis'):
    raw = content.encode()
    return {'artifact_id': str(uuid4()), 'attempt_id': str(lease.command.attempt_id),
        'path': path, 'kind': 'system_analysis', 'media_type': 'text/markdown', 'version': 1,
        'schema_version': '1.0', 'author_agent': 'analysis', 'checksum': sha256(raw).hexdigest(),
        'size_bytes': len(raw), 'related_fr_ids': [], 'content': content}


def test_atomic_batch_replay_restart_and_owner(db):
    store, run_id, lease, rt = setup_store(db)
    first = artifact(lease)
    batches = PostgreSQLBatchStore(store)
    scope = dict(owner_id=str(store.owner_id), run_id=str(run_id), attempt_id=str(lease.command.attempt_id))
    batches.open_attempt(**scope, fingerprint='f' * 64)
    call_id = str(uuid4())
    ledger = PostgreSQLBudgetLedger(store)
    ledger.reserve(**{k: scope[k] for k in ('run_id', 'attempt_id')}, call_id=call_id, model='test',
        system='policy', prompt='generate', schema={'type': 'object'}, max_output_tokens=100,
        timeout_seconds=10, agent_limits={'max_calls': 10, 'max_output_tokens': 1000})
    ledger.finish(**{k: scope[k] for k in ('run_id', 'attempt_id')}, call_id=call_id,
        result=LLMResult(data={}, usage=LLMUsage(calls=1, tokens_in=20, tokens_out=30)))
    receipt = {'schema_version': '1.0', 'artifacts': [first], 'call_id': call_id, 'fingerprint': 'a' * 64}
    batches.accept(**scope, batch_id='1', receipt=receipt)
    batches.accept(**scope, batch_id='1', receipt=receipt)
    restarted = PostgreSQLArtifactStore(db, owner_id=store.owner_id, execution=store.execution, profile=store.profile)
    assert restarted.read(str(run_id), first['artifact_id']) == first
    assert PostgreSQLBatchStore(restarted).receipt(**scope, batch_id='1') == receipt
    with pytest.raises(ArtifactError):
        PostgreSQLArtifactStore(db, owner_id=uuid4(), execution=store.execution,
            profile=store.profile).read(str(run_id), first['artifact_id'])
    changed = deepcopy(first); changed['content'] += '!'
    with pytest.raises((ArtifactError, ValueError)):
        store.put(str(run_id), changed)
    good = artifact(lease, content='new'); good['version'] = 2
    denied = artifact(lease, path='backend/app/main.py')
    with pytest.raises(ArtifactError):
        batches.accept(**scope, batch_id='2', receipt={**receipt, 'artifacts': [good, denied]})
    with db() as s:
        assert s.get(ArtifactObject, good['artifact_id']) is None
        assert s.get(ExecutionReceipt, (lease.command.attempt_id, 'batch:2')) is None


def test_lease_loss_blocks_publication(db):
    store, run_id, lease, rt = setup_store(db)
    item = artifact(lease)
    with db.begin() as s:
        s.get(WorkerCommand, lease.command.command_id).expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    with pytest.raises(LeaseLost):
        store.put(str(run_id), item)
    with db() as s:
        assert s.get(ArtifactObject, item['artifact_id']) is None


def test_budget_unknown_reservation_and_settlement_replay(db):
    store, run_id, lease, rt = setup_store(db)
    ledger = PostgreSQLBudgetLedger(store)
    scope = dict(run_id=str(run_id), attempt_id=str(lease.command.attempt_id))
    call_id = str(uuid4())
    args = dict(**scope, call_id=call_id, model='test', system='server policy', prompt='requirements',
                schema={'type': 'object'}, max_output_tokens=700, timeout_seconds=10,
                agent_limits={'max_calls': 10, 'max_output_tokens': 1000})
    ledger.reserve(**args)
    with pytest.raises(GenerationError, match='ALREADY_RESERVED'):
        ledger.reserve(**args)
    ledger.abandon(**scope, call_id=call_id)
    assert ledger.usage(**scope)['output_tokens'] is None
    with pytest.raises(GenerationError, match='BUDGET_EXCEEDED'):
        ledger.reserve(**{**args, 'call_id': str(uuid4())})
    result = LLMResult(data={}, usage=LLMUsage(calls=1, tokens_in=20, tokens_out=30, latency_ms=100))
    ledger.finish(**scope, call_id=call_id, result=result)
    ledger.finish(**scope, call_id=call_id, result=result)
    assert ledger.usage(**scope)['calls'] == 1
    assert ledger.usage(**scope)['output_tokens'] == 30
    ledger.reserve(**{**args, 'call_id': str(uuid4())})
    assert ledger.usage(**scope)['output_tokens'] is None


def test_real_batch_executor_with_postgresql_replay(db):
    """Real A-01/A-30/A-22 with controlled HTTP transport, not a live provider.

    This is a generation-port integration test, not HTTP-to-ZIP E2E.
    """
    import asyncio
    import json
    import httpx
    from app.agents.context import ContextBuilder, ResolvedArtifact
    from app.agents.generation import BatchExecutor, FileSpec
    from app.agents.llm import LLMConfig

    store, run_id, lease, rt = setup_store(db)
    # Trusted stage registry supplies the backend owner to the bound adapter.
    store.owner_agent = 'backend'
    # A-30's fixture is only the already-accepted upstream handoff in this test.
    from backend.app.agents.generation.example import fixture_inputs
    upstream = list(fixture_inputs().values())
    upstream = [a for a in upstream if hasattr(a, 'metadata') and a.metadata['kind'] in
                {'requirements', 'openapi', 'data_model'}]
    resolved = {a.metadata['artifact_id']: ResolvedArtifact(str(store.owner_id), str(run_id),
                '1', True, a.metadata, a.content) for a in upstream}
    class Inputs:
        def resolve(self, *, owner_id, run_id, artifact_id):
            return resolved[artifact_id]
    request = {'schema_version': '1.0', 'run_id': str(run_id), 'stage_id': 'analysis',
        'attempt': 1, 'contract_version': '1', 'profile_id': store.profile.profile_id,
        'input_artifact_ids': list(resolved), 'limits': {'max_calls': 10, 'max_output_tokens': 1000}}
    config = LLMConfig(base_url='https://controlled.invalid/v1', model='controlled-test', api_key='test',
                       max_output_tokens=512, timeout_seconds=10)
    execution_inputs = {'prompt': b'Implement the approved service.', 'template': b'',
        'model_configuration': json.dumps({k: v for k, v in vars(config).items() if k != 'api_key'}).encode(),
        'compiler_configuration': b'{"language":"python"}'}
    context = ContextBuilder(Inputs(), max_context_bytes=262144).prepare(request,
        owner_id=str(store.owner_id), owner_agent='backend', profile=store.profile,
        references={k: a.metadata for k, a in resolved.items()}, execution_inputs=execution_inputs)
    path = 'backend/app/services/request_state.py'
    plan = {'schema_version': '1.0', 'contract_version': '1', 'profile_id': store.profile.profile_id,
        'tasks': [{'task_id': 'state', 'stage_id': 'analysis', 'owner_agent': 'backend', 'depends_on': [],
                  'input_artifact_ids': list(resolved), 'output_paths': [path], 'output_directories': [],
                  'related_fr_ids': ['FR-001']}]}
    calls = []
    content = 'def transition(current, expected, target):\n    if current != expected:\n        raise ValueError("stale state")\n    return target\n'
    def reply(request):
        calls.append(request)
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {
            'content': json.dumps({'files': [{'path': path, 'content': content, 'complete': True}]})}}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 60, 'total_tokens': 160}})
    def validate(spec, text, prepared, accepted):
        import ast
        ast.parse(text)
    def executor():
        return BatchExecutor(config=config, store=PostgreSQLBatchStore(store),
            ledger=PostgreSQLBudgetLedger(store), max_files_per_batch=1, max_request_bytes=262144,
            validate_file=validate, transport=httpx.MockTransport(reply))
    args = dict(request=request, owner_id=str(store.owner_id), attempt_id=str(lease.command.attempt_id),
        task_id='state', plan=plan, profile=store.profile, context=context,
        files=(FileSpec(path, 'source', 'text/x-python', 1, 200),),
        model_configuration=execution_inputs['model_configuration'])
    first = asyncio.run(executor().run(**args))
    assert first.result['status'] == 'succeeded', first.result['diagnostics']
    assert len(calls) == 1
    again = asyncio.run(executor().run(**args))
    assert again.result['status'] == 'succeeded'
    assert len(calls) == 1
    produced = first.result['output_artifacts'][0]
    assert store.read(str(run_id), produced['artifact_id'])['content'] == content


def test_partial_snapshot_http_owner_pagination_and_not_ready(db):
    """HTTP read API across new service instances; not generation E2E."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.artifacts import configure_artifacts
    from app.api.auth import current_user
    from app.models.dto.auth import CurrentUser
    from app.services.artifacts.snapshots import SnapshotPublisher
    from app.services.artifacts.store import metadata
    store, run_id, lease, rt = setup_store(db)
    item = artifact(lease)
    store.put(str(run_id), item)
    store.owner_agent = 'assembly'
    manifest = {'schema_version': '1.0', 'snapshot_id': str(uuid4()), 'run_id': str(run_id),
        'contract_version': '1.0', 'profile_id': store.profile.profile_id,
        'created_at': '2026-10-31T12:00:00Z', 'readiness': 'partial',
        'artifacts': [metadata(item)], 'traceability_artifact_id': None, 'unresolved_items': []}
    publisher = SnapshotPublisher(store)
    publisher.publish(manifest)
    publisher.publish(manifest)
    with pytest.raises(ArtifactError, match='SNAPSHOT_ID_CONFLICT'):
        publisher.publish({**manifest, 'created_at': '2026-10-31T13:00:00Z'})
    # A fresh app/service reads the committed database, not producer memory.
    def app_for(owner):
        app = FastAPI()
        configure_artifacts(app, db)
        app.dependency_overrides[current_user] = lambda: CurrentUser(owner, 'test')
        return app
    with TestClient(app_for(store.owner_id)) as client:
        page = client.get(f'/api/v1/runs/{run_id}/artifacts?limit=1')
        assert page.status_code == 200
        assert page.json()['snapshot_id'] == manifest['snapshot_id']
        assert page.json()['items'] == [metadata(item)]
        response = client.get(f'/api/v1/runs/{run_id}/artifacts/{item["artifact_id"]}')
        assert response.status_code == 200 and response.content == item['content'].encode()
        assert response.headers['content-disposition'].startswith('attachment;')
        assert client.get(f'/api/v1/runs/{run_id}/archive?snapshot_id={manifest["snapshot_id"]}').status_code == 409
        assert client.get(f'/api/v1/runs/{run_id}/artifacts?cursor=bad').status_code == 422
    with TestClient(app_for(uuid4())) as client:
        assert client.get(f'/api/v1/runs/{run_id}/artifacts').status_code == 404
        assert client.get(f'/api/v1/runs/{run_id}/artifacts/{item["artifact_id"]}').status_code == 404


def test_archive_reader_roundtrip_pins_immutable_bytes(db):
    """Reader contract test with a prepared snapshot fixture, not assembly E2E."""
    from io import BytesIO
    from zipfile import ZipFile
    import json
    from app.services.artifacts.models import ArtifactSnapshot
    from app.services.artifacts.snapshots import ArtifactReadService
    from app.services.artifacts.store import metadata
    store, run_id, lease, rt = setup_store(db)
    item = artifact(lease)
    store.put(str(run_id), item)
    snapshot_id = uuid4()
    manifest = {'schema_version': '1.0', 'snapshot_id': str(snapshot_id), 'run_id': str(run_id),
        'contract_version': '1.0', 'profile_id': store.profile.profile_id,
        'created_at': '2026-10-31T12:00:00Z', 'readiness': 'prepared',
        'artifacts': [metadata(item)], 'traceability_artifact_id': str(uuid4()), 'unresolved_items': []}
    # Test-only fixture bypasses publication to isolate archive encoding. A production
    # publisher would reject this incomplete required inventory as intended.
    with db.begin() as session:
        session.add(ArtifactSnapshot(snapshot_id=snapshot_id, run_id=run_id, manifest=manifest))
    newer = {**artifact(lease, content='New version'), 'version': 2}
    store.put(str(run_id), newer)
    reader = ArtifactReadService(db)
    archive = reader.archive(owner_id=store.owner_id, run_id=run_id, snapshot_id=snapshot_id)
    with ZipFile(BytesIO(archive)) as zipped:
        assert set(zipped.namelist()) == {item['path'], 'manifest.json'}
        assert zipped.read(item['path']) == item['content'].encode()
        assert json.loads(zipped.read('manifest.json')) == manifest
        assert sha256(zipped.read(item['path'])).hexdigest() == item['checksum']


def test_provider_admission_spacing_is_durable_and_does_not_reserve_denied_call(db):
    from app.services.artifacts.budget import RateAdmissionDeferred
    store, run_id, lease, rt = setup_store(db)
    scope = dict(run_id=str(run_id), attempt_id=str(lease.command.attempt_id))
    args = dict(**scope, model='rate-test', system='policy', prompt='subject', schema={'type': 'object'},
                max_output_tokens=100, timeout_seconds=10, agent_limits={'max_calls': 10, 'max_output_tokens': 1000},
                minimum_interval_seconds=65, rate_scope='controlled-provider-key-hash')
    ledger = PostgreSQLBudgetLedger(store)
    ledger.reserve(**args, call_id=str(uuid4()))
    fresh = PostgreSQLBudgetLedger(store)
    with pytest.raises(RateAdmissionDeferred) as error:
        fresh.reserve(**args, call_id=str(uuid4()))
    assert 0 < error.value.seconds <= 65
    assert fresh.usage(**scope)['calls'] == 1
    # Admission rollback released the Run lock; heartbeat remains possible.
    rt.queue.heartbeat(lease)
