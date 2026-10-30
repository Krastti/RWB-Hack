# Persistence interface (B-05)

Contract baseline: [C-01 v1.0.0](../../../contracts/platform.openapi.yaml),
[C-03 v1.0](../../../contracts/lifecycle.md), accepted in main
`ef1956c958ef64c8784ce0558c53269bea25142f`. Public imports:

```python
from backend.app.models import (
    Base, User, Project, RequirementRevision,
    Run, StageAttempt, RunEvent, IdempotencyRecord,
)
```

Flyway owns schema changes. Apply `backend/migrations/V1__*.sql`, then
`V2__create_runs_attempts_events_idempotency.sql` using `backend/flyway.conf`.
V1 remains unchanged. V2 adds a composite unique key to requirement revisions
so the run's revision must belong to its project. All references use RESTRICT:
removing a project/user/run must not silently remove history or replay records.

## Rows and API mapping

- `Run.id` maps to API `run_id`; all other scalar names match C-01. `project`
  exposes the owner through `Project.owner_id`. `revision` is a read-only
  relationship: set `revision_id` and `project_id` when writing. `profile_id`
  stores a nonempty identifier; the API/service validates the permitted profile
  against the current contract, allowing new profiles without a DB migration.
- `Run.stage_attempts` stores the complete history ordered by stage_id, attempt_no.
  API `stages` serializes ALL attempts in that order (C-03 §3); the maximum
  attempt_no is used only for the current UI state of each logical stage.
  `StageAttempt.id` is the internal UUID used by future artifact references;
  `attempt_no` starts at 1 within each `(run_id, stage_id)`.
- `Run.events` orders by seq. `RunEvent` has composite primary key `(run_id, seq)`;
  seq starts at 1 and is BIGINT. Event `data` is the public C-03 payload, including
  schema_version. Never persist provider secrets or private diagnostics here.
- `Run.questions_revision` / `questions` and `metrics` are persisted read projections
  for C-01. Questions start at revision 0 with an empty array; metrics start at
  six zero counters. Writers must validate their full DTO shape and replace JSON
  values rather than mutating nested dicts/lists in place (SQLAlchemy does not track
  nested changes). These projections must be updated atomically with source facts
  and events; this module does not implement ModelCall/Clarification accounting.
- `stopped_at` is an internal nullable timestamp: writers set it on failed,
  budget_exceeded, completed, cancelled and clear it on retry. It supports elapsed
  duration freezing; updated_at is not a substitute for the stop timestamp.
- Optional current_stage, stop_reason, attempt times/reason/error and event stage_id
  use SQL NULL when absent; serializers omit absent optional API properties.
  `error` stores the complete C-01 Error schema, including its outer `error`
  property: `{"error":{"code":"...","message":"...","details":{},"request_id":"..."}}`.

## Atomic write example

This example assumes an existing project and revision, a caller-supplied SHA-256
input hash, a normalized request hash and a durable response_snapshot_ref from the
response store; it does not enqueue work:

```python
from uuid import uuid4
from sqlalchemy.orm import Session

run_id = uuid4()
with Session(engine) as session, session.begin():
    run = Run(
        id=run_id, project_id=project_id, revision_id=revision_id,
        profile_id="web-fastapi-react",
        limits={"max_calls": 20, "max_output_tokens": 10000},
        metrics={"tokens_in": 0, "tokens_out": 0, "model_calls": 0,
                 "attempts": 1, "manual_clarifications": 0, "duration_ms": 0},
    )
    session.add(run)
    session.flush()
    session.add_all([
        StageAttempt(
            run_id=run_id, stage_id="analysis", attempt_no=1,
            contract_version="1", input_hash=input_hash,
        ),
        RunEvent(
            run_id=run_id, seq=1, event_type="run.created",
            data={"schema_version": "1.0", "status": "queued",
                  "project_id": str(project_id), "revision_id": str(revision_id),
                  "profile_id": "web-fastapi-react"},
        ),
        IdempotencyRecord(
            owner_id=owner_id, operation="createRun", resource_id=project_id,
            key=idempotency_key, request_hash=request_hash,
            response_ref=response_snapshot_ref,
        ),
    ])
```

Idempotency scope is `(owner_id, operation, resource_id, key)`, exactly C-01:
`createRun` uses the project UUID, `answerQuestions`/`retryRun`/`createExport`
use the run UUID. The operation is the OpenAPI operationId. `response_ref` is an
opaque durable reference to the original response (status/body), which the service
must resolve for replay after authorizing access again. Key/request_hash never
belong in event data or logs. The SHA-256 request hash covers the normalized body
and scope as agreed by the service; this module does not canonicalize input.
Response snapshot storage is owned by the write service; referencing a mutable
Run GET response does not provide exact replay of the original response. Records
represent successful committed operations and have no TTL; request validation
errors reserve nothing. Concurrent scope conflicts need service-level handling of
IntegrityError/locking to provide C-01 409 responses; no placeholder commits here.

For subsequent writes, lock the Run row with `SELECT ... FOR UPDATE` before
allocating `max(seq)+1` or `max(attempt_no)+1`. All cooperating writers must use the
same lock, insert events/attempts and update run/projections in one transaction.
Unique constraints are the final duplicate guard; they do not themselves allocate
numbers or guarantee monotonic writes. After rollback retry the whole operation.
State transition guards, immutable accepted inputs/history, ownership checks,
idempotent replay and reliable queue intent belong to B-06/B-07/B-10/B-12/A-04.
The schema supplies storage and structural checks, not a functioning pipeline.

## Migration and rollback

V2 is additive. Old rows in V1 are preserved. Before adoption, rollback can stop
new writers and drop idempotency_records, run_events, stage_attempts, runs, then
`uq_requirement_revisions_id_project`. After data exists this destroys history:
prefer stopping writers and restoring a backup or an approved forward migration.
Reverting Python alone does not reverse the migration. Do not run create_all as a
replacement for Flyway in deployment.
