CREATE TABLE pipeline_active_intervals (
 attempt_id UUID NOT NULL REFERENCES stage_attempts(id),
 generation BIGINT NOT NULL CHECK (generation > 0),
 run_id UUID NOT NULL REFERENCES runs(id),
 stage_id TEXT NOT NULL,
 started_at TIMESTAMPTZ NOT NULL,
 lease_until TIMESTAMPTZ NOT NULL,
 ended_at TIMESTAMPTZ,
 PRIMARY KEY (attempt_id, generation),
 CHECK (lease_until >= started_at),
 CHECK (ended_at IS NULL OR ended_at >= started_at)
);
CREATE INDEX ix_pipeline_active_run ON pipeline_active_intervals(run_id);
