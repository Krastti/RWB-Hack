"""HTTP -> actual worker -> source generators -> snapshot and exact-byte ZIP."""
import json
from hashlib import sha256
from io import BytesIO
from uuid import UUID
from zipfile import ZipFile
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_runtime_recovery_postgresql import db
from test_pipeline_execution import setup, execute_next
from mvp_source_fixture import FILES
from app.models import StageAttempt
from app.adapters.queue.models import WorkerCommand
from app.services.artifacts.models import ModelCall


def provider(calls):
    def reply(request):
        sent = json.loads(request.content)
        prompt = json.loads(sent['messages'][1]['content'])
        calls.append(prompt['task']['task_id'])
        data = {'files': [{'path': path, 'content': FILES[path], 'complete': True} for path in prompt['batch_paths']]}
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(data)}}],
            'usage': {'prompt_tokens': 400, 'completion_tokens': 600, 'total_tokens': 1000}})
    return httpx.MockTransport(reply)


@pytest.mark.parametrize('fault', [None, 'response', 'result', 'snapshot'])
def test_complete_web_mvp_with_worker_restarts_and_exact_archive(db, tmp_path, monkeypatch, fault):
    app, factory, calls, requirements = setup(db, waiting=False, profile='web-fastapi-angular', configured=False)
    generated_calls = []
    failed = []
    class SimulatedProcessCrash(BaseException):
        pass
    if fault == 'response':
        from app.services.artifacts.budget import PostgreSQLBudgetLedger
        finish = PostgreSQLBudgetLedger.finish
        def crash_response(self, **kwargs):
            finish(self, **kwargs)
            if not failed and self.artifacts.execution.lease.command.stage_id == 'backend':
                failed.append(True)
                raise SimulatedProcessCrash()
        monkeypatch.setattr(PostgreSQLBudgetLedger, 'finish', crash_response)
    elif fault == 'result':
        from app.services.artifacts.store import PostgreSQLArtifactStore
        accept = PostgreSQLArtifactStore.accept
        def crash_result(self, run, attempt, result):
            accept(self, run, attempt, result)
            if not failed and result['stage_id'] == 'backend':
                failed.append(True)
                raise OSError('controlled result-to-checkpoint outage')
        monkeypatch.setattr(PostgreSQLArtifactStore, 'accept', crash_result)
    elif fault == 'snapshot':
        from app.services.artifacts.snapshots import SnapshotPublisher
        publish = SnapshotPublisher.publish
        def crash_snapshot(self, manifest):
            publish(self, manifest)
            if not failed:
                failed.append(True)
                raise OSError('controlled snapshot-to-checkpoint outage')
        monkeypatch.setattr(SnapshotPublisher, 'publish', crash_snapshot)
    def worker():
        result = factory()
        result.stage_worker.orchestrator.transports.update({name: provider(generated_calls) for name in ('backend', 'frontend')})
        return result
    with TestClient(app) as client:
        project = client.post('/api/v1/projects', json={'name':'MVP', 'requirements_text':requirements}).json()
        created = client.post(f'/api/v1/projects/{project["id"]}/runs', headers={'Idempotency-Key':'mvp'},
            json={'requirements_text': requirements, 'profile_id':'web-fastapi-angular'})
        assert created.status_code == 202, created.text
        run_id = created.json()['run_id']
        worker().stage_worker.orchestrator.runtime.intake.dispatch()
        commands = []
        for _ in range(14):
            run = client.get(f'/api/v1/runs/{run_id}').json()
            if run['status'] != 'running' and commands:
                break
            with db() as session:
                pending = session.scalar(select(WorkerCommand).where(WorkerCommand.state == 'pending'))
                stage = session.get(StageAttempt, pending.attempt_id).stage_id
            if fault and not failed and stage == ('assembly' if fault == 'snapshot' else 'backend'):
                if fault == 'response':
                    with pytest.raises(SimulatedProcessCrash):
                        worker().stage_worker.execute(pending.id)
                    from datetime import datetime, timedelta, timezone
                    with db.begin() as session:
                        session.get(WorkerCommand, pending.id).expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
                else:
                    assert worker().tasks['rwb.stage.execute.v1'].apply(args=[str(pending.id)]).get() == 'recovery_pending'
                assert failed
                assert worker().tasks['rwb.stage.execute.v1'].apply(args=[str(pending.id)]).get() == 'recovered'
                commands.append(pending.id)
            else:
                commands.append(execute_next(db, worker()))
        run = client.get(f'/api/v1/runs/{run_id}').json()
        assert run['status'] == 'completed', run
        assert len(commands) == 10
        assert generated_calls == ['backend', 'backend', 'frontend', 'frontend']
        assert bool(failed) == bool(fault)
        page = client.get(f'/api/v1/runs/{run_id}/artifacts').json()
        assert page['next_cursor'] is None
        response = client.get(f'/api/v1/runs/{run_id}/archive', params={'snapshot_id':page['snapshot_id']})
        assert response.status_code == 200, response.text
        archive = ZipFile(BytesIO(response.content))
        manifest = json.loads(archive.read('manifest.json'))
        assert manifest['readiness'] == 'prepared'
        assert set(archive.namelist()) == {a['path'] for a in manifest['artifacts']} | {'manifest.json'}
        for artifact in manifest['artifacts']:
            assert sha256(archive.read(artifact['path'])).hexdigest() == artifact['checksum']
        trace = json.loads(archive.read('traceability.json'))
        assert all(c['status'] == 'partial' for c in trace['coverage'])
        assert any(c['operation_ids'] for c in trace['coverage'])
        with db() as session:
            attempts = list(session.scalars(select(StageAttempt).where(StageAttempt.run_id == UUID(run_id))))
            ledger = list(session.scalars(select(ModelCall).where(ModelCall.run_id == UUID(run_id))))
            assert len(attempts) == 10 and all(a.status == 'succeeded' for a in attempts)
            assert len(ledger) == len(calls) + len(generated_calls) == run['metrics']['model_calls']
        before = len(calls), len(generated_calls)
        for command in commands:
            assert worker().tasks['rwb.stage.execute.v1'].apply(args=[str(command)]).get() == 'already_handled'
        assert (len(calls), len(generated_calls)) == before
        # Optional local artifact export supports separate build/migration tests.
        import os
        if os.environ.get('RWB_MVP_EXPORT_DIR'):
            from pathlib import Path
            target = Path(os.environ['RWB_MVP_EXPORT_DIR'])
            for name in archive.namelist():
                path = target / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(archive.read(name))
