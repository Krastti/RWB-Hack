-- B-01: platform tables for C-01 v1.0.0. Flyway manages the transaction.
CREATE TABLE users (
    id UUID NOT NULL,
    login VARCHAR(120) NOT NULL,
    password_hash TEXT NOT NULL,
    status VARCHAR(16) DEFAULT 'active' NOT NULL,
    CONSTRAINT pk_users PRIMARY KEY (id),
    CONSTRAINT uq_users_login UNIQUE (login),
    CONSTRAINT ck_users_login_length CHECK (length(login) BETWEEN 1 AND 120),
    CONSTRAINT ck_users_password_hash_not_empty CHECK (length(password_hash) > 0),
    CONSTRAINT ck_users_status CHECK (status IN ('active', 'disabled'))
);

CREATE TABLE projects (
    id UUID NOT NULL,
    owner_id UUID NOT NULL,
    name VARCHAR(120) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_projects PRIMARY KEY (id),
    CONSTRAINT fk_projects_owner_id_users FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE RESTRICT,
    CONSTRAINT ck_projects_name_length CHECK (length(name) BETWEEN 1 AND 120)
);

CREATE INDEX ix_projects_owner_id ON projects (owner_id);

CREATE TABLE requirement_revisions (
    id UUID NOT NULL,
    project_id UUID NOT NULL,
    number INTEGER NOT NULL,
    requirements_text TEXT NOT NULL,
    hash VARCHAR(64) NOT NULL,
    CONSTRAINT pk_requirement_revisions PRIMARY KEY (id),
    CONSTRAINT fk_requirement_revisions_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE RESTRICT,
    CONSTRAINT uq_requirement_revisions_project_number UNIQUE (project_id, number),
    CONSTRAINT ck_requirement_revisions_number_positive CHECK (number >= 1),
    CONSTRAINT ck_requirement_revisions_text_length CHECK (length(requirements_text) BETWEEN 1 AND 100000),
    CONSTRAINT ck_requirement_revisions_hash_sha256 CHECK (hash ~ '^[a-f0-9]{64}$')
);
