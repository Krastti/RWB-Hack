CREATE TABLE artifact_snapshots (
    snapshot_id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
    manifest JSONB NOT NULL CHECK (jsonb_typeof(manifest) = 'object')
);
CREATE INDEX artifact_snapshots_run ON artifact_snapshots(run_id);
