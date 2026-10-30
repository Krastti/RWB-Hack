CREATE TABLE clarifications (
    run_id UUID NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
    questions_revision INTEGER NOT NULL CHECK (questions_revision >= 1),
    question_id UUID NOT NULL,
    question TEXT NOT NULL CHECK (length(question) > 0),
    reason TEXT NOT NULL CHECK (length(reason) > 0),
    related_fr_ids JSONB NOT NULL CHECK (jsonb_typeof(related_fr_ids) = 'array'),
    required BOOLEAN NOT NULL,
    answer TEXT CHECK (answer IS NULL OR length(answer) BETWEEN 1 AND 10000),
    PRIMARY KEY (run_id, questions_revision, question_id)
);
CREATE TABLE clarification_inputs (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
    payload JSONB NOT NULL CHECK (jsonb_typeof(payload) = 'object')
);
CREATE INDEX ix_clarification_inputs_run ON clarification_inputs(run_id);
CREATE TABLE answer_replays (
    run_id UUID NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
    key TEXT NOT NULL CHECK (length(key) BETWEEN 1 AND 128 AND key ~ '^[A-Za-z0-9._:-]+$'),
    request_hash TEXT NOT NULL CHECK (request_hash ~ '^[a-f0-9]{64}$'),
    response JSONB NOT NULL CHECK (jsonb_typeof(response) = 'object'),
    PRIMARY KEY (run_id, key)
);
