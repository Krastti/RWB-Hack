# A-31: opt-in behavior model

Run from repository root (Python 3.12, `backend/requirements-worker.txt`):

```sh
python -m backend.app.agents.behavior.example
```

Dependencies: A-29 #143, A-30 #145, A-22 #146. The example explicitly uses
MockTransport and a synthetic ledger. It verifies A-01 invocation, validation,
partial blocking, immutable binding revision and rejection cases; it does not
prove text extraction accuracy or test a live model/database.

`BehaviorNode.run(request, attempt_id, context, profile, accepted_answers,
previous=None)` returns the existing C-02 AgentResult. The worker must authorize
and freeze B-12 answers, prepare A-30 context containing accepted requirements,
fence/replay the StageAttempt before invocation, provide the durable shared
BudgetLedger and atomically accept output+result. Every physical dispatch has a
fresh call ID and is charged before validation, including failures. Cancellation
preserves the reservation through `abandon`. There is no retry or storage fallback.
A partial model is returned with an error diagnostic and failed status; the
existing analysis/clarification coordinator must consume it without dispatching
code generators. It is not a reason to automatically retry extraction.

`validate_model(model, requirements, accepted_answers)` checks types, local
references, stable UUIDs derived from semantic decision IDs, evidence links,
FR/AC coverage, exclusive field assignments and critical assumptions. Fact,
accepted answer and assumption are distinct. Quotes are checked against their
sources; this cannot establish that the interpretation is correct. Full coverage
means declared supported rules/AC links, not proof of implementation or intent.
Decision IDs must be preserved by the producer across revisions; the node includes
the previous snapshot in the prompt, but semantic equivalence of renamed decisions
cannot be proved locally.

The DSL operates on one entity record per action. Fields are enum, boolean,
bounded safe integer (Python/JS exact range), or finite identity, optionally null.
Roles and synthetic identities have explicit finite domains. Expressions are
literals, state/actor/parameter references, invariant-only previous-state
references, comparisons, and/or/not, present, is_null. Missing values are unknown;
null comparisons are unknown (use is_null explicitly). No coercion, truthiness,
code, arbitrary arithmetic, I/O, cross-record effects or clocks. Effects assign
simultaneously from pre-state; assigning one field twice is rejected. Unsupported
logic is an explicit unsupported rule with null expression, never a permissive
predicate. Any critical unresolved/unsupported rule blocks downstream codegen.

`bind(model, bindings, catalog)` checks an **accepted A-09 catalog**, including
entity fields, operation→entity and screen element→operation, and returns version
+1 with parent checksum. Logical and bound snapshots use separate paths/owners.
The catalog is a narrow port documented in the function; an adapter to real
A-09 payloads must be supplied by A-09, not populated by guessed names. Changes
never overwrite accepted bytes. Consumers must validate against the accepted
catalog as well as the structural model schema.

Profile 1.1.0 is a separate opt-in snapshot. `behavior_profile()` registers it
explicitly; default A-29 loading still selects unchanged 1.0.0.
The node also accepts the caller's frozen 1.2.0 profile from #148, which includes
search bounds. It never replaces that snapshot with the helper's 1.1.0 default.
Analysis owns behavior-model.json; architecture owns behavior-bindings.json.
The new profile limits model bytes, expression depth, rules and domain size. Its schema and the
new payload schema are additive proposals for C-02/C-05 owner review; closed
AgentInput/AgentResult are unchanged. Stage registry integration must assign
unique stage IDs (e.g. behavior_model/behavior_bindings) under existing owners;
this module does not self-register untrusted executors.

Outstanding integration: A-03 #77 and A-09 #87 are assigned to other contributors.
Their accepted hooks, durable answer/receipt adapters and graph routing are not
present in main. This module is deliberately opt-in and the PR remains draft
until those hooks and the new profile contract are reviewed. No end-to-end
pipeline completion is claimed.
