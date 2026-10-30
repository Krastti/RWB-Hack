-- Снимки и задание фиксируются одной транзакцией с Run и IdempotencyRecord.
CREATE TABLE run_input_snapshots (
    run_id UUID NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
    payload JSONB NOT NULL,
    CONSTRAINT pk_run_input_snapshots PRIMARY KEY (run_id),
    CONSTRAINT ck_run_input_snapshots_payload_object CHECK (jsonb_typeof(payload) = 'object')
);

CREATE TABLE run_creation_responses (
    id UUID NOT NULL,
    run_id UUID NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
    status_code INTEGER NOT NULL,
    body JSONB NOT NULL,
    CONSTRAINT pk_run_creation_responses PRIMARY KEY (id),
    CONSTRAINT uq_run_creation_responses_run_id UNIQUE (run_id),
    CONSTRAINT ck_run_creation_responses_accepted CHECK (status_code = 202),
    CONSTRAINT ck_run_creation_responses_body_object CHECK (jsonb_typeof(body) = 'object')
);

-- Исполнитель очереди подключается отдельно; записи переживают остановку API.
CREATE TABLE run_queue_jobs (
    id UUID NOT NULL,
    run_id UUID NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
    kind TEXT DEFAULT 'createRun' NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_run_queue_jobs PRIMARY KEY (id),
    CONSTRAINT uq_run_queue_jobs_run_id UNIQUE (run_id),
    CONSTRAINT ck_run_queue_jobs_kind CHECK (kind = 'createRun')
);

CREATE INDEX ix_run_queue_jobs_created_id ON run_queue_jobs (created_at, id);
