-- V4 зарезервирована для снимков создания Run из B-07 / PR #133.
-- Транспорт хранит одну команду на уже зарегистрированную StageAttempt.
CREATE TABLE worker_commands (
    id UUID NOT NULL,
    attempt_id UUID NOT NULL REFERENCES stage_attempts(id) ON DELETE RESTRICT,
    state VARCHAR(16) DEFAULT 'pending' NOT NULL,
    generation BIGINT DEFAULT 0 NOT NULL,
    owner_token UUID,
    expires_at TIMESTAMP WITH TIME ZONE,
    reason TEXT,
    next_delivery_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_worker_commands PRIMARY KEY (id),
    CONSTRAINT uq_worker_commands_attempt_id UNIQUE (attempt_id),
    CONSTRAINT ck_worker_commands_state CHECK (state IN ('pending', 'running', 'lost', 'recovering', 'completed', 'obsolete')),
    CONSTRAINT ck_worker_commands_generation_nonnegative CHECK (generation >= 0),
    CONSTRAINT ck_worker_commands_lease_state CHECK (
        (state IN ('running', 'recovering') AND owner_token IS NOT NULL AND expires_at IS NOT NULL)
        OR (state NOT IN ('running', 'recovering') AND owner_token IS NULL AND expires_at IS NULL)
    )
);

CREATE INDEX ix_worker_commands_delivery_id ON worker_commands (next_delivery_at, id);
