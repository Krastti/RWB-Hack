# P1/P2 consistency audit

Base: main aa5c2d7bea0c9e68b6dc785d29b355efc296be01 (rechecked before push).
PR #119 merged as ce5c3b1; #100 merged. Issues #120–#126 remain open.
PR #109 open, head 267117775c5c3bdc1d127482a524a75f4452a78c.

| Issue | Cause / affected files | Implemented correction | Evidence / remaining criteria |
|---|---|---|---|
| #120 | Run read profile Literal and transition documentation; #109 control models | Historical React reads preserved in C-01, read DTO and Angular types; new create remains Angular | HistoricalProfileTests; isolated merge with #109 succeeds but React-only control Literal still rejects Angular. Control consumer remains blocked, not closed. |
| #121 | Conflicting A-01/C-02 exact pins and implicit Pydantic import | A-01 includes shared C-02 requirements and explicit Pydantic; worker requirements aggregates all components | Clean Python 3.14 venv, one pip resolver, pip check, joint imports and offline graph/analyst tests pass |
| #122 | C-03 §8 contradicted §4 | Completion now explicitly requires confirmed checkpoint; receipt and intent distinguished | Existing graph checkpoint-outage regression passes without second model call. Shared durable lifecycle/worker barrier integration remains #128; not claimed implemented here. |
| #123 | Public Retry supports calls/output only; control implementation is in open #109 | No runtime policy change in this branch | Input/time prohibition and normalized API/UI reason remain unimplemented; do not close |
| #124 | Project DTO/service accepted whitespace | Reject whitespace before repository writes; preserve valid bytes/hash; C-01 pattern and Angular requirements/answer validators agree with Python Unicode whitespace | RequirementsWhitespaceTests and Angular validator regression pass. Run/answers write routes and revision/confirmation validation still need #127 integration; do not close |
| #125 | Version filtering before seq; unrelated GET/event snapshots | Highest seq selected first; GET totals always show unknown completeness | Angular regression for unknown version, order and duplicates; no event flag can label unrelated GET totals complete |
| #126 | Plan backend directory exceeded C-05 grants | Narrow plan to actual grants, propagate inline artifact and manifest metadata; --check validates actual profile | Positive/negative grants regression and full linked fixture validator pass |

No production data accessed or rewritten. No paid LLM call performed. The LLM
example exits because LLM_API_KEY is absent; it is not reported as a successful
provider integration. Two PostgreSQL tests require RWB_TEST_DATABASE_URL and are
skipped. No general multi-agent pipeline, shared persistence or call ledger is
introduced; #127–#131 remain open.

## Installation and display policy

Install from repository root with `python -m pip install -r
backend/requirements-worker.txt` in a fresh venv. No installation-order workaround,
no --no-deps/--force. C-02 remains 1.0, profile version and Run identity are unchanged.
OpenAPI keeps 2.1.0; this patch narrows invalid writes and permits historical reads.

For requirements/Answer.value, whitespace is U+0009–000D, U+001C–0020, U+0085,
U+00A0, U+1680, U+2000–200A, U+2028–2029, U+202F, U+205F, U+3000.
Validation never trims valid content. Historical requirements are not migrated.

GET Run has no shared snapshot identity with metrics.updated. The UI therefore
labels GET token totals with unknown completeness even when an event claims
complete usage. This remains safe during failed polling and recovery and does
not confuse zero totals with evidence of completeness.

## Review follow-up

Historical React runs are now read-only in the Angular UI: cancel/retry/answers
are hidden and their handlers make no HTTP request when invoked directly.
Server enforcement is still an explicit #109/#127 integration requirement, not
claimed as provided by the UI policy.

The grants validator distinguishes explicit files from directory grants and
rejects a file also used as a directory (including a parent needed by another
output), even within one task. Negative tests cover foreign migrations/integrations
paths, unknown owners and mismatched profiles. Directory-as-file and same-task
collision were reproduced as accepted by a2701c2 and rejected by this follow-up.

Questions now show whitespace/required/maxlength feedback with ARIA association.
DOM input-event tests exercise required and optional fields and correction.
RunDetail.metrics has a direct regression proving that complete event flags
cannot label successive GET metrics complete. Rendered RunDetail tests cover
historical profiles and direct handler tests cover cancel/retry/answers bypasses.

Follow-up verification: Angular 47 tests PASS and production build PASS under
Node 24; C-02 6 tests PASS; full validator PASS (11 schemas, 22 examples, 4 bundles,
1 snapshot); backend 4 PASS / 2 PostgreSQL SKIP; pip check and diff check PASS.
