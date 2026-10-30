from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_runtime_recovery_postgresql import db, prepare_recovery
from app.models import Run
from app.models.clarification import Clarification
from app.api.answers.service import persist_questions
from app.services.run_control.models import ControlError


def question():
    return {"question_id":str(uuid4()),"question":"Choose?","reason":"Need choice","related_fr_ids":["FR-1"],"required":True,"answer":None}


@pytest.mark.parametrize('change',["remove_confirmed","change_confirmed","replace_id","duplicate"])
def test_question_publication_rejects_loss_and_identity_changes(db, change):
    _, _, run_id, _, _ = prepare_recovery(db)
    original=question()
    if change in {"remove_confirmed","change_confirmed"}:
        original['answer']='yes'
    with db.begin() as s:
        row=s.get(Run,run_id)
        row.questions_revision=1
        row.questions=[original]
        persist_questions(s,row)
    with pytest.raises(ControlError) as exc:
        with db.begin() as s:
            row=s.scalar(select(Run).where(Run.id==run_id).with_for_update())
            new=deepcopy(original)
            if change=='remove_confirmed':
                row.questions_revision=2
                row.questions=[]
            elif change=='change_confirmed':
                row.questions_revision=2
                new['question']='Different?'
                row.questions=[new]
            elif change=='replace_id':
                new['question_id']=str(uuid4())
                row.questions=[new]
            else:
                row.questions=[new,new]
            persist_questions(s,row)
    assert exc.value.code==('VALIDATION_ERROR' if change=='duplicate' else 'RUN_STATE_CONFLICT')
    with db() as s:
        assert s.get(Run,run_id).questions==[original]
        assert len(list(s.scalars(select(Clarification).where(Clarification.run_id==run_id))))==1


def test_answer_snapshot_duration_order_and_replay(db, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from app.models import StageAttempt, Project, RunEvent
    from app.api.answers import Answers
    from app.api.answers.service import AnswersService
    rt, _, run_id, attempt_id, _=prepare_recovery(db)
    q=question()
    clock=datetime(2026,10,31,12,tzinfo=timezone.utc)
    with db.begin() as s:
        row=s.get(Run,run_id)
        row.status='waiting_for_input'
        row.created_at=clock-timedelta(seconds=123)
        row.questions_revision=1
        row.questions=[q]
        owner=s.get(Project,row.project_id).owner_id
        s.get(StageAttempt,attempt_id).status='waiting_for_input'
        s.add(StageAttempt(id=uuid4(),run_id=run_id,stage_id='zz',attempt_no=1,contract_version='1.0',input_hash='b'*64,status='succeeded'))
    monkeypatch.setattr('app.api.answers.service.now',lambda:clock)
    service=AnswersService(rt)
    body=Answers(questions_revision=1,answers=[{'question_id':q['question_id'],'value':'yes'}])
    response=service.accept(owner_id=owner,run_id=run_id,key='answer1',body=body)
    assert response.metrics.duration_ms==123000
    assert [(a.stage_id,a.attempt_no) for a in response.stages]==[('analysis',1),('analysis',2),('zz',1)]
    assert service.accept(owner_id=owner,run_id=run_id,key='answer1',body=body)==response
    with db() as s:
        metrics=list(s.scalars(select(RunEvent).where(RunEvent.run_id==run_id,RunEvent.event_type=='metrics.updated').order_by(RunEvent.seq)))[-1]
        assert metrics.data['metrics']['duration_ms']==123000
