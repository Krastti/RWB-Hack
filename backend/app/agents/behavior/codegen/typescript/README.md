# A-34: TypeScript policy and Angular consumer

Run from root with Python 3.12 and Node >=24:

```sh
python -m backend.app.agents.behavior.codegen.typescript.example
# If Node is outside PATH: NODE_BINARY=/absolute/path/to/node python -m ...example
```

Dependencies: A-29 #143, A-31 #147, A-32 #148. No dependency on the Python emitter.
`emit(model, catalog, profile)` validates accepted bindings, one contract version,
full supported rule coverage and frontend grants. It returns standalone policy,
Reactive Forms component, explicit UI bindings and a versioned rule source map.
Byte identity is stable even if JSON object keys are reordered. Exact file hashes,
model/input checksums and compiler version are recorded. Independent invariants
are labeled unenforced; they are simulation assertions, not extra UI guards.

The runtime uses three-valued evaluation, exact typed equality, safe integer
checks, explicit missing/null, and no truthiness/coercion. It only reads fields
listed in the accepted binding's public_fields; missing private owner data means
unknown, not an extra API fetch or permission. Finite synthetic identity domains
retain the A-31 limitation; arbitrary new identities do not silently get access.

DomainActionComponent calls `decide` on every evaluation, so changed actor, record
or form values recompute access. Reactive Form validators call the same emitted
field constraints. Action submission rechecks access, uses the supplied accepted
API gateway, displays denial/unknown explanations with rule IDs, and refreshes
after success or failure. Async state updates notify zoneless Angular with
`markForCheck()`. Failed refresh blocks further submission; label changes do not
remove that block. A replacement `record` input must contain freshly loaded server
data. Changing operation, record ID, gateway or actor without replacing that input
keeps submission blocked until fresh data arrives.

Each request captures its gateway, operation, record ID and generation. Changing
context or replacing the input record invalidates pending callbacks, including
A→B→A navigation. Old responses cannot change record, freshness, error or the busy
state of a newer request. Destroyed components ignore pending responses. The
server still authorizes every request; disabling a button is not authorization.

A-11 must place the component at the accepted screen/element in UI_BINDINGS,
provide its real actor/API gateway and map API response data through those bindings.
It must reserve all returned file paths from LLM rewriting and publish them through
A-22 acceptance. Unknown identity parameter widgets are explicitly unsupported;
no arbitrary text identity control or new framework is introduced.

Validation: eight TS/interpreter cases run in Node's native TypeScript support;
missing hidden owner remains unknown. The saved component regressions compile
the actual emitted files with Angular 22.2.4 / TypeScript 6.0.2 and the repository's
strict settings, then exercise zoneless TestBed with JSDOM and controlled Promises.
They check DOM updates without manual change detection, failed refresh recovery,
same-ID context switches, A→B→A races and component destruction.

With the dependencies above present and `npm ci` completed in `frontend`:

```sh
python -m backend.app.agents.behavior.codegen.typescript.tests.run
```

`NODE_BINARY` selects Node; `ANGULAR_NODE_MODULES` can point to an existing install
of the repository's frontend dependencies. The runner uses temporary emitted files,
does not install packages and only adjusts the compiled policy import extension
for Node ESM. Compiler/source-map version is `typescript-1.0.1`.

Outstanding: the component has not been installed in a real A-11 generated screen
with accepted A-09/ui-spec bindings and an actual API gateway. The standalone
Angular compile/runtime checks do not replace that integration or application build.
Those checks remain required before #114 closes.
The draft module is opt-in; default frontend generation and dependencies are unchanged.
Rollback is to omit this compiler/consumer from A-11.
