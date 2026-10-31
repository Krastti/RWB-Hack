from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text, func
from test_runtime_recovery_postgresql import db
from test_pipeline_execution import setup
from app.agents.profiles import ProfileLoader
from app.adapters.queue.models import WorkerCommand
from app.models import Run, Project, StageAttempt
from app.workers.execution import LeasedExecution
from app.services.pipeline.active_time import ActiveTime, union_seconds
from app.services.artifacts.store import PostgreSQLArtifactStore
from app.services.artifacts.budget import PostgreSQLBudgetLedger, BudgetAdmissionError
from app.services.artifacts.models import ModelCall


def test_union_counts_overlap_once_and_skips_waits():
    t = datetime.now(timezone.utc)
    spans = [(t, t+timedelta(seconds=3)), (t+timedelta(seconds=2), t+timedelta(seconds=5)),
             (t+timedelta(hours=1), t+timedelta(hours=1, seconds=2))]
    assert union_seconds(spans) == 7


def test_durable_active_budget_clamps_calls_and_survives_clock_restart(db):
    app, factory, _, requirements = setup(db, waiting=False, profile='web-fastapi-angular')
    with TestClient(app) as client:
        project = client.post('/api/v1/projects',json={'name':'clock','requirements_text':requirements}).json()
        created = client.post(f'/api/v1/projects/{project["id"]}/runs',headers={'Idempotency-Key':'clock'},
                             json={'requirements_text':requirements,'profile_id':'web-fastapi-angular'}).json()
    worker = factory(); runtime = worker.stage_worker.orchestrator.runtime
    runtime.intake.dispatch()
    with db() as session:
        command = session.scalar(select(WorkerCommand).where(WorkerCommand.state == 'pending'))
        owner = session.get(Project, UUID(project['id'])).owner_id
    lease = runtime.queue.claim(command.id)
    execution = LeasedExecution(runtime.queue, lease)
    profile = ProfileLoader().load('web-fastapi-angular')
    clock = ActiveTime(execution, profile); clock.start()
    # A prior worker ran for 3595 seconds. Its missing close is clamped to
    # the expired lease, so the hour before this attempt is not active time.
    prior = uuid4()
    with db.begin() as session:
        session.add(StageAttempt(id=prior, run_id=lease.command.run_id, stage_id='prior-stage', attempt_no=1,
            contract_version='1', input_hash='a'*64, status='succeeded'))
        session.flush()
        session.execute(text('INSERT INTO pipeline_active_intervals VALUES '
            "(:attempt,1,:run,'prior-stage',clock_timestamp()-interval '7195 seconds',"
            "clock_timestamp()-interval '3600 seconds',NULL)"), dict(attempt=prior,run=lease.command.run_id))
    store = PostgreSQLArtifactStore(db, owner_id=owner, execution=execution, profile=profile, owner_agent='analysis')
    ledger = PostgreSQLBudgetLedger(store)
    args = dict(run_id=str(lease.command.run_id), attempt_id=str(lease.command.attempt_id), model='controlled',
                system='test',prompt='test',schema={'type':'object'},max_output_tokens=10,timeout_seconds=120,
                agent_limits={'max_calls':10,'max_output_tokens':30000,'timeout_seconds':600})
    admitted = ledger.reserve(**args,call_id=str(uuid4()))
    assert 0 < admitted <= 5
    replacement = ActiveTime(execution, profile)
    execution.activity_clock = replacement
    with execution.transaction() as session:
        remaining, _ = replacement.remaining(session,args['agent_limits'])
        assert 0 < remaining <= 5
        session.execute(text("UPDATE pipeline_active_intervals SET started_at=started_at-interval '10 seconds' WHERE attempt_id=:id"),{'id':prior})
    with pytest.raises(BudgetAdmissionError) as caught:
        ledger.reserve(**args,call_id=str(uuid4()))
    assert caught.value.reason == 'RUN_TIMEOUT'
    from app.models.dto.llm import LLMResult, LLMUsage
    late = ledger.deadline_result(LLMResult(data={'paid':True}, usage=LLMUsage(calls=1,tokens_in=2,tokens_out=3)), args['agent_limits'])
    assert late.error.code == 'BUDGET_RUN_TIMEOUT' and late.data is None and late.usage.calls == 1
    with db() as session:
        assert session.scalar(select(func.count()).select_from(ModelCall)) == 1
    replacement.stop()
