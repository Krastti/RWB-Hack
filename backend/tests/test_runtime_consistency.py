"""Offline control/lifecycle regressions against durable local adapters."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from app.services.run_control import (Run, Record, StageAttempt, AttemptState, Limits,
    Retry, RunControlService, SQLiteControlStore, ControlError)
from app.services.lifecycle import (LifecycleService, SQLiteLifecycleRepository,
    RunRecord, Attempt, LifecycleError)
from app.services.lifecycle.events import EventSequenceError, next_event


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.owner, self.id = uuid4(), uuid4()
        self.store = SQLiteControlStore(Path(self.tmp.name)/'control.db')
        self.record = Record(owner_id=self.owner,
            run=Run(run_id=self.id, project_id=uuid4(), revision_id=uuid4(),
                status='budget_exceeded', stop_reason='MAX_CALLS',
                limits=Limits(max_calls=5,max_output_tokens=100),
                stages=[StageAttempt(stage_id='analysis', attempt_no=1,contract_version='1',status='failed')]),
            attempts=[AttemptState(stage_id='analysis',attempt_no=1,input_hash='a'*64,input_state={'frozen':True})],
            saved_state={'accepted':['artifact']},required_limits=Limits(max_calls=6,max_output_tokens=100))
        self.record.run.metrics.model_calls=5
        self.record.run.metrics.tokens_out=30
        self.service=RunControlService(self.store,authorize_retry=lambda *_:None)

    def save(self): self.store.create(self.record)
    def get(self):
        with self.store.transaction(self.owner,self.id) as r: return r.model_copy(deep=True)
    def retry(self,key='key',**limits):
        return self.service.retry(owner_id=self.owner,run_id=self.id,key=key,
            body=Retry(limits=Limits(**(limits or dict(max_calls=6,max_output_tokens=100)))))

    def test_angular_and_historical_dto_and_server_profile_guard(self):
        self.assertEqual(self.record.run.profile_id,'web-fastapi-angular')
        self.record.run.profile_id='web-fastapi-react'; self.save(); before=self.get()
        for command in (lambda:self.retry(),lambda:self.service.cancel(owner_id=self.owner,run_id=self.id)):
            with self.assertRaises(ControlError) as error: command()
            self.assertEqual(error.exception.code,'UNSUPPORTED_RUN_PROFILE')
            self.assertEqual(self.get(),before)

    def test_http_profile_budget_refusal_and_dto_compatibility(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from types import SimpleNamespace
        from app.api.auth import current_user
        from app.services.run_control.api import configure_run_control
        from app.services.run_queries.dto import Run as ReadRun
        # Public control response is accepted by the current read projection.
        self.assertEqual(ReadRun.model_validate(self.record.run.model_dump()).profile_id, 'web-fastapi-angular')
        self.record.run.stop_reason='MAX_INPUT_TOKENS'; self.save()
        app=FastAPI();configure_run_control(app,self.service)
        app.dependency_overrides[current_user]=lambda:SimpleNamespace(id=self.owner)
        client=TestClient(app)
        response=client.post(f'/api/v1/runs/{self.id}/retry',headers={'Idempotency-Key':'http'},
            json={'limits':{'max_calls':100,'max_output_tokens':1000}})
        self.assertEqual(response.status_code,409)
        self.assertIn('RETRY_NOT_SUPPORTED',response.text)
        with self.store.transaction(self.owner,self.id) as r:r.run.profile_id='web-fastapi-react'
        before=self.get()
        response=client.post(f'/api/v1/runs/{self.id}/cancel')
        self.assertEqual(response.status_code,409);self.assertIn('UNSUPPORTED_RUN_PROFILE',response.text)
        self.assertEqual(self.get(),before)

    def test_retry_preserves_frozen_state_usage_and_replay_events(self):
        self.save(); response=self.retry(); after=self.get()
        self.assertEqual(after.attempts[-1].input_hash,'a'*64)
        self.assertEqual(after.attempts[-1].input_state,{'frozen':True})
        self.assertEqual(after.run.metrics.model_calls,5)
        self.assertEqual(after.run.metrics.tokens_out,30)
        self.assertEqual(after.saved_state,{'accepted':['artifact']})
        self.assertEqual(self.retry(),response); self.assertEqual(self.get(),after)

    def test_unsupported_budget_reasons_fail_without_effects(self):
        for reason in ('MAX_INPUT_TOKENS','RUN_TIMEOUT','UNKNOWN',None):
            with self.subTest(reason=reason):
                self.record.run.stop_reason=reason
                with closing(self.store._connect()) as db, db: db.execute('DELETE FROM run_control')
                self.save(); before=self.get()
                with self.assertRaises(ControlError) as error: self.retry()
                self.assertEqual(error.exception.code,'RETRY_NOT_SUPPORTED')
                self.assertEqual(self.get(),before)

    def client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from types import SimpleNamespace
        from app.api.auth import current_user
        from app.services.run_control.api import configure_run_control
        app = FastAPI()
        configure_run_control(app, self.service)
        app.dependency_overrides[current_user] = lambda: SimpleNamespace(id=self.owner)
        return TestClient(app)

    def test_unsupported_budget_before_next_stage_has_normalized_http_error(self):
        self.record.run.stages = []
        self.record.attempts = []
        self.save()
        for reason in ('MAX_INPUT_TOKENS', 'RUN_TIMEOUT'):
            with self.store.transaction(self.owner, self.id) as record:
                record.run.stop_reason = reason
            before = self.get()
            response = self.client().post(f'/api/v1/runs/{self.id}/retry',
                headers={'Idempotency-Key': 'early-budget'}, json={})
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()['error']['code'], 'RETRY_NOT_SUPPORTED')
            self.assertEqual(self.get(), before)

    def test_accepted_results_require_internal_recovery_not_manual_retry(self):
        self.record.run.status = 'failed'
        self.record.run.stop_reason = 'CHECKPOINT_REQUIRED'
        self.record.run.stages[0].status = 'succeeded'
        self.record.attempts[0].accepted_result = {'status': 'succeeded', 'artifacts': ['accepted']}
        self.save()
        before = self.get()
        response = self.client().post(f'/api/v1/runs/{self.id}/retry',
            headers={'Idempotency-Key': 'checkpoint'}, json={})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['error']['code'], 'RECOVERY_REQUIRED')
        self.assertEqual(self.get(), before)  # no attempts, usage, replay or outbox effects

    def test_allocator_failure_has_control_http_envelope_and_rolls_back(self):
        self.record.run.status = 'queued'
        self.record.run.stages[0].status = 'pending'
        self.record.events = [{'seq': 1}, {'seq': 1}]
        self.save()
        before = self.get()
        response = self.client().post(f'/api/v1/runs/{self.id}/cancel')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['error']['code'], 'RUN_STATE_CONFLICT')
        self.assertEqual(self.get(), before)

    def prepare_between_stages(self, cause):
        from copy import deepcopy
        from app.services.run_control.models import Checkpoint
        self.record.run.stop_reason = cause
        self.record.run.stages[0].status = 'succeeded'
        self.record.run.metrics.attempts = 1
        self.record.attempts[0].accepted_result = dict(run_id=str(self.id),stage_id='analysis',
            attempt=1,contract_version='1',status='succeeded')
        aid = str(self.record.run.revision_id)
        def task(name, deps, output):
            return dict(task_id=name,stage_id=name,owner_agent=name,depends_on=deps,
                input_artifact_ids=[aid],output_paths=[output],output_directories=[],related_fr_ids=[])
        request = dict(schema_version='1.0',run_id=str(self.id),stage_id='backend',attempt=1,
            contract_version='1',profile_id='web-fastapi-angular',input_artifact_ids=[aid],
            limits=dict(max_calls=1,max_output_tokens=50))
        self.record.saved_state.update(generation_plan=dict(schema_version='1.0',contract_version='1',
            profile_id='web-fastapi-angular',tasks=[task('analysis',[],'requirements.json'),
                                                  task('backend',['analysis'],'backend/app/api/routes.py')]),
            pending_inputs={'backend':dict(input_state=request,input_hash='b'*64)},reservations={'calls':1})
        if cause == 'MAX_OUTPUT_TOKENS':
            self.record.run.metrics.model_calls=2;self.record.run.metrics.tokens_out=100
            self.record.required_limits=Limits(max_calls=5,max_output_tokens=120)
        self.record.checkpoint=Checkpoint(run_id=self.id,revision_id=self.record.run.revision_id,
            contract_version='1',input_hash='a'*64,state_version=self.record.state_version,
            state=deepcopy(self.record.saved_state))

    def test_supported_budget_between_stages_continues_once(self):
        for cause in ('MAX_CALLS','MAX_OUTPUT_TOKENS'):
            with self.subTest(cause=cause):
                # New independent durable local aggregate for each scenario.
                self.setUp(); self.prepare_between_stages(cause); self.save(); before=self.get()
                wrong = dict(max_calls=5,max_output_tokens=200) if cause=='MAX_CALLS' else dict(max_calls=10,max_output_tokens=100)
                with self.assertRaises(ControlError) as error:self.retry(**wrong)
                self.assertEqual(error.exception.status,409);self.assertEqual(self.get(),before)
                limits=dict(max_calls=6,max_output_tokens=100) if cause=='MAX_CALLS' else dict(max_calls=5,max_output_tokens=120)
                with ThreadPoolExecutor(max_workers=4) as pool:
                    replies=list(pool.map(lambda _:self.retry(**limits),range(8)))
                after=self.get()
                self.assertTrue(all(reply==replies[0] for reply in replies))
                self.assertEqual([(a.stage_id,a.status,a.attempt_no) for a in after.run.stages],
                    [('analysis','succeeded',1),('backend','pending',1)])
                self.assertEqual(after.attempts[0],before.attempts[0])
                self.assertEqual(after.attempts[1].input_hash,'b'*64)
                self.assertEqual(after.saved_state,before.saved_state)
                self.assertEqual(after.run.metrics.model_calls,before.run.metrics.model_calls)
                self.assertEqual(after.run.metrics.tokens_out,before.run.metrics.tokens_out)
                self.assertEqual(len(after.outbox),1)
                self.assertFalse(any(e['event_type']=='retry.scheduled' for e in after.events))
                self.assertEqual(self.retry(**limits),replies[0]);self.assertEqual(self.get(),after)

    def test_between_stages_requires_frozen_inputs_and_confirmed_dependencies(self):
        for defect in ('no_plan','no_inputs','unconfirmed','changed_context'):
            with self.subTest(defect=defect):
                self.setUp();self.prepare_between_stages('MAX_CALLS')
                if defect=='no_plan':self.record.saved_state.pop('generation_plan')
                if defect=='no_inputs':self.record.saved_state.pop('pending_inputs')
                if defect=='unconfirmed':self.record.checkpoint=None
                if defect=='changed_context':self.record.saved_state['pending_inputs']['backend']['input_state']['run_id']=str(uuid4())
                self.save();before=self.get()
                with self.assertRaises(ControlError) as error:self.retry()
                self.assertEqual(error.exception.code,'RESUME_STATE_UNAVAILABLE')
                self.assertEqual(self.get(),before)

    def test_output_exhaustion_requires_output_increase(self):
        self.record.run.stop_reason='MAX_OUTPUT_TOKENS'
        self.record.run.metrics.model_calls=2
        self.record.run.metrics.tokens_out=100
        self.record.required_limits=Limits(max_calls=5,max_output_tokens=120)
        self.save(); before=self.get()
        with self.assertRaises(ControlError):self.retry(max_calls=10,max_output_tokens=100)
        self.assertEqual(self.get(),before)
        response=self.retry(max_calls=5,max_output_tokens=120)
        self.assertEqual(response.metrics.tokens_out,100)
        self.assertEqual(response.status,'queued')

    def test_wrong_limit_and_policy_denial_rollback(self):
        self.save(); before=self.get()
        with self.assertRaises(ControlError):self.retry(max_calls=5,max_output_tokens=200)
        self.assertEqual(self.get(),before)
        def denied(*_): raise ControlError(403,'FORBIDDEN','No budget authority')
        self.service.authorize_retry=denied
        with self.assertRaises(ControlError):self.retry()
        self.assertEqual(self.get(),before)

    def test_concurrent_cancel_and_repeat_do_not_duplicate_events(self):
        self.record.run.status='queued';self.record.run.stages[0].status='pending';self.save()
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _:self.service.cancel(owner_id=self.owner,run_id=self.id),range(16)))
        after=self.get();seqs=[e['seq'] for e in after.events]
        self.assertEqual(seqs,list(range(1,len(seqs)+1)))
        self.assertEqual(after.run.status,'cancelled');self.assertEqual(len(after.outbox),1)
        self.service.cancel(owner_id=self.owner,run_id=self.id);self.assertEqual(self.get(),after)

    def test_event_allocation_uses_watermark_not_list_length(self):
        event=next_event(run_id=self.id,events=[{'seq':7}],event_type='metrics.updated',data={})
        self.assertEqual(event.seq,8)
        with self.assertRaises(EventSequenceError):
            next_event(run_id=self.id,events=[{'seq':7},{'seq':7}],event_type='metrics.updated',data={})


class LifecycleCheckpointTests(unittest.TestCase):
    def test_completion_requires_confirmation_and_preserves_accepted_state(self):
        with TemporaryDirectory() as directory:
            repo=SQLiteLifecycleRepository(Path(directory)/'lifecycle.db')
            owner,run_id=uuid4(),uuid4()
            attempt=Attempt(stage_id='analysis',attempt_no=1,contract_version='1',input_hash='a'*64,status='succeeded')
            record=RunRecord(run_id=run_id,owner_id=owner,status='running',context={'profile_id':'web-fastapi-angular'},
                attempts=[attempt],data={'accepted':['artifact'],'usage':{'calls':1},'checkpoint_intent':True})
            repo.insert(record)
            service=LifecycleService(repo,guard=lambda *_:None)
            with self.assertRaises(LifecycleError) as error:
                with service.transaction(owner_id=owner,run_id=run_id,expected_version=1) as tx:
                    tx.transition_run(expected='running',target='completed',reason_code='ASSEMBLED')
            self.assertEqual(error.exception.code,'CHECKPOINT_REQUIRED')
            self.assertEqual(service.get(owner_id=owner,run_id=run_id),record)
            with service.transaction(owner_id=owner,run_id=run_id,expected_version=1) as tx:
                tx.transition_run(expected='running',target='failed',reason_code='CHECKPOINT_OUTAGE')
            with service.transaction(owner_id=owner,run_id=run_id,expected_version=2) as tx:
                tx.transition_run(expected='failed',target='queued',reason_code='RECOVER_CHECKPOINT')
                tx.transition_run(expected='queued',target='running',reason_code='RECOVERY_ADMITTED')
            service=LifecycleService(repo,guard=lambda *_:None,checkpoint_confirmed=lambda r,a:a.attempt_id==attempt.attempt_id)
            with service.transaction(owner_id=owner,run_id=run_id,expected_version=3) as tx:
                tx.transition_run(expected='running',target='completed',reason_code='ASSEMBLED')
            saved=service.get(owner_id=owner,run_id=run_id)
            self.assertEqual(saved.attempts,[attempt]);self.assertEqual(saved.data,record.data)
            self.assertEqual(saved.status,'completed')

    def test_allocator_failure_is_mapped_to_lifecycle_error(self):
        with TemporaryDirectory() as directory:
            repo = SQLiteLifecycleRepository(Path(directory) / 'lifecycle.db')
            service = LifecycleService(repo, guard=lambda *_: None)
            owner, run_id = uuid4(), uuid4()
            service.create(owner_id=owner, run_id=run_id,
                context={'profile_id':'web-fastapi-angular','revision_id':str(uuid4())}, data={})
            with repo.transaction(owner_id=owner, run_id=run_id) as record:
                record.last_seq += 1
            before = service.get(owner_id=owner, run_id=run_id)
            with self.assertRaises(LifecycleError) as error:
                with service.transaction(owner_id=owner, run_id=run_id, expected_version=before.version) as tx:
                    tx.publish(event_type='metrics.updated', data={})
            self.assertEqual(error.exception.code, 'RUN_STATE_CONFLICT')
            self.assertEqual(service.get(owner_id=owner, run_id=run_id), before)

    def test_concurrent_events_and_failed_transaction(self):
        with TemporaryDirectory() as directory:
            repo=SQLiteLifecycleRepository(Path(directory)/'lifecycle.db')
            service=LifecycleService(repo,guard=lambda *_:None)
            owner,run_id=uuid4(),uuid4()
            record=service.create(owner_id=owner,run_id=run_id,context={'profile_id':'web-fastapi-angular','revision_id':str(uuid4())},data={})
            def event(_):
                # repository serializes authoritative sequence allocation.
                with repo.transaction(owner_id=owner,run_id=run_id) as r:
                    from app.services.lifecycle.service import LifecycleTransaction
                    tx=LifecycleTransaction(r,lambda *_:None)
                    tx.publish(event_type='metrics.updated',data={})
            with ThreadPoolExecutor(max_workers=8) as pool:list(pool.map(event,range(16)))
            before=service.get(owner_id=owner,run_id=run_id)
            self.assertEqual([e.seq for e in before.events],list(range(1,19)))
            with self.assertRaises(RuntimeError):
                with service.transaction(owner_id=owner,run_id=run_id,expected_version=before.version) as tx:
                    tx.publish(event_type='metrics.updated',data={});raise RuntimeError('outage')
            self.assertEqual(service.get(owner_id=owner,run_id=run_id),before)
