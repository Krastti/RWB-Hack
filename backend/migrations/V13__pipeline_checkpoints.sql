CREATE TABLE pipeline_checkpoints (
 run_id UUID NOT NULL REFERENCES runs(id), ns TEXT NOT NULL, checkpoint_id TEXT NOT NULL,
 parent_id TEXT, checkpoint_type TEXT NOT NULL, checkpoint BYTEA NOT NULL,
 metadata_type TEXT NOT NULL, metadata BYTEA NOT NULL,
 PRIMARY KEY(run_id,ns,checkpoint_id)
);
CREATE TABLE pipeline_checkpoint_writes (
 run_id UUID NOT NULL REFERENCES runs(id), ns TEXT NOT NULL, checkpoint_id TEXT NOT NULL,
 task_id TEXT NOT NULL, idx INTEGER NOT NULL, task_path TEXT NOT NULL, channel TEXT NOT NULL,
 value_type TEXT NOT NULL, value BYTEA NOT NULL,
 PRIMARY KEY(run_id,ns,checkpoint_id,task_id,idx)
);
