ALTER TABLE worker_commands
    ADD COLUMN recovery_failures BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN recovery_error TEXT,
    ADD CONSTRAINT ck_worker_commands_recovery_failures_bounded CHECK (recovery_failures BETWEEN 0 AND 3);
