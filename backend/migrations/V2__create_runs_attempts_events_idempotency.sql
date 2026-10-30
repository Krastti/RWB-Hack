-- B-05: C-01 v1.0.0 / lifecycle C-03 v1.0. Flyway owns the transaction.
-- Keep V1 unchanged; this composite key prevents cross-project revision references.
ALTER TABLE requirement_revisions ADD CONSTRAINT uq_requirement_revisions_id_project UNIQUE (id, project_id);

CREATE TABLE runs (
	id UUID NOT NULL,
	project_id UUID NOT NULL,
	revision_id UUID NOT NULL,
	profile_id TEXT NOT NULL,
	status VARCHAR(24) DEFAULT 'queued' NOT NULL,
	current_stage TEXT,
	limits JSONB NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	stopped_at TIMESTAMP WITH TIME ZONE,
	stop_reason TEXT,
	questions_revision INTEGER DEFAULT '0' NOT NULL,
	questions JSONB DEFAULT '[]'::jsonb NOT NULL,
	metrics JSONB DEFAULT '{"tokens_in": 0,"tokens_out": 0,"model_calls": 0,"attempts": 0,"manual_clarifications": 0,"duration_ms": 0}'::jsonb NOT NULL,
	CONSTRAINT pk_runs PRIMARY KEY (id),
	CONSTRAINT fk_runs_revision_project FOREIGN KEY(revision_id, project_id) REFERENCES requirement_revisions (id, project_id) ON DELETE RESTRICT,
	CONSTRAINT ck_runs_status CHECK (status IN ('queued', 'running', 'waiting_for_input', 'completed', 'cancelling', 'cancelled', 'failed', 'budget_exceeded')),
	CONSTRAINT ck_runs_profile_id_not_empty CHECK (length(profile_id) > 0),
	CONSTRAINT ck_runs_limits_object CHECK (jsonb_typeof(limits) = 'object'),
	CONSTRAINT ck_runs_questions_array CHECK (jsonb_typeof(questions) = 'array'),
	CONSTRAINT ck_runs_metrics_object CHECK (jsonb_typeof(metrics) = 'object'),
	CONSTRAINT ck_runs_questions_revision_nonnegative CHECK (questions_revision >= 0),
	CONSTRAINT fk_runs_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE RESTRICT
);

CREATE INDEX ix_runs_project_created_id ON runs (project_id, created_at, id);

CREATE TABLE stage_attempts (
	id UUID NOT NULL,
	run_id UUID NOT NULL,
	stage_id TEXT NOT NULL,
	attempt_no INTEGER NOT NULL,
	contract_version TEXT NOT NULL,
	input_hash VARCHAR(64) NOT NULL,
	status VARCHAR(24) DEFAULT 'pending' NOT NULL,
	started_at TIMESTAMP WITH TIME ZONE,
	finished_at TIMESTAMP WITH TIME ZONE,
	reason TEXT,
	error JSONB,
	CONSTRAINT pk_stage_attempts PRIMARY KEY (id),
	CONSTRAINT uq_stage_attempts_run_stage_attempt UNIQUE (run_id, stage_id, attempt_no),
	CONSTRAINT ck_stage_attempts_attempt_no_positive CHECK (attempt_no >= 1),
	CONSTRAINT ck_stage_attempts_stage_id_not_empty CHECK (length(stage_id) > 0),
	CONSTRAINT ck_stage_attempts_contract_version_not_empty CHECK (length(contract_version) > 0),
	CONSTRAINT ck_stage_attempts_input_hash_sha256 CHECK (input_hash ~ '^[a-f0-9]{64}$'),
	CONSTRAINT ck_stage_attempts_status CHECK (status IN ('pending', 'running', 'waiting_for_input', 'succeeded', 'failed', 'cancelled', 'skipped')),
	CONSTRAINT ck_stage_attempts_skipped_reason CHECK (status <> 'skipped' OR (reason IS NOT NULL AND length(reason) > 0)),
	CONSTRAINT fk_stage_attempts_run_id_runs FOREIGN KEY(run_id) REFERENCES runs (id) ON DELETE RESTRICT
);

CREATE TABLE run_events (
	run_id UUID NOT NULL,
	seq BIGINT NOT NULL,
	event_type TEXT NOT NULL,
	stage_id TEXT,
	timestamp TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	data JSONB NOT NULL,
	CONSTRAINT pk_run_events PRIMARY KEY (run_id, seq),
	CONSTRAINT ck_run_events_seq_positive CHECK (seq >= 1),
	CONSTRAINT ck_run_events_event_type_not_empty CHECK (length(event_type) > 0),
	CONSTRAINT ck_run_events_data_object CHECK (jsonb_typeof(data) = 'object'),
	CONSTRAINT fk_run_events_run_id_runs FOREIGN KEY(run_id) REFERENCES runs (id) ON DELETE RESTRICT
);

CREATE TABLE idempotency_records (
	id UUID NOT NULL,
	owner_id UUID NOT NULL,
	operation TEXT NOT NULL,
	resource_id UUID NOT NULL,
	key VARCHAR(128) NOT NULL,
	request_hash VARCHAR(64) NOT NULL,
	response_ref TEXT NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT pk_idempotency_records PRIMARY KEY (id),
	CONSTRAINT uq_idempotency_records_scope_key UNIQUE (owner_id, operation, resource_id, key),
	CONSTRAINT ck_idempotency_records_operation_not_empty CHECK (length(operation) > 0),
	CONSTRAINT ck_idempotency_records_key_format CHECK (length(key) BETWEEN 1 AND 128 AND key ~ '^[A-Za-z0-9._:-]+$'),
	CONSTRAINT ck_idempotency_records_request_hash_sha256 CHECK (request_hash ~ '^[a-f0-9]{64}$'),
	CONSTRAINT ck_idempotency_records_response_ref_not_empty CHECK (length(response_ref) > 0),
	CONSTRAINT fk_idempotency_records_owner_id_users FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE RESTRICT
);
