# A-32: deterministic bounded exploration

`python -m backend.app.agents.behavior.explorer.example` runs without a model,
network, generated code or application. Dependencies: A-31 #147 / A-29 #143.

`interpreter.step(model, state, actor, action)` validates the model and returns
allowed/denied/unknown, a copied next state, rule IDs and FR IDs. No Python eval,
implicit boolean/integer coercion or mutation. Missing and explicit null are
different; comparisons involving either are unknown. `present` and `is_null`
are explicit. Invalid bounded values and actors return unknown. Effects read
pre-state and apply simultaneously. All supported operations have fixed semantics.

`explore(model, initial, max_depth=5, max_states=1000, timeout_seconds=2,
max_domain_values=64, max_scenarios=10000)` enumerates sorted identities, roles,
actions and finite parameter domains, then breadth-first states. The first
counterexample is shortest by number of actions; invariants are independent
assertions evaluated on every edge before state deduplication and at the initial
state. State-only deduplication is valid because this DSL has no hidden state.
Integer domains larger than the cap use explicit boundary/zero samples; sampled
or unknown domains are inconclusive. Scenario enumeration is also bounded.
Identities are the declared **synthetic finite domain**, not all real users.

Reports distinguish counterexample, no_counterexample_within_bounds,
inconclusive (time/state/scenario limit, partial model or sampled domain), and
unsupported (invalid model/operator). A depth frontier is explicit; a clean
bounded result is never an unbounded proof. Time-limited searches can stop at
different points on different machines; all non-time-limited ordering/traces
are deterministic. `replay(model, report)` checks model/interpreter version,
every edge and the independent violated invariant, including zero-step traces.
No correctness claim is made for extraction or emitted applications.

The example deliberately removes the owner guard while retaining the independent
owner invariant: Bob cancels Alice's draft in one step. The corrected model has
no counterexample within the same bounds. State exhaustion, an unknown operator,
partial model, missing/null owner and exact trace replay are separate checks.

`adapter.run_report` resolves one accepted input through the existing graph
ArtifactStore, validates C-02 bytes, uses the frozen profile's search limits,
and atomically accepts report plus AgentResult. Existing receipts replay without
rerunning search; changed inputs/limits conflict. The adapter does not create a
store or bypass worker owner authorization/lease fencing. Architecture owns
`contracts/behavior-report.json` under its existing grant.

Profile 1.2.0 adds search bounds as an explicit immutable opt-in snapshot; 1.0.0
and 1.1.0 remain unchanged. Register its ProfileSource explicitly through A-29.
The normal worker does not select it automatically. The new payload/profile
contracts still require owner review. Production graph registration and B-14
storage integration remain pending those upstream components; no database or
end-to-end pipeline test is claimed.
