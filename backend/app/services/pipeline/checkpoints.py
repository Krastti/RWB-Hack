"""Fenced PostgreSQL LangGraph saver. Checkpoints and pending writes survive workers."""
from uuid import UUID
from sqlalchemy import text
from langgraph.checkpoint.base import BaseCheckpointSaver, CheckpointTuple, WRITES_IDX_MAP
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer


class PostgreSQLCheckpoints(BaseCheckpointSaver):
    def __init__(self, artifacts):
        super().__init__(serde=JsonPlusSerializer(pickle_fallback=False,
            allowed_msgpack_modules=[('langgraph.types', 'Interrupt')]))
        self.artifacts = artifacts

    def scope(self, config):
        c = config['configurable']
        if UUID(c['thread_id']) != self.artifacts.execution.lease.command.run_id:
            raise ValueError('CHECKPOINT_SCOPE_MISMATCH')
        return c['thread_id'], c.get('checkpoint_ns', '')

    def get_tuple(self, config):
        run, ns = self.scope(config)
        with self.artifacts.sessions() as s:
            self.artifacts._scope(s, run)
            params = dict(run=run, ns=ns, id=config['configurable'].get('checkpoint_id'))
            row = s.execute(text('SELECT * FROM pipeline_checkpoints WHERE run_id=:run AND ns=:ns '
                'AND (CAST(:id AS TEXT) IS NULL OR checkpoint_id=:id) ORDER BY checkpoint_id DESC LIMIT 1'), params).mappings().first()
            if row is None:
                return None
            writes = s.execute(text('SELECT * FROM pipeline_checkpoint_writes WHERE run_id=:run AND ns=:ns '
                'AND checkpoint_id=:id ORDER BY task_path, task_id, idx'), {**params, 'id': row['checkpoint_id']}).mappings()
            def cfg(identity):
                return {'configurable': {'thread_id': run, 'checkpoint_ns': ns, 'checkpoint_id': identity}}
            return CheckpointTuple(config=cfg(row['checkpoint_id']),
                checkpoint=self.serde.loads_typed((row['checkpoint_type'], row['checkpoint'])),
                metadata=self.serde.loads_typed((row['metadata_type'], row['metadata'])),
                parent_config=cfg(row['parent_id']) if row['parent_id'] else None,
                pending_writes=[(w['task_id'], w['channel'], self.serde.loads_typed((w['value_type'], w['value']))) for w in writes])

    def put(self, config, checkpoint, metadata, new_versions):
        run, ns = self.scope(config)
        ct, cb = self.serde.dumps_typed(checkpoint)
        mt, mb = self.serde.dumps_typed(metadata)
        with self.artifacts.execution.transaction() as s:
            self.artifacts._writing(s, run, self.artifacts.execution.lease.command.attempt_id)
            s.execute(text('INSERT INTO pipeline_checkpoints '
                '(run_id, ns, checkpoint_id, parent_id, checkpoint_type, checkpoint, metadata_type, metadata) '
                'VALUES (:run,:ns,:id,:parent,:ct,:cb,:mt,:mb) ON CONFLICT DO NOTHING'),
                dict(run=run, ns=ns, id=checkpoint['id'], parent=config['configurable'].get('checkpoint_id'), ct=ct, cb=cb, mt=mt, mb=mb))
        return {'configurable': {'thread_id': run, 'checkpoint_ns': ns, 'checkpoint_id': checkpoint['id']}}

    def put_writes(self, config, writes, task_id, task_path=''):
        run, ns = self.scope(config)
        with self.artifacts.execution.transaction() as s:
            for i, (channel, value) in enumerate(writes):
                vt, vb = self.serde.dumps_typed(value)
                idx = WRITES_IDX_MAP.get(channel, i)
                conflict = 'DO UPDATE SET channel=EXCLUDED.channel,value_type=EXCLUDED.value_type,value=EXCLUDED.value' if idx < 0 else 'DO NOTHING'
                s.execute(text('INSERT INTO pipeline_checkpoint_writes '
                    '(run_id,ns,checkpoint_id,task_id,idx,task_path,channel,value_type,value) '
                    'VALUES (:run,:ns,:id,:task,:idx,:path,:channel,:vt,:vb) ON CONFLICT '
                    '(run_id,ns,checkpoint_id,task_id,idx) ' + conflict),
                    dict(run=run, ns=ns, id=config['configurable']['checkpoint_id'], task=task_id,
                         idx=idx, path=task_path, channel=channel, vt=vt, vb=vb))
