# Offline transfer from standalone clarifications V7

This procedure is only for the old #144 schema with V1–V6 plus
`V7__clarifications.sql`. It does not apply to runtime V7. No `repair`, history
edits, data deletion or checksum replacement is allowed.

1. Stop API, workers, beat and every writer. Keep them stopped until cutover.
   Take a full PostgreSQL custom-format backup and verify restoration to an
   isolated database. Keep the original database and its migration history.
2. Record source Flyway info/validate using the exact old release. Confirm all
   V1–V6 checksums and legacy V7 identity; investigate any failed migration.
3. Create a separate empty destination database with matching encoding, extensions
   and permissions. Run current Flyway migrate (V1–V9), then validate.
4. Set `SOURCE_DATABASE_URL` and `TARGET_DATABASE_URL` privately for the operator.
   Run `python backend/tools/migrate_legacy_clarifications.py`. The tool reads a
   read-only repeatable-read source snapshot and copies tables in FK order into
   one target transaction. It refuses nonempty targets/unknown tables and compares
   all original columns in primary-key order before committing. New V7/V8 columns
   receive their defaults. Flyway history is excluded; the source is unmodified.
5. Validate target Flyway again. Compare row counts, confirmed clarification
   answers, immutable inputs, replays, attempts, runtime payloads and events.
   Smoke-test owner-scoped reads and idempotent answers in the isolated target.
   Do not restart processing until these checks and backup verification pass.
6. Switch all services to the destination together. Keep the source read-only.
   Before any destination write, rollback is switching services back to source.
   After destination writes, switching back loses new work: stop writers and use
   a separately reviewed reconciliation/restore plan instead of silent rollback.

The procedure requires a maintenance window and space for both databases. It is
not an online upgrade and does not copy roles, grants or extension configuration;
provision those from the deployment configuration. Do not print database URLs or
row contents in logs. A failed copy rolls back the target transaction and leaves
the source available for investigation. Recreate an empty target before retrying
if an earlier copy already committed.
