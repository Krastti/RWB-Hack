"""Local control-plane example; no mock business logic or provider calls."""
from pathlib import Path
from hashlib import sha256
from tempfile import TemporaryDirectory
from uuid import uuid4

from . import Change, LifecycleError, LifecycleService, RunRecord, SQLiteLifecycleRepository


def local_guard(record: RunRecord, change: Change) -> None:
    """Only the paths exercised by this control fixture are admitted.

    Real integration supplies guards from B-05/B-08/B-09/B-10/B-12/A-04,
    using actual authorization/lease/artifacts/questions/limits/snapshot state.
    """
    if change.kind == "create" and record.data.get("outbox"):
        return
    if change.kind == "register_attempt" and record.status == "queued":
        return
    if change.kind == "run" and (change.source, change.target) in {
        ("queued", "running"), ("running", "cancelling"), ("cancelling", "cancelled"),
    }:
        return
    if change.kind == "attempt" and change.target in {"running", "cancelled"}:
        return
    raise LifecycleError(409, "RUN_STATE_CONFLICT", "Operation is not admitted by the local example")


def main():
    with TemporaryDirectory() as directory:
        repository = SQLiteLifecycleRepository(Path(directory) / "lifecycle.db")
        service = LifecycleService(repository, guard=local_guard)
        owner_id, run_id = uuid4(), uuid4()
        record = service.create(owner_id=owner_id, run_id=run_id,
                                context={"revision_id": str(uuid4()), "profile_id": "web-fastapi-angular"},
                                data={"outbox": [{"id": str(uuid4()), "kind": "start"}]})
        with service.transaction(owner_id=owner_id, run_id=run_id, expected_version=record.version) as tx:
            attempt = tx.register_attempt(stage_id="backend-orders", contract_version="1",
                                          input_hash=sha256(b"example-immutable-input").hexdigest(),
                                          reason_code="INITIAL_ATTEMPT")
            tx.transition_run(expected="queued", target="running", reason_code="WORKER_ADMITTED")
            attempt = tx.transition_attempt(attempt_id=attempt.attempt_id, expected="pending", target="running",
                                            generation=0, reason_code="LEASE_ACQUIRED")
        record = service.get(owner_id=owner_id, run_id=run_id)
        try:
            with service.transaction(owner_id=owner_id, run_id=run_id, expected_version=record.version) as tx:
                tx.transition_run(expected="running", target="queued", reason_code="INVALID_TRANSITION")
        except LifecycleError as exc:
            print("forbidden transition:", exc.status, exc.code)
        with service.transaction(owner_id=owner_id, run_id=run_id, expected_version=record.version) as tx:
            tx.transition_run(expected="running", target="cancelling", reason_code="USER_CANCEL")
            tx.update_data(lambda data: data.update(partial_state={"accepted_artifact_ids": []}))
            tx.transition_attempt(attempt_id=attempt.attempt_id, expected="running", target="cancelled",
                                  generation=attempt.generation, reason_code="SAFE_BOUNDARY")
            tx.transition_run(expected="cancelling", target="cancelled", reason_code="SAFE_BOUNDARY")
        # Same file, new service instance: committed seq is not reset by restart.
        service = LifecycleService(SQLiteLifecycleRepository(repository.path), guard=local_guard)
        record = service.get(owner_id=owner_id, run_id=run_id)
        page = service.events(owner_id=owner_id, run_id=run_id)
        print("saved status:", record.status, "version:", record.version)
        print("saved seq:", [event.seq for event in page.items])


if __name__ == "__main__":
    main()
