ALTER TABLE run_queue_jobs
    ADD COLUMN state TEXT NOT NULL DEFAULT 'pending',
    ADD COLUMN failures INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ADD COLUMN failure_code TEXT,
    ADD CONSTRAINT ck_run_queue_jobs_delivery_state CHECK (state IN ('pending', 'rejected')),
    ADD CONSTRAINT ck_run_queue_jobs_failures_bounded CHECK (failures BETWEEN 0 AND 3);
CREATE INDEX ix_run_queue_jobs_due ON run_queue_jobs (next_attempt_at, id) WHERE state = 'pending';
