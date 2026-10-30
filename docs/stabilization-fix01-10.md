# Stabilization FIX-01–FIX-10

Base main: f030c71 (PR #132 merged). Existing #109 head e5e2661 was applied
locally, without changing its branch. No new worker, ledger, generator, production
adapter, automatic endpoint wiring or CI was introduced. The existing optional
control router and local adapter come unchanged from #109 except the fixes listed.

| Fix | Change / evidence |
|---|---|
| 01 | Control Run accepts Angular by default and historical React unchanged. Every command/admission refuses historical profiles server-side (409 UNSUPPORTED_RUN_PROFILE). HTTP and read/control DTO regressions. |
| 02 | Only MAX_CALLS/MAX_OUTPUT_TOKENS allow public retry. Input/time/unknown causes fail closed (409 RETRY_NOT_SUPPORTED). Cause-specific increase, cumulative capacity, reservations and authorization all required. API/UI/C-01/C-03/C-05 aligned; no new limits API. |
| 03 | Completion fails closed without checkpoint_confirmed(record, attempt) returning exactly True for each latest succeeded attempt. Callback verifies actual durable confirmation; receipt/intent alone do not qualify. Existing guard still checks domain prerequisites and dependent-node admission. Accepted-state recovery test registers no new agent attempt. |
| 04 | Graph canonical hash excludes attempt and limits and sorts input references; requirements and confirmed answers still change hash. Old saved hashes are not silently migrated. |
| 05 | Control reuses lifecycle Run/stage transition tables and common event allocator. Watermark/history validation replaces len(events)+1. Transaction, concurrency, cancel replay and rollback regressions pass. Shared production write ownership remains #128. |
| 06 | Existing main project validation and graph answer validation preserve Unicode whitespace rules and original text. Existing regressions retained. The unmerged #133 RunCreate still lacks whitespace validation; it must be fixed in that component before integration (#127), not introduced here as a new API. |
| 07 | Original metrics fixes retained; additional route-change regression clears prior Run and unsubscribes old stream; complete flags never label unrelated GET totals complete. |
| 08 | Fresh Python 3.14 venv: one pip install -r backend/requirements-worker.txt and pip check pass; joint imports and offline suites pass. |
| 09 | All linked fixtures and grants pass. New test corrupts size/checksum and confirms rejection. |
| 10 | Control README versions/profile/module status updated. PR #133 PostgreSQL readiness checkbox corrected to unchecked SKIPPED; no PostgreSQL execution claimed. |

T-01/02/03/07: backend/tests/test_runtime_consistency.py ControlTests (including
HTTP refusal/DTO compatibility). T-04/05: graph test_input_identity_excludes_execution_controls
and test_confirmed_answers_change_input_identity. T-06: existing graph checkpoint-outage
regressions and lifecycle completion/recovery test. T-08: concurrent local adapter
events and rollback tests. T-09/10: existing test_consistency.py requirements tests.
T-11/12: Angular metrics/detail/monitor suites. T-13: clean installation. T-14/15:
contracts/agents/test_validate.py and validate.py --check.

Executed with /tmp/rwb-stabilize-env/bin/python (new venv):

- pip install -r backend/requirements-worker.txt: PASS, one resolver; pip check PASS.
- PYTHONPATH=backend python -m unittest discover -s backend/tests -v: 13 PASS, 2 SKIP.
- python -m unittest discover -s backend/app/agents/graph -p 'test_*.py' -v: 26 PASS.
- PYTHONPATH=backend/app/agents python -m unittest discover -s backend/app/agents/requirements_analyst/tests -p 'test_*.py' -v: 27 PASS.
- python -m unittest discover -s contracts/agents -p 'test_*.py' -v: 7 PASS.
- python contracts/agents/validate.py --check: PASS, 11 schemas/22 examples/4 bundles/1 snapshot.
- python -m backend.app.services.lifecycle.example and run_control.example: PASS.
- frontend npm ci PASS; tests/build rerun with explicit Node 24 binary PATH.
- Angular tests: 53 PASS; production build PASS. git diff --check PASS.

The initial npm ci invocation reported the system Node 22 engine warning; final
Angular tests/build used explicit Node 24. No audit-force/dependency bypass used.
Two PostgreSQL tests are SKIPPED without disposable RWB_TEST_DATABASE_URL; no
production data accessed and no paid LLM calls. No CI or real provider/E2E pass
is claimed. Review remains pending.

## External integration dependencies

#128 must provide one authoritative Run lock/store/writer and real confirmation
callback. Sharing allocation code does not allow independently stored control and
lifecycle aggregates to jointly own one Run. Do not connect both standalone local
adapters to the same Run. No general PostgreSQL write adapter is added here.

#127 must integrate write consumers, fix #133 RunCreate whitespace validation,
and reject invalid writes before persistence. Missing answers endpoints are not
implemented. #129 must produce normalized budget causes and reservation totals;
unknown causes safely reject retry until then. The graph's old persisted hashes
need controlled recovery when upgrading, not rewriting frozen input snapshots.
