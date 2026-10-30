"""Local control-plane example; no model calls or generated business responses."""
from tempfile import TemporaryDirectory
from pathlib import Path
from uuid import uuid4

from . import AttemptState, Limits, Record, Retry, Run, RunControlService, SQLiteControlStore, StageAttempt


def main():
    owner_id, run_id, revision_id = uuid4(), uuid4(), uuid4()
    with TemporaryDirectory() as directory:
        store = SQLiteControlStore(Path(directory) / "control.sqlite3")
        run = Run(run_id=run_id, project_id=uuid4(), revision_id=revision_id,
                  status="failed", limits=Limits(max_calls=5, max_output_tokens=5000),
                  current_stage="backend-orders", stages=[StageAttempt(
                      stage_id="backend-orders", attempt_no=1, contract_version="1", status="failed")])
        run.metrics.attempts = 1
        # Control fixture only. In production B-05/A-04 supply the complete
        # immutable inputs and graph state with references to real artifacts.
        store.create(Record(owner_id=owner_id, run=run,
                            saved_state={"revision_id": str(revision_id), "artifact_ids": []},
                            attempts=[AttemptState(stage_id="backend-orders", attempt_no=1,
                                                   input_hash="example-input-hash",
                                                   input_state={"revision_id": str(revision_id)})]))

        def local_policy(record, limits):
            # Only this fixture exists; production injects owner/active-run policy.
            assert record.owner_id == owner_id

        service = RunControlService(store, authorize_retry=local_policy)
        response = service.retry(owner_id=owner_id, run_id=run_id, key="example-retry", body=Retry())
        print("retry:", response.status, response.stages[-1].attempt_no)
        # Simulate API restart: replay is persisted, not an extra attempt.
        service = RunControlService(SQLiteControlStore(store.path), authorize_retry=local_policy)
        replay = service.retry(owner_id=owner_id, run_id=run_id, key="example-retry", body=Retry())
        print("replay:", replay.status, replay.stages[-1].attempt_no)
        cancelled = service.cancel(owner_id=owner_id, run_id=run_id)
        print("cancel before worker:", cancelled.status, cancelled.stages[-1].status)


if __name__ == "__main__":
    main()
