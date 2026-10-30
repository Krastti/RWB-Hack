# A-33: deterministic Python projection

Run `python -m backend.app.agents.behavior.codegen.python.example` from the root.
Dependencies: A-29 #143, A-31 #147, A-32 #148. The example compiles/imports only
our emitted trusted source and checks eight cases against the interpreter:
allowed owner, foreign owner, wrong state, missing/null, integer lower/upper/outside
bounds and boolean masquerading as integer. A plain body dictionary cannot be an
auth context. Repeated emission has identical bytes; missing bindings fail.

`emit(model, catalog, routes, profile)` takes an immutable validated bound model,
the accepted A-09 catalog and A-13 route descriptors. It validates all bindings,
operation ownership, primary key fields, contract version, identifiers and grants.
Unresolved/unsupported rules block emission. It returns exactly three backend-owned
files: policy, transition service/route installer, and source map. The compiler
owns their complete bytes: A-22 must accept them as deterministic outputs and
exclude these paths from LLM file specs. It must still preserve FR/contract and
subject-validation receipts; deterministic generation is not automatic acceptance.

The policy includes the exact pure A-32 runtime, finite model and TrustedActor.
The single-record service opens a transaction, SELECTs the actual SQLAlchemy
entity with `FOR UPDATE`, evaluates the locked row using trusted authenticated
actor data, applies only declared effects and flushes before commit. A rejected
policy performs no mutation. No user-supplied owner/state is used for authorization.
The fresh session must have no active transaction. Identity fields in this first
finite model must already match its string identity domain; the compiler does not
invent an identity conversion or silently broaden that domain.

`install_routes` produces real FastAPI handlers which call the locked service.
A-13 must call it with its **actual** session/auth dependencies, entity registry
and success/failure response adapters matching the accepted OpenAPI. Route method,
path (`{record_id}`) and primary-key field are explicit accepted inputs; unsupported
route shapes fail. The descriptor is a narrow integration port, not a replacement
OpenAPI/data-model schema. Error serialization is delegated to A-13 instead of
inventing an error DTO. Invariant functions remain independent simulator assertions;
they do not silently replace authorization guards (the map labels this explicitly).

Source map includes contract/model/input/compiler versions, exact emitted-file
SHA-256 checksums and rule→file/symbol. All files stay within backend service grants.
No migration, foreign owner files, external side effects or multi-record operation
is emitted. Default platform behavior is unchanged; delete the opt-in module or
omit route installation to roll back.

**Outstanding acceptance:** A-12/A-13 and accepted A-09 adapters are absent from
main. This PR supplies executable policy/service/installer but cannot install it
into a nonexistent generated application. PostgreSQL concurrency, actual FastAPI
response schemas/auth dependencies and end-to-end generation have not been tested.
Those checks and A-13 invocation remain required before #113 can be closed. The
module is draft, not a claim that an unused policy file completes integration.
