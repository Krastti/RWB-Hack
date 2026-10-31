# Hackathon integration audit — partial delivery

Base: `0337844f0f47c5eba6600beec87e833a2f63738a` (origin/main fetched 2026-10-31).
Workspace: separate worktree; original dirty checkout was preserved.

## Technical matrix

| Component | Existing implementation / production path | Gap / integration point | Evidence |
|---|---|---|---|
| HTTP, PostgreSQL, auth | `create_app` composes SQL repositories and runtime | No complete generation composition | runtime/owner tests |
| Lifecycle, queue | `compose_runtime`, intake, worker commands, lease/fencing | `compose_worker` requires external orchestrator | recovery/intake tests |
| Analysis | `AnalysisGraph`, C-02 analyst exist | End-to-end worker binding not established by this change | existing graph tests; not live provider evidence |
| Clarification | `/answers`, PostgreSQL replay | Full resumed graph to generated project not demonstrated | answers/recovery tests |
| Architecture | A-09 `prepare_architecture`, `RuntimeNodes`, freeze | Fixed legacy schema/profile validation in architecture and generators | architecture stage tests |
| Profiles | A-29 loader and frozen bytes/rules | This change registers separate contest policy; HTTP selection remains legacy | profile regression and contest tests |
| Context | A-30 `ContextBuilder` and resolver port | Durable artifact resolver needed in generation composition | context tests |
| Generation | A-22 `BatchExecutor` | Durable BatchStore/BudgetLedger and approved exact inventory scheduling need composition | batch recovery/replay tests |
| Persistence | Flyway V1–V9, Run runtime state | Physical ModelCall ledger, artifact/snapshot adapters still required | SQL lifecycle tests, not generated-product evidence |
| Artifacts / ZIP / export | C-01 specifies interfaces | Not implemented by this change | not_run |
| Web Admin | login/projects/run analysis/questions/metrics | No new generation tree/evidence/export UI in this change | unchanged |
| Sandbox / repair | Required by prompt | No isolated runner configured or implemented here | not_run |

Actual path examined: HTTP → Run creation + SQL queue → intake → StageWorker
with caller-provided orchestrator. This report does **not** claim an assembled
analysis → architecture → generation → verification production path.

## Delivered

Separate `russtech-fullstack/1.0.0` policy/schema/rules with Angular 20.1.6,
ASP.NET Core 10/C# 14, PostgreSQL 17.9, EF Core migrations, conditional Kotlin
2.2.21, explicit Android/verification ownership and mandatory CI/test/infra files.
Trusted rules resolution now uses an explicit ID-to-local-document allowlist.
Legacy profile/schema bytes remain untouched. No DTO, routes or migrations changed.
No production HTTP switch is enabled for unsupported generators.

## Issue mapping (not closure evidence)

All referenced issues were OPEN when queried. Their full DoD is not claimed:

| Issue | Delivery status |
|---|---|
| #77, #80 | partial upstream components; full worker/LLM path not delivered here |
| #105 | partial upstream A-22; durable runtime integration remains |
| #122, #124 | not assessed against complete issue acceptance criteria |
| #127 | partial: separate registered policy; API selection/migration remains |
| #128, #129 | partial upstream runtime; durable graph/accounting integration remains |
| #130 | worker requirements install on Python 3.12 verified; live binding remains |
| #131 | partial: contest policy grants/freeze; artifact-to-export ownership remains |

Use Refs only. No automatic issue closure.

## Reproduction

```sh
uv venv /tmp/rwb-e2e-venv --python 3.12
uv pip install --python /tmp/rwb-e2e-venv/bin/python -r backend/requirements-worker.txt pytest
PYTHONPATH=backend:backend/tests:. /tmp/rwb-e2e-venv/bin/python -m pytest -q backend/tests
PYTHONPATH=backend:. /tmp/rwb-e2e-venv/bin/python -m unittest backend.app.agents.generation.tests.test_executor -q
```

`backend/tests` must be in PYTHONPATH because existing tests import sibling test
fixtures by module name. Run A-22 with unittest; pytest collection treats its
package as a standalone top-level package and fails relative imports, including
with importlib mode. Initial system-Python runs failed collection because
LangGraph was missing and these import requirements were not supplied.
PostgreSQL tests require RWB_TEST_DATABASE_URL pointing to a disposable database.
This audit uses PostgreSQL 16 for **platform** regression, not contest-product
PostgreSQL 17.9 verification. No generated code was executed on the host.

## Remaining acceptance work

P0 and P1 from the requested prompt are incomplete: durable artifact/batch/call
adapters, single orchestrator production binding, exact approved inventory,
contest generators, assembly/snapshots/API/ZIP, isolated verification and bounded
repair, GitLab export/CI/deployment, UI, two domain E2E fixtures and real-provider
smoke. No generated Angular/.NET/Android application or parking acceptance result
is claimed. Build/security/deploy and live provider checks are not_run.

Rollback removes the new registry entry/profile. Keep entries while any caller
retains frozen snapshots. No applied migration changes or database rollback needed.

## Recorded results (2026-10-31)

Code tested: `693a596` (profile implementation); subsequent commit only adds this report.
Python 3.12.9, Linux, dependencies from requirements-worker.txt installed in
/tmp/rwb-e2e-venv. Disposable PostgreSQL 16 container bound to loopback port 55439.

- `RWB_TEST_DATABASE_URL=<disposable-url> PYTHONPATH=backend:backend/tests:. /tmp/rwb-e2e-venv/bin/python -m pytest -q backend/tests`:
  **109 passed, 15 subtests passed**, 38.67 seconds, exit 0.
- `PYTHONPATH=backend:backend/tests:. /tmp/rwb-e2e-venv/bin/python -m unittest backend.app.agents.generation.tests.test_executor -q`:
  **11 tests OK**, 6.416 seconds, exit 0.
- `PYTHONPATH=backend:. /tmp/rwb-e2e-venv/bin/python -m contracts.agents.validate --check`:
  **11 schemas, 22 examples, 4 linked bundles, 1 snapshot OK**, exit 0.
- `git diff --check`: exit 0.

Earlier exploratory combined pytest runs: 67 passed/42 skipped/11 collection
errors without configured DB; 98 passed/22 errors with DB (11 initial connection
errors before startup and 11 A-22 collection errors). Corrected final commands
above supersede those runs; unittest is the supported A-22 invocation here.
No platform runtime failure was observed in the final PostgreSQL-backed run.
These results are regression evidence for platform components, not generated
contest-project execution or full HTTP-to-generation E2E evidence.
