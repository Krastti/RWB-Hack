CREATE TABLE model_call_ledger (
    call_id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
    attempt_id UUID NOT NULL REFERENCES stage_attempts(id) ON DELETE RESTRICT,
    payload JSONB NOT NULL CHECK (jsonb_typeof(payload) = 'object')
);
CREATE INDEX model_call_ledger_run ON model_call_ledger(run_id);
