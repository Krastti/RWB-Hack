# Run reads — B-09 / #69

Public contract: C-01 OpenAPI **1.0.0** and C-03 lifecycle **1.0**, as accepted in
main `ef1956c958ef64c8784ce0558c53269bea25142f`. No wire contract changes.

`RunQueryService(repository, cursor_secret=...)` exposes synchronous, keyword-only:

- `list_runs(owner_id: UUID, project_id: UUID, limit=100, cursor=None) -> RunPage`
- `get_run(owner_id: UUID, run_id: UUID) -> Run`
- `list_events(owner_id: UUID, run_id: UUID, after_seq=0, limit=100) -> EventPage`

`Run`, `StageAttempt`, `Event`, and pages are validated read projections from
`dto.py`, independent of ORM models. `Run.stages` includes all historical attempts
sorted by `(stage_id, attempt_no)`; current status/current_stage do not replace
that history. Public statuses are the exact C-01 names, including
`waiting_for_input`, `cancelling`, and `budget_exceeded`. Optional absent fields
are omitted; required nullable `RunPage.next_cursor` remains JSON null. All dates
are timezone aware, normalized to UTC and serialized with Z. Stored metrics and
confirmed question snapshots are returned without inventing measurements or
reconstructing state from events. Unknown event types remain public data.

## Replaceable repository

Implement `RunReadRepository` from `repository.py`. Every method receives owner_id;
SQL implementations must enforce it through Project.owner_id on every query.
`project_exists` prevents a missing/foreign project from looking like an empty
history. `get_run` also confirms that the returned run's project belongs to the
caller through `project_exists`; event reads share this check.
`OwnedRun(owner_id, run)` provides an additional service ownership check.
Foreign and missing resources both raise `ReadNotFound` (HTTP 404). Ownership is
checked before an event read, and the event query itself must be owner scoped.
Adapters must return detached consistent snapshots, with bounded queries and no
read-side writes. The service fails closed on owner/project/run mismatches,
duplicate `(stage_id, attempt_no)` pairs in any run, incorrect ordering, or
excessive rows, and never silently repairs event ordering.
Storage failures should use the application's existing generic storage handler.

History uses descending `(created_at, run_id)` keyset ordering. The signed opaque
cursor binds version, owner, project and last key. Use a server-only stable secret
of at least 32 bytes shared by app instances. Invalid/tampered/foreign-context
cursors raise `InvalidReadInput` (422); secret rotation invalidates old cursors.
Cursors contain a signed boundary, not authorization. They are opaque, not encrypted.
Newer runs inserted between pages do not shift the continuation boundary.

Events use `seq > after_seq`, ascending sequence, limit 1–100; gaps are allowed.
The repository receives `limit + 1` for lookahead (maximum 101). The service returns
at most limit items; `next_after_seq` is the final seq, or the input after_seq for
an empty page. `has_more` reports additional events currently visible; future
commits remain possible. Nothing resets or renumbers a run's seq on retry.
The repository supplies only sanitized C-03 public event data, never prompts,
raw provider responses, secrets, or operational logs.

## HTTP composition

After the existing auth configuration, explicitly install a real repository:

```python
from backend.app.api.runs import configure_runs
from backend.app.services.run_queries import RunQueryService

# app has already been configured with configure_auth(app, auth_service).
# repository implements RunReadRepository against real persisted snapshots.
configure_runs(app, RunQueryService(repository, cursor_secret=auth_config.secret_key))
```

`configure_runs` installs `GET /api/v1/projects/{project_id}/runs`,
`GET /api/v1/runs/{run_id}`, and `GET /api/v1/runs/{run_id}/events`, plus C-01
error handlers. It uses the same `current_user`, Bearer auth and ContractRoute as
project endpoints. `create_runs_router()` is also available for composition with
an overridden `get_run_query_service` dependency. Configure exactly once.

## Local example, explicitly in memory

```python
from uuid import uuid4
from backend.app.services.run_queries import RunQueryService
from backend.app.services.run_queries.memory import InMemoryRunReadRepository

owner_id, project_id = uuid4(), uuid4()
repository = InMemoryRunReadRepository(projects={project_id: owner_id})
service = RunQueryService(repository, cursor_secret="local-example-secret-32-bytes-long")
page = service.list_runs(owner_id=owner_id, project_id=project_id)
assert page.items == () and page.next_cursor is None
```

For populated examples pass `runs=[OwnedRun(owner_id, Run(...))]` and validated
`events=[Event(...)]` explicitly. The adapter makes detached copies and rejects
cross-owner projects, missing event runs, and duplicate event sequences. It is a
fixed local snapshot, not a persistence or concurrency implementation.

## Integration boundary and rollback

The production `main.create_app` is unchanged and does **not** mount these routes
or install an in-memory source by default. There is no hidden fallback, feature
flag, runtime-generated data, migration, worker or pipeline implementation here.
B-11 owns production composition with a persistent adapter. #65 supplies the
entities; that separate PR is not imported or required by this module.
For that adapter map ORM `Run.id -> Run.run_id`, `Run.stage_attempts -> Run.stages`,
`Run.questions_revision/questions/metrics ->` the persisted public snapshots,
`StageAttempt` public fields directly, and `RunEvent -> Event`; never expose
internal attempt IDs/input_hash/leases or stopped_at as extra closed DTO fields.
The adapter/writers must maintain correct metrics (including elapsed duration)
and current question snapshots atomically according to C-03; this query service
does not fabricate unavailable facts. Production PostgreSQL read behavior and
end-to-end pipeline execution remain unverified until that integration exists.
Rollback: revert this additive module PR or omit configure_runs at composition;
there are no database or external side effects to undo.
