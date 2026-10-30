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
after success or failure. Failed refresh blocks further submission. Late responses
do not replace a different record selected while the request was pending. The
server still authorizes every request; disabling a button is not authorization.

A-11 must place the component at the accepted screen/element in UI_BINDINGS,
provide its real actor/API gateway and map API response data through those bindings.
It must reserve all returned file paths from LLM rewriting and publish them through
A-22 acceptance. Unknown identity parameter widgets are explicitly unsupported;
no arbitrary text identity control or new framework is introduced.

Validation: eight TS/interpreter cases run in Node's native TypeScript support;
missing hidden owner remains unknown. Emitted policy also passes TypeScript 5.9.3
`tsc --noEmit --strict --target ES2022`. This checks the policy module only, not
Angular application compatibility. The earlier type-check failure on provenance
metadata was corrected by explicitly allowing metadata in the internal model types.

Outstanding: actual A-10/A-11 template/generator and accepted A-09/ui-spec adapters
are absent from main. The Angular consumer is executable source but has not been
installed in a real generated screen or built with that application's Angular
compiler. Those integration/build checks remain required before #114 closes.
The draft module is opt-in; default frontend generation and dependencies are unchanged.
Rollback is to omit this compiler/consumer from A-11.
