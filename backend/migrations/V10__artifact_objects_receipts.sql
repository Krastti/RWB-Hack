-- Bytes and their acceptance receipts commit in one fenced SQL transaction.
CREATE TABLE artifact_objects (
    artifact_id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
    attempt_id UUID NOT NULL REFERENCES stage_attempts(id) ON DELETE RESTRICT,
    metadata_json JSONB NOT NULL CHECK (jsonb_typeof(metadata_json) = 'object'),
    content BYTEA NOT NULL,
    UNIQUE (run_id, attempt_id, artifact_id)
);
CREATE INDEX artifact_objects_run ON artifact_objects(run_id);
CREATE TABLE execution_receipts (
    attempt_id UUID NOT NULL REFERENCES stage_attempts(id) ON DELETE RESTRICT,
    key VARCHAR(128) NOT NULL,
    payload JSONB NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    PRIMARY KEY (attempt_id, key)
);
