"""Drain durable createRun jobs into initial attempts and stage commands."""
from copy import deepcopy
from hashlib import sha256
import json
import logging

from sqlalchemy import select

from ..models import Run
from ..models.run_creation import RunQueueJob
from ..services.run_control.models import AttemptState


logger = logging.getLogger(__name__)


class RunIntakeDispatcher:
    def __init__(self, sessions, repository, states, queue):
        self.sessions, self.repository, self.states = sessions, repository, states
        self.queue = queue

    def dispatch(self, limit=100):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("Invalid intake limit")
        with self.sessions() as session:
            jobs = session.execute(select(RunQueueJob.id, RunQueueJob.run_id).order_by(
                RunQueueJob.created_at, RunQueueJob.id).limit(limit)).all()
        accepted = failed = 0
        for job_id, run_id in jobs:
            try:
                with self.sessions.begin() as session:
                    # Same lock order as queue claim/control: Run before job/command.
                    run = session.scalar(select(Run).where(Run.id == run_id).with_for_update(skip_locked=True))
                    if run is None:
                        continue
                    job = session.scalar(select(RunQueueJob).where(
                        RunQueueJob.id == job_id).with_for_update(skip_locked=True))
                    if job is None:
                        continue
                    if run.status == "queued":
                        record, _ = self.repository.load(session, run)
                        if not record.run.stages:
                            self._register(session, run)
                        else:
                            # A committed registration never needs another attempt.
                            for source in record.attempts:
                                stage = next(a for a in record.run.stages if
                                             (a.stage_id, a.attempt_no) == (source.stage_id, source.attempt_no))
                                if stage.status == "pending":
                                    self.queue.enqueue(session, source.attempt_id)
                    session.delete(job)
                    accepted += 1
            except Exception:
                logger.exception("Run intake failed: run_id=%s job_id=%s", run_id, job_id)
                failed += 1
        return {"accepted": accepted, "failed": failed}

    def _register(self, session, run):
        frozen = self.repository.frozen_input(session, run)
        # This is private intake context, not a fabricated C-02 artifact/AgentInput.
        # The AG bridge must resolve real artifacts before invoking the agent.
        identity = {k: v for k, v in frozen.items() if k != "limits"}
        digest = sha256(json.dumps(identity, sort_keys=True, separators=(",", ":"),
                                   ensure_ascii=True, allow_nan=False).encode()).hexdigest()
        source = AttemptState(stage_id="analysis", attempt_no=1, input_hash=digest,
                              input_state={"schema_version": "runtime-intake/1", **deepcopy(frozen)})
        with self.states.transaction(session, run) as tx:
            def save_input(data):
                data["attempts"].append(source.model_dump(mode="json"))
                data["saved_state"] = {"intake": deepcopy(source.input_state)}
                data["run"]["metrics"]["attempts"] += 1
                data["run"]["current_stage"] = "analysis"
            tx.update_data(save_input)
            tx.register_attempt(stage_id="analysis", contract_version=frozen["contracts"]["agents"],
                                input_hash=digest, attempt_id=source.attempt_id, reason_code="RUN_ACCEPTED")
            tx.publish(event_type="metrics.updated", data={"metrics": tx.snapshot.data["run"]["metrics"],
                       "tokens_complete": tx.snapshot.data["tokens_complete"]})
        self.queue.enqueue(session, source.attempt_id)
