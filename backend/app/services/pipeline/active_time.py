"""Durable active wall time: queue/user waits and crash downtime are excluded.

An unclosed interval ends at its last persisted lease deadline. Heartbeats extend
only the current fenced generation. Retries never erase previous intervals.
"""
from sqlalchemy import text
from ...adapters.queue.models import WorkerCommand


def union_seconds(intervals):
    total, end = 0.0, None
    for start, stop in sorted(intervals):
        if stop <= start:
            continue
        total += max(0, (stop - max(start, end or start)).total_seconds())
        end = max(stop, end or stop)
    return total


class ActiveTime:
    def __init__(self, execution, profile):
        self.execution, self.profile = execution, profile
        command = execution.lease.command
        self.scope = dict(attempt=command.attempt_id, generation=execution.lease.generation,
                          run=command.run_id, stage=command.stage_id)

    def start(self):
        with self.execution.transaction() as session:
            lease = session.get(WorkerCommand, self.execution.lease.command.command_id)
            session.execute(text('INSERT INTO pipeline_active_intervals '
                '(attempt_id,generation,run_id,stage_id,started_at,lease_until) '
                'VALUES (:attempt,:generation,:run,:stage,clock_timestamp(),:until) ON CONFLICT DO NOTHING'),
                self.scope | dict(until=lease.expires_at))
        self.execution.activity_clock = self

    def heartbeat(self):
        with self.execution.transaction() as session:
            lease = session.get(WorkerCommand, self.execution.lease.command.command_id)
            session.execute(text('UPDATE pipeline_active_intervals SET lease_until=:until '
                'WHERE attempt_id=:attempt AND generation=:generation AND ended_at IS NULL'),
                self.scope | dict(until=lease.expires_at))

    def stop(self):
        self.execution.activity_clock = None
        with self.execution.transaction() as session:
            session.execute(text('UPDATE pipeline_active_intervals SET ended_at=LEAST(clock_timestamp(),lease_until) '
                'WHERE attempt_id=:attempt AND generation=:generation AND ended_at IS NULL'), self.scope)

    def remaining(self, session, agent_limits):
        current = session.scalar(text('SELECT clock_timestamp()'))
        rows = session.execute(text('SELECT * FROM pipeline_active_intervals WHERE run_id=:run'), self.scope).mappings().all()
        intervals = [(r['started_at'], min(r['ended_at'] or current, r['lease_until'], current)) for r in rows]
        stage = [(r['started_at'], min(r['ended_at'] or current, r['lease_until'], current))
                 for r in rows if r['stage_id'] == self.scope['stage']]
        return (self.profile.data['limits']['run']['timeout_seconds'] - union_seconds(intervals),
                min(agent_limits.get('timeout_seconds', self.profile.data['limits']['agent']['timeout_seconds']),
                    self.profile.data['limits']['agent']['timeout_seconds']) - union_seconds(stage))
