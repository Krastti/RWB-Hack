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

## Iteration after PR #152: durable storage and artifact reads

This iteration is **not** a completed production HTTP → worker → generation → ZIP
pipeline. The requested full integration test has not been delivered. In particular,
there is still no production domain orchestrator wiring A-03/A-09/generation to
StageWorker and lifecycle, no private analysis-input bootstrap in the artifact adapter, no PostgreSQL LangGraph checkpoint saver binding, and
no runnable contest-profile HTTP generation. These gaps are implementation work,
not an infrastructure-only blocker. The earlier matrix is historical; the following
rows supersede its storage/API gaps.

Implemented:

- V10: PostgreSQL BYTEA artifact objects plus immutable attempt/batch receipts.
  PostgreSQLArtifactStore implements the existing graph ArtifactStore and A-30
  resolver interfaces; PostgreSQLBatchStore implements A-22's existing port.
  Writes use the **existing** LeasedExecution.transaction(), including Run lock
  and lease/fencing validation. No second queue, lifecycle or StageAttempt model.
  Whole batches and receipts commit together. Identical replay is a no-op;
  changed UUID bytes, duplicate path/version, owner, profile, stage ownership,
  grants, checksum and profile count/size violations are rejected.
- V11: physical ModelCall reservations, settlement and unknown/in-doubt state.
  PostgreSQLBudgetLedger implements A-22 reserve/finish/abandon/usage. Global
  admission serializes under the existing Run lock. Input reservation is a
  conservative UTF-8 byte upper bound of the larger A-01 json_schema envelope (also conservative for json_object), including
  schema instructions. Output uses the request cap. Unknown usage retains the
  reservation. A repeated call UUID cannot dispatch again. Prompts, headers,
  API keys and raw model responses are not stored in the call ledger.
  This ledger is integrated and tested with A-22; analysis/architecture still
  require the production composition to use the same accounting boundary.
- V12: immutable snapshot manifests referencing exact artifact metadata/bytes.
  SnapshotPublisher rejects unsafe/colliding paths and self-referencing manifest.
  Prepared publication requires profile inventory, owner/kind, traceability,
  no unanswered required questions, accepted upstream result receipts and
  checkpoint confirmation receipts. No current orchestrator produces that full
  barrier yet. Publication itself does not change Run.status.
- Existing FastAPI create_app now mounts C-01 `listArtifacts`, `getArtifact`,
  `downloadArchive` at the specified paths. Owner checks precede reads; foreign
  resources return 404. Listing has snapshot-pinned pagination; content is an
  attachment with nosniff. ZIP reads selected bytes and verifies hashes again.
  Partial snapshots return 409 for archive download. Missing snapshots are 404.
  No export endpoints, frontend changes or public DTO changes.

The new migrations are additive and do not edit V1–V9. Test migration runners
now sort by numeric Flyway version: lexical sorting broke at V10. Apply V10–V12
with the normal platform Flyway process before deploying this code. Preserve
artifact/receipt/snapshot/call data on rollback; stop writers before reverting code.
PostgreSQL storage shares backup/access policy with the platform database; capacity
must account for BYTEA and retained immutable versions. No filesystem staging or
symlink-following is involved.

New integration tests (`backend/tests/test_artifact_persistence.py`) exercise:

1. Existing PostgreSQL intake → command claim → LeasedExecution for persistence.
2. Atomic batch acceptance, duplicate receipt, identity conflict, forbidden file,
   rollback of earlier batch entries, new store instance reading durable bytes,
   and owner isolation.
3. Expired lease preventing artifact publication.
4. Budget reservation replay, in-doubt usage remaining unknown, conservative
   budget denial and idempotent settlement/reconciliation.
5. Real ContextBuilder/A-01/BatchExecutor with controlled HTTP transport and real
   PostgreSQL adapters: one generated source file, then replay with no new call.
   Upstream handoff uses fixtures; this is **not** a full generated application,
   real-provider smoke, contest-profile acceptance or HTTP-to-ZIP E2E.
6. HTTP artifact endpoints against committed PostgreSQL data and fresh app/service
   instances; partial archive 409, immutable snapshot identity and owner 404.
7. ZIP roundtrip and old-byte pinning after a newer file version. The archive
   reader test directly seeds a prepared snapshot fixture to isolate encoding;
   it explicitly does **not** prove assembly acceptance or lifecycle completion.

No backend/worker subprocess restart test has run: new adapter/service instances
prove absence of process-local storage but are not claimed as process recovery E2E.
No build/test/security readiness is inferred from any snapshot or HTTP response.

### Verification for this iteration

Implementation commits: `48fc7e0` (storage/ledger), `cd1582f` (snapshots/read API).
Python 3.12.9, disposable PostgreSQL 16 at loopback, same worker requirements as
previous iteration. No generated code executed on host.

```sh
RWB_TEST_DATABASE_URL=<disposable PostgreSQL URL> PYTHONPATH=backend:backend/tests:. /tmp/rwb-e2e-venv/bin/python -m pytest -q backend/tests
# 115 passed, 15 subtests passed, 47.36s
PYTHONPATH=backend:backend/tests:. /tmp/rwb-e2e-venv/bin/python -m unittest backend.app.agents.generation.tests.test_executor -q
# 11 tests OK, 6.558s
PYTHONPATH=backend:. /tmp/rwb-e2e-venv/bin/python -m contracts.agents.validate --check
# 11 schemas, 22 examples, 4 linked bundles, 1 snapshot OK
```

After the final response DTO and accepted-result identity checks, the affected
`test_artifact_persistence.py` suite was rerun: **6 passed, 5.76s**. `git diff
--check` passed. The initial full run had 114 passing and one failing legacy
migration fixture; its numeric sorting/table-copy fixture was corrected and the
full suite above passed. An initial DB connection attempt before PostgreSQL was
ready failed; subsequent tests used a ready instance. No such failures are counted
as passing runs.

**HTTP-to-ZIP scenario: not_run / not implemented.** The HTTP read and ZIP encoding
tests are separate component tests with explicit upstream/snapshot fixtures.
Live provider smoke, subprocess restart, analysis/clarification/architecture chain,
contest product generation and full bounded recovery remain not_run. No Issues
closed, no alternate Run lifecycle introduced, draft PR stays draft.
