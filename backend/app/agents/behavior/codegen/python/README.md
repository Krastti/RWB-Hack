# A-33: deterministic Python projection

Run `python -m backend.app.agents.behavior.codegen.python.example` from the root.
Dependencies: A-29 #143, A-31 #147, A-32 #148. The example compiles/imports only
our emitted trusted source and checks eight cases against the interpreter:
allowed owner, foreign owner, wrong state, missing/null, integer lower/upper/outside
bounds and boolean masquerading as integer. A plain body dictionary cannot be an
auth context. Repeated emission has identical bytes; missing bindings fail.

`emit(model, catalog, routes, profile)` takes an immutable validated bound model,
the accepted A-09 catalog and A-13 route descriptors. It validates all bindings,
operation ownership, primary key fields/types, contract version, identifiers and grants.
Compiler version is `python-1.1.0`; regenerate all three files together.
Unresolved/unsupported rules block emission. It returns exactly three backend-owned
files: policy, transition service/route installer, and source map. The compiler
owns their complete bytes: A-22 must accept them as deterministic outputs and
exclude these paths from LLM file specs. It must still preserve FR/contract and
subject-validation receipts; deterministic generation is not automatic acceptance.

The policy includes the exact pure A-32 runtime, finite model and TrustedActor.
The single-record service opens a transaction, SELECTs the actual SQLAlchemy
entity with `FOR UPDATE`, evaluates the locked row using trusted authenticated
actor data, applies only declared effects and flushes before commit. It explicitly
loads mapped columns and copies their values before commit. After successful
commit it returns that detached dictionary, including refreshed server values.
No expired ORM object reaches the synchronous response adapter; both default
`expire_on_commit=True` and explicit False work. Commit failure returns no success.
A rejected
policy performs no mutation. No user-supplied owner/state is used for authorization.
The fresh session must have no active transaction. Identity fields in this first
finite model must already match its string identity domain; the compiler does not
invent an identity conversion or silently broaden that domain.

`install_routes` produces real FastAPI handlers which call the locked service.
A-13 must call it with its **actual** session/auth dependencies, entity registry
and success/failure response adapters matching the accepted OpenAPI. Route method,
path (`{record_id}`), primary-key field and `id_type` are explicit accepted inputs; unsupported
route shapes fail. The descriptor is a narrow integration port, not a replacement
OpenAPI/data-model schema. Error serialization is delegated to A-13 instead of
inventing an error DTO. Invariant functions remain independent simulator assertions;
they do not silently replace authorization guards (the map labels this explicitly).

Each descriptor is exactly `{method, path, id_field, id_type}`; `id_type` is
`integer`, `string` or `uuid`. Catalog routes must contain the same descriptor.
The generated FastAPI path parameter is typed as `int`, `str` or `uuid.UUID`;
invalid integer/UUID URLs are rejected by FastAPI validation before SQL. Direct
`transition` callers must also pass the native declared type; bool is not int.
At installation, every registry model must have the bound single-column primary
key with a matching SQLAlchemy Integer/String/Uuid Python type. Composite,
non-primary and mismatched keys are rejected before any route is installed or
data changed. Unsupported/missing descriptor types block emission; no string
default or implicit identity conversion is introduced.

`success_response(operation_id, snapshot)` now consumes a plain dictionary of
mapped column values instead of an ORM row. It must select/serialize the accepted
response DTO, e.g. `{'id': snapshot['id'], 'status': snapshot['status']}`.
Relationships are excluded; columns (including deferred/server-generated values)
are explicitly refreshed while the transaction still holds the lock. The return
happens only after commit succeeds, so the adapter performs no implicit ORM I/O.
Record primary keys are separate from the finite string actor/owner identity
domain; the latter keeps its existing constraints.

Source map includes contract/model/input/compiler versions, exact emitted-file
SHA-256 checksums and rule→file/symbol. All files stay within backend service grants.
No migration, foreign owner files, external side effects or multi-record operation
is emitted. Default platform behavior is unchanged; delete the opt-in module or
omit route installation to roll back.

**Outstanding acceptance:** A-12/A-13 and accepted catalog/route adapters remain
required. This PR supplies executable policy/service/installer but cannot install it
into a nonexistent generated application. PostgreSQL concurrency, actual FastAPI
response schemas/auth dependencies and end-to-end generation have not been tested.
Those checks and A-13 invocation remain required before #113 can be closed. The
module is draft, not a claim that an unused policy file completes integration.

## Review regressions

With the A-31/A-32 dependencies present, run:

```sh
python -m unittest discover -s backend/tests -p 'test_python_behavior_codegen.py' -v
```

The eight tests exercise emitted source through FastAPI/ASGI and
SQLAlchemy+aiosqlite, including default/disabled expiry, integer/string/UUID keys,
deferred columns, refreshed trigger values, invalid IDs before SQL, failed commit,
unchanged owner/state denials and mismatched/composite key bindings. Actual emitted
SELECT statements are compiled for the PostgreSQL/psycopg dialect to verify native
key parameters and FOR UPDATE. SQLAlchemy async requires greenlet; the database
cases also require aiosqlite. Tests report an explicit skip when pending A-31/A-32
or async test dependencies are absent. SQLite does not verify PostgreSQL locking
or concurrent transactions, and these adapters are test auth/response providers,
not the future A-13 application wiring.
