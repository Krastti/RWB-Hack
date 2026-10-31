# Hackathon integration audit

**Current scope:** HTTP → production worker → AnalysisGraph/PostgreSQL checkpoint
→ A-09 is implemented and integration-tested on both profiles. See the final
“Current iteration” section for current evidence. Earlier partial-delivery
sections below are historical; the complete generation/verification hackathon
pipeline remains outside this completed iteration.

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

## Current iteration: production HTTP → AnalysisGraph → A-09

This section supersedes the earlier statement that no production domain
orchestrator exists. Base for this iteration: `ad47cf8`. Work remains on PR #152
and the same worktree/branch. The original user checkout remains untouched.

### Executed path

`create_app` HTTP createProject/createRun → immutable revision + RunInputSnapshot
(profile JSON, rule bytes/checksums and server policy bundle) → PostgreSQL intake
→ registered Celery task → existing StageWorker/LeasedExecution →
PipelineOrchestrator → trusted private analysis-input artifact → A-30 validation
→ C-02 AnalysisNode using A-02/A-01 → AnalysisGraph + PostgreSQL saver → durable
accepted result → confirmed checkpoint → same lifecycle finish_attempt →
atomic architecture attempt/WorkerCommand registration → A-30 → real A-09
RuntimeNodes (proposal/OpenAPI/data model/UI spec) → atomic five-artifact receipt
→ lifecycle acceptance and checkpoint confirmation receipt.

The Run remains **running** after successful architecture: generation and assembly
have not executed. No completed Run, generated product, build/test or deployment
readiness is claimed. No new queue, Run lifecycle or duplicate StageAttempt was
introduced. The production entrypoint calls existing compose_runtime and
compose_worker. Pipeline-specific recovery replays captured responses/results
within the same registered attempt under a new fenced command generation;
RuntimeRecovery remains the fallback for uncaptured lost executions.

### Contracts and compatibility

C-01 is now **2.2.0**: additive `russtech-fullstack` profile identity in create/read
Run DTOs and the normative OpenAPI. Existing fields and legacy requests remain
unchanged; historical web-fastapi-react remains read-only. C-02 envelopes and
C-03 lifecycle are unchanged. The data-model/UI content schemas admit the new
profile identity; their PostgreSQL model/UI semantics remain unchanged. Schema
and ownership validation still run against an allowlisted frozen profile version.
No .NET profile is translated into FastAPI: prompts carry the actual .NET 10,
C# 14, EF Core, Angular 20.1.6 profile. Legacy defaults are retained.

The local requirements_analyst is requirements-analyst/0.1 with Q-NNN question
IDs and incompatible DTOs. The production C-02 bridge therefore uses A-02's
accepted strict draft/provenance validation and Markdown renderer, with A-01
HTTP transport. AnalysisGraph preserves FR/role/question identities and verifies
source evidence and confirmed answers before acceptance. No legacy 0.1 envelope
is relabeled as C-02.

A-09's trusted policy contracts are preserved. API/security and API↔data naming
policies come from server configuration, not a model-generated policy or user
request. They are frozen when the Run is created. Missing policy configuration
produces a failed architecture result with `ARCHITECTURE_POLICIES_REQUIRED`
without an architecture LLM call. A checksum may be supplied explicitly; a null
requirements_checksum in a **server-approved** policy binds that policy to the
accepted requirements bytes before validation. Operation/permission/name mappings
are still validated by the existing policy validators. Arbitrary business domains
need appropriate approved policies; there is no invented permissive default.
Android and generation inventory dispatch remain outside this iteration.

### Durable boundaries

New migration: **V13__pipeline_checkpoints.sql** only. V1–V12 were not edited.
PostgreSQLCheckpoints stores complete typed LangGraph checkpoints and pending
writes, uses JSON+/msgpack with pickle disabled, and writes through the existing
fenced execution transaction. It uses no local SQLite checkpoint file. Sync graph
durability is confirmed before scheduling architecture. A-09 is intentionally an
ephemeral graph: its accepted bundle and runtime checkpoint receipt form its
persistent replay boundary.

All physical calls now use the same PostgreSQLBudgetLedger, including every A-09
child. The A-01 accounting context reserves before HTTP dispatch, settles actual
provider usage and stores the raw structured response privately with settlement
for deterministic replay after downstream storage/validation failure. This
supersedes the previous iteration's statement that the ledger contains no raw
responses. Keys, authorization headers and prompts are not stored there. Private
response bytes contain business/model data and require the same access/retention
policy as artifacts. Deterministic physical call UUIDs match accepted C-02 usage;
replay checks the original request fingerprint. Unknown calls retain reservations
and are marked in_doubt when their lease is revoked. No Run lock is held during
LLM HTTP, and the integration transport checks this with a separate NOWAIT lock.

Celery tasks now use shared=False: multiple application instances must not reuse
a previous app's worker callable. This was exposed by the restart/service tests.
The A-01 optional accounting ContextVar is dependency-free; standalone A-01 does
not acquire a LangGraph/PostgreSQL dependency.

### Launch / reproduction

Install the existing worker requirements and apply Flyway V1–V13 to a disposable
or deployment PostgreSQL database using the normal platform migration process.
Package backend, contracts, templates and docs from the same checkout.

```sh
uv venv /tmp/rwb-e2e-venv --python 3.12
uv pip install --python /tmp/rwb-e2e-venv/bin/python -r backend/requirements-worker.txt pytest

# Set DATABASE_URL and JWT_SECRET_KEY using the existing platform setup.
# Server-only JSON file with exactly api_policy and naming_policy:
export PIPELINE_POLICY_FILE=/trusted/config/architecture-policies.json
PYTHONPATH=backend:. /tmp/rwb-e2e-venv/bin/uvicorn app.main:create_app --factory

# Worker additionally requires CELERY_BROKER_URL and LLM_BASE_URL, LLM_MODEL,
# LLM_API_KEY. Optional LLM_MAX_OUTPUT_TOKENS, LLM_TIMEOUT_SECONDS,
# LLM_RESPONSE_FORMAT=json_schema|json_object. Do not put secrets in artifacts.
PYTHONPATH=backend:. /tmp/rwb-e2e-venv/bin/celery -A app.workers.pipeline:app worker -B --loglevel=INFO
```

Policy structures are documented by the existing OpenAPI policy schema and
architecture naming_policy_schema; the checked-in agent examples illustrate
those structures for their fixture only. They are not default production policy.
Changing PIPELINE_POLICY_FILE affects new Runs only. Historical Runs lacking
frozen rules/policies are not silently reconstructed from current server files.

New executable evidence lives in `backend/tests/test_pipeline_execution.py`.
These tests create project/revision/Run through real HTTP handlers, seed only an
authenticated test User, and execute registered Celery tasks using Task.apply
(in-process task delivery, memory broker configuration). They use the real
StageWorker, PostgreSQL queue/lifecycle/stores/checkpointer, A-01 HTTP adapter,
A-02, AnalysisGraph, ContextBuilder and all four real A-09 adapters. Only model
HTTP responses are controlled fixtures. No accepted AgentResult, artifact,
checkpoint or successful stage is manually inserted. Authentication is supplied
by a dependency override; authentication regressions run separately.

They prove both profiles; clarification + idempotent HTTP answers and a new
worker instance; eight public artifact bytes and checksums (also via HTTP GET);
physical ModelCall IDs/totals; event/attempt history; duplicate command delivery;
fresh services; and three fault recoveries (acceptance outage, checkpoint outage,
expired lease after acceptance) without another physical analysis call. The
policy-absent case proves diagnostic failure rather than fallback architecture.

Live-provider smoke and an external Celery broker/process crash are **not_run**.
Fresh service instances and controlled fault injection are not represented as
external broker delivery or a real model-quality evaluation. The runtime supports
a real OpenAI-compatible provider through environment configuration.

### Final verified results for Analysis → Architecture

Implementation SHA: **a196f76**, with contract compatibility in **de09852** and
preflight/diagnostic fixes in **0dd230b**. The following documentation commit does
not change executable code. Environment: Python 3.12.9, Linux, disposable
PostgreSQL 16 platform database, installed backend/requirements-worker.txt.

```sh
RWB_TEST_DATABASE_URL=<disposable PostgreSQL URL> PYTHONPATH=backend:backend/tests:. /tmp/rwb-e2e-venv/bin/python -m pytest -q backend/tests --tb=short
# 122 passed, 15 subtests passed; 94.65s; exit 0

RWB_TEST_DATABASE_URL=<disposable PostgreSQL URL> PYTHONPATH=backend:backend/tests:. /tmp/rwb-e2e-venv/bin/python -m pytest -q backend/tests/test_pipeline_execution.py --tb=short
# 7 passed; 41.43s; exit 0 (also included in the full suite above)

PYTHONPATH=backend:backend/app/agents:. /tmp/rwb-e2e-venv/bin/python -m unittest backend.app.agents.graph.test_graph backend.app.agents.graph.test_cross_version backend.app.agents.nodes.architecture.test_node backend.app.agents.generators.ui_spec.test_ui_spec backend.app.agents.requirements_analyst.tests.test_agent backend.app.agents.generation.tests.test_executor -q
# 144 tests OK; 46.523s; exit 0 (includes the 11 A-22 tests)

PYTHONPATH=backend:. /tmp/rwb-e2e-venv/bin/python -m contracts.agents.validate --check
# 11 schemas, 22 examples, 4 linked bundles, 1 snapshot; offline OK
```

`celery -A app.workers.pipeline:app worker --help` with test environment values
successfully loads the production entrypoint (exit 0); it does not start a worker
or prove broker availability. `compileall` for pipeline/worker and `git diff
--check` passed.

During development the new HTTP test caught an ambiguous PostgreSQL NULL parameter
in checkpoint reads, a Celery shared-task closure crossing service instances, and
an inconsistent controlled OpenAPI assumption. These were fixed. The broader agent
regression found changed error-code names and an obsolete expected physical call
for an invalid frozen path; error codes were preserved and only that preflight
case now expects zero physical calls. The final commands above all passed.

For a successful no-question Run the measured fixture uses **5 physical calls**
(1 analysis + 4 architecture), 500 provider-reported input tokens and 1000 output
tokens. Clarification uses **6 calls**, two immutable analysis attempts and one
architecture attempt. The controlled provider's token numbers verify accounting;
they are not real-model efficiency measurements. For each replay/fault case, the
transport call counter remains unchanged until the next genuinely new stage.

Rollback: stop dispatch/workers before changing execution code. Retain V10–V13,
artifacts, response receipts, call reservations and checkpoint data. Keep the
C-01 2.2 read DTOs and registered profile versions while contest Runs exist; an
unmodified pre-2.2 reader cannot decode that profile identity. Do not migrate
historical missing snapshots by inventing current rules or policies.

This iteration's requested Analysis → Architecture execution path is demonstrated.
Remaining work belongs to the later generation/assembly/verification milestones;
no full hackathon E2E, external broker/process-crash proof, live LLM quality,
Android, product build, GitLab, deployment or UI completion is claimed here.

### Shared environment configuration

Backend and production worker automatically load the repository root `.env`,
independently of the working directory. Copy `.env.example` to `.env` and set
`LLM_BASE_URL`, `LLM_MODEL`, and `LLM_API_KEY`. Existing process environment
variables take precedence. `.env` is ignored by Git; only the empty template is
committed. Database, broker and policy variables may also be placed in this file.

### Compose runtime incident: rate limit, manual retry and manifest readiness

The local provider returned `LLM_RATE_LIMITED` on the first analysis. Manual
HTTP retries then called `AnalysisGraph.start` for new attempt IDs against the
existing Run checkpoint, causing `RUN_STATE_CONFLICT`. Registered retries now
continue from the failed checkpoint, preserve immutable business inputs and
attempt history, and reuse the existing attempt on duplicate delivery. No new
Run or second queue is created. The physical provider rate limit still requires
provider capacity or a later retry.

The panel also treated `409 MANIFEST_NOT_READY` from listArtifacts as an
artifact failure. This expected response now shows pending analysis. Artifact
listing still requires a published manifest; this change does not manufacture
a snapshot or claim that assembly is implemented.

Validation: production PostgreSQL worker integration suite: 8 passed (47.58s),
including controlled model 429 → HTTP retry → accepted analysis and A-09 →
duplicate delivery without another call. AnalysisGraph regression suite: 26
tests passed. Angular analysis component: 3 tests passed. Controlled transport
was used for verification; no additional live model call was issued.

### Budget admission after repeated unknown-usage failures

The fifth local analysis attempt failed before dispatch: two earlier provider
429 responses had no usage, leaving conservative input reservations of 122803
each. Another identical reservation exceeds the frozen 300000 input limit.
This is internal admission exhaustion, not another provider response. Unknown
usage remains unknown and its reservation is retained.

Budget denial now returns a failed C-02 result with zero physical calls and a
specific `BUDGET_MAX_INPUT_TOKENS` diagnostic. Result and checkpoint are accepted,
Run transitions to `budget_exceeded` with `MAX_INPUT_TOKENS`, and unsupported
input-limit retries are rejected through the existing C-03 path. Failed stage
reasons now expose the accepted diagnostic instead of retaining WORKER_STARTED.
Historical attempts are not rewritten.

A registered retry can also replace pending graph work from a pre-result crash
only after the worker verifies that the previous C-03 attempt is durably failed.
This preserves the old receipts and uses the new registered attempt identity.

Validation: PostgreSQL pipeline suite 9 passed (51.48s), additional pre-result
crash/retry integration test 1 passed (7.82s), persistence suite 6 passed, and
AnalysisGraph regression suite 26 passed. All model verification used controlled
transport; no additional live provider call was made for this fix.

After rebuilding Compose, the existing local Run
`14cb554c-ee37-4b82-8568-81da61749651` was retried through authenticated HTTP
(`202`). Registered attempt 6 reached `BUDGET_MAX_INPUT_TOKENS`, and Run reached
`budget_exceeded / MAX_INPUT_TOKENS` without an orchestrator exception. The
physical ledger stayed at two rows, confirming no additional model dispatch.
The original five attempts and unknown-usage reservations were preserved.
