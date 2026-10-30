"""Atomic answer package acceptance; no model/network calls under SQL locks."""
from copy import deepcopy
from hashlib import sha256
import json
from uuid import uuid4

from sqlalchemy import select, func

from ...models import Project, Run as RunRow, User
from ...models.clarification import AnswerReplay, Clarification, ClarificationInput
from ...services.run_control.models import AttemptState, ControlError, Run, StageAttempt, now


def persist_questions(session, run):
    """Producer hook: call under Run lock for EACH published revision, before commit."""
    if run.questions_revision < 1:
        raise ControlError(409, "RUN_STATE_CONFLICT", "No question revision published")
    for question in run.questions:
        from uuid import UUID
        identity = (run.id, run.questions_revision, UUID(str(question["question_id"])))
        confirmed = session.scalars(select(Clarification).where(
            Clarification.run_id == run.id, Clarification.question_id == identity[2],
            Clarification.answer.is_not(None))).all()
        if any(old.answer != question.get("answer") for old in confirmed):
            raise ControlError(409, "RUN_STATE_CONFLICT", "Confirmed answer must survive question revisions")
        row = session.get(Clarification, identity)
        fields = {k: deepcopy(question[k]) for k in ("question", "reason", "related_fr_ids", "required")}
        if row is None:
            row = Clarification(run_id=run.id, questions_revision=run.questions_revision,
                                question_id=identity[2], **fields, answer=question.get("answer"))
            session.add(row)
        else:
            if any(getattr(row, k) != v for k, v in fields.items()):
                raise ControlError(409, "RUN_STATE_CONFLICT", "Published question definition changed")
            answer = question.get("answer")
            if row.answer is not None and row.answer != answer:
                raise ControlError(409, "RUN_STATE_CONFLICT", "Confirmed answer changed")
            row.answer = answer


class AnswersService:
    def __init__(self, runtime):
        self.runtime = runtime

    def accept(self, *, owner_id, run_id, key, body):
        normalized = body.model_dump(mode="json")
        normalized["answers"].sort(key=lambda a: a["question_id"])
        digest = sha256(json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        repo = self.runtime.repository
        with repo.sessions.begin() as session:
            owned = session.scalar(select(RunRow.id).join(Project).where(
                RunRow.id == run_id, Project.owner_id == owner_id))
            if owned is None:
                raise ControlError(404, "NOT_FOUND", "Run not found")
            scope = f"answerQuestions:{owner_id}:{run_id}:{key}"
            if not session.scalar(select(func.pg_try_advisory_xact_lock(func.hashtextextended(scope, 0)))):
                raise ControlError(409, "IDEMPOTENCY_IN_PROGRESS", "Answer operation is in progress")
            user = session.scalar(select(User).where(User.id == owner_id).with_for_update())
            if user is None or user.status != "active":
                raise ControlError(401, "UNAUTHORIZED", "Authentication required")
            row = session.scalar(select(RunRow).where(RunRow.id == run_id).with_for_update())
            replay = session.get(AnswerReplay, (run_id, key))
            if replay is not None:
                if replay.request_hash != digest:
                    raise ControlError(409, "IDEMPOTENCY_KEY_CONFLICT", "Key was used with a different package")
                return Run.model_validate(replay.response)
            control = repo.control_in(session, row)
            with control._transaction(owner_id, run_id) as record:
                run = record.run
                control._profile(record)
                if run.status != "waiting_for_input":
                    raise ControlError(409, "RUN_STATE_CONFLICT", "Run is not waiting for input")
                if body.questions_revision != run.questions_revision:
                    raise ControlError(409, "STALE_QUESTIONS_REVISION", "Question revision is stale")
                questions = {q.question_id: q for q in run.questions}
                submitted = {a.question_id: a.value for a in body.answers}
                if (not submitted.keys() <= questions.keys() or any(
                        q.required and q.answer is None and q.question_id not in submitted for q in run.questions)):
                    raise ControlError(422, "VALIDATION_ERROR", "Unknown question or incomplete required package")
                if any(questions[q].answer is not None and questions[q].answer != v for q, v in submitted.items()):
                    raise ControlError(422, "VALIDATION_ERROR", "Confirmed answers cannot be replaced")
                stage = control._latest(record, run.current_stage or "analysis")
                source = control._private(record, stage)
                if stage.status != "waiting_for_input" or any(a.status in {"pending", "running"} for a in run.stages):
                    raise ControlError(409, "RUN_STATE_CONFLICT", "Unresolved execution prevents answers")
                if not source.input_state:
                    raise ControlError(500, "RESUME_STATE_UNAVAILABLE", "Saved input is unavailable")
                persist_questions(session, row)
                changed = []
                for qid, value in submitted.items():
                    if questions[qid].answer is None:
                        changed.append(str(qid))
                    questions[qid].answer = value
                input_id = uuid4()
                payload = {"schema_version": "clarification-input/1", "run_id": str(run_id),
                           "revision_id": str(run.revision_id), "questions_revision": run.questions_revision,
                           "previous_input": deepcopy(source.input_state),
                           "answers": [{"question_id": str(q.question_id), "value": q.answer}
                                       for q in run.questions if q.answer is not None]}
                session.add(ClarificationInput(id=input_id, run_id=run_id, payload=payload))
                input_hash = sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                private = AttemptState(stage_id=stage.stage_id, attempt_no=stage.attempt_no+1,
                                       input_hash=input_hash, input_state={"input_artifact_id": str(input_id), **payload})
                attempt = StageAttempt(stage_id=stage.stage_id, attempt_no=private.attempt_no,
                                       contract_version=stage.contract_version, status="pending")
                record.attempts.append(private)
                run.stages.append(attempt)
                run.metrics.attempts += 1
                run.metrics.manual_clarifications += len(changed)
                record.saved_state["accepted_answers"] = deepcopy(payload)
                record.state_version += 1
                control._stage_event(record, attempt, None)
                control._event(record, "answers.accepted", questions_revision=run.questions_revision,
                               answered_question_ids=changed, input_artifact_id=str(input_id))
                control._status(record, "running")
                control._event(record, "metrics.updated", metrics=run.metrics.model_dump(),
                               tokens_complete=record.tokens_complete)
                run.updated_at = now()
                response = run.model_copy(deep=True)
                session.add(AnswerReplay(run_id=run_id, key=key, request_hash=digest,
                                         response=response.model_dump(mode="json")))
            # bound store has flushed the attempt, all writes still share this transaction.
            self.runtime.queue.enqueue(session, private.attempt_id)
            persist_questions(session, row)
            return response
