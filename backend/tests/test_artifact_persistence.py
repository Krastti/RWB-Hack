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
    receipt = {'artifacts': [first], 'call_id': str(uuid4()), 'fingerprint': 'a' * 64}
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
        batches.accept(**scope, batch_id='2', receipt={'artifacts': [good, denied]})
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
