-- Public Run/attempt/event rows remain authoritative; only private execution
-- inputs, control replay/outbox, aggregate version and lifecycle generations live here.
CREATE TABLE run_runtime_states (
    run_id UUID NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
    version BIGINT NOT NULL DEFAULT 1,
    payload JSONB NOT NULL,
    generations JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT pk_run_runtime_states PRIMARY KEY (run_id),
    CONSTRAINT ck_run_runtime_states_version_positive CHECK (version >= 1),
    CONSTRAINT ck_run_runtime_states_payload_object CHECK (jsonb_typeof(payload) = 'object'),
    CONSTRAINT ck_run_runtime_states_generations_object CHECK (jsonb_typeof(generations) = 'object')
);
