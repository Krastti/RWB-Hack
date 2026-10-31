from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import select
from test_runtime_recovery_postgresql import db, prepare_recovery
from test_answers_review import question
from app.models import Run, StageAttempt, Project, RunEvent
from app.adapters.queue.models import WorkerCommand
from app.models.clarification import Clarification, AnswerReplay, ClarificationInput
from app.services.run_control.models import ControlError, Retry, Limits
from app.services.runtime import compose_runtime
from app.api.answers import Answers
from app.api.answers.service import AnswersService


def waiting(db):
    rt, _, run_id, attempt_id, command_id = prepare_recovery(db)
    q=question()
    with db.begin() as s:
        row=s.get(Run,run_id)
        row.status='waiting_for_input'
        row.questions_revision=1
        row.questions=[q]
        owner=s.get(Project,row.project_id).owner_id
        s.get(StageAttempt,attempt_id).status='waiting_for_input'
        s.delete(s.get(WorkerCommand,command_id))
    body=Answers(questions_revision=1,answers=[{'question_id':q['question_id'],'value':'yes'}])
    return rt,run_id,owner,body


@pytest.mark.parametrize('other',['cancel','retry','same_key','different_key'])
def test_concurrent_answers_commands(db,other):
    rt,run_id,owner,body=waiting(db)
    barrier=Barrier(2)
    def call(which):
        barrier.wait(timeout=5)
        try:
            if which==0:
                return AnswersService(rt).accept(owner_id=owner,run_id=run_id,key='one',body=body)
            if other=='cancel':
                return rt.control.cancel(owner_id=owner,run_id=run_id)
            if other=='retry':
                return rt.control.retry(owner_id=owner,run_id=run_id,key='retry',body=Retry())
            return AnswersService(rt).accept(owner_id=owner,run_id=run_id,key='one' if other=='same_key' else 'two',body=body)
        except ControlError as exc:
            assert exc.status==409
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(call,i) for i in range(2)]
        outcomes=[f.result(timeout=15) for f in futures]
    with db() as s:
        attempts=list(s.scalars(select(StageAttempt).where(StageAttempt.run_id==run_id)))
        assert len(attempts)<=2
        events=list(s.scalars(select(RunEvent).where(RunEvent.run_id==run_id).order_by(RunEvent.seq)))
        assert [e.seq for e in events]==list(range(1,len(events)+1))
        accepted=sum(e.event_type=='answers.accepted' for e in events)
        assert accepted<=1
        assert len(list(s.scalars(select(ClarificationInput).where(ClarificationInput.run_id==run_id))))==accepted
        if other=='cancel':
            assert s.get(Run,run_id).status=='cancelled'
        else:
            assert accepted==1
            assert s.get(Run,run_id).status=='running'
            assert len(attempts)==2
            assert list(s.scalars(select(Clarification).where(Clarification.run_id==run_id)))[0].answer=='yes'
    if other=='same_key':
        # A concurrent in-progress response is retryable through durable replay.
        replay=AnswersService(rt).accept(owner_id=owner,run_id=run_id,key='one',body=body)
        assert replay.status=='running'


def test_answers_enqueue_failure_rolls_back_then_restart_replays(db,monkeypatch):
    rt,run_id,owner,body=waiting(db)
    enqueue=rt.queue.enqueue
    def fail(s,attempt):
        enqueue(s,attempt)
        raise RuntimeError('injected enqueue fault')
    monkeypatch.setattr(rt.queue,'enqueue',fail)
    with pytest.raises(RuntimeError,match='injected enqueue fault'):
        AnswersService(rt).accept(owner_id=owner,run_id=run_id,key='one',body=body)
    with db() as s:
        assert s.get(Run,run_id).status=='waiting_for_input'
        assert len(list(s.scalars(select(StageAttempt).where(StageAttempt.run_id==run_id))))==1
        assert not list(s.scalars(select(AnswerReplay)))
        assert not list(s.scalars(select(ClarificationInput)))
        assert not list(s.scalars(select(WorkerCommand)))
        assert not list(s.scalars(select(Clarification)))
    fresh=compose_runtime(db,retry_limits=Limits(max_calls=10,max_output_tokens=1000))
    result=AnswersService(fresh).accept(owner_id=owner,run_id=run_id,key='one',body=body)
    restarted=compose_runtime(db,retry_limits=Limits(max_calls=10,max_output_tokens=1000))
    assert AnswersService(restarted).accept(owner_id=owner,run_id=run_id,key='one',body=body)==result
    with db() as s:
        command=s.scalar(select(WorkerCommand.id))
    lease=restarted.queue.claim(command)
    assert lease.command.attempt_no==2 and not lease.recovery
