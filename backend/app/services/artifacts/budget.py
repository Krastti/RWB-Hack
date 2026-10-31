"""A-22 physical-call admission with conservative reservations and unknown usage."""
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from uuid import UUID
from sqlalchemy import select
from ...agents.generation import GenerationError
from ...agents.llm.adapter import request_payload
from ...agents.llm import LLMConfig
from ...models import Run
from .models import ModelCall


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def now():
    return datetime.now(timezone.utc).isoformat()


class PostgreSQLBudgetLedger:
    def __init__(self, artifacts):
        self.artifacts = artifacts

    def reserve(self, *, run_id, attempt_id, call_id, model, system, prompt, schema,
                max_output_tokens, timeout_seconds, agent_limits):
        # UTF-8 bytes bound the tokenizer input conservatively; includes A-01's
        # schema instruction and provider envelope, not only the subject prompt.
        # json_schema is the larger envelope even for json_object callers.
        envelope = request_payload(LLMConfig(base_url='https://unused.invalid/v1', model=model,
            api_key='unused', max_output_tokens=max_output_tokens, timeout_seconds=timeout_seconds),
            system=system, prompt=prompt, schema=schema)
        encoded = canonical(envelope).encode()
        reservation = {'input': len(encoded), 'output': max_output_tokens}
        with self.artifacts.execution.transaction() as session:
            self.artifacts._writing(session, run_id, attempt_id)
            run = session.get(Run, UUID(str(run_id)))  # lease transaction already holds Run lock
            old = session.get(ModelCall, UUID(call_id))
            if old is not None:
                raise GenerationError('MODEL_CALL_ALREADY_RESERVED')
            rows = list(session.scalars(select(ModelCall).where(ModelCall.run_id == run.id)))
            per_attempt = [r for r in rows if r.attempt_id == UUID(str(attempt_id))]
            def cost(items, key):
                return sum(r.payload['usage'][key + '_tokens'] if r.payload.get('usage') and
                           r.payload['usage'][key + '_tokens'] is not None else r.payload['reservation'][key]
                           for r in items)
            limits = self.artifacts.profile.data['limits']['run']
            if (len(rows) >= min(run.limits['max_calls'], limits['max_calls']) or
                    len(per_attempt) >= agent_limits['max_calls'] or
                    cost(rows, 'input') + reservation['input'] > limits['max_input_tokens'] or
                    cost(rows, 'output') + max_output_tokens > min(run.limits['max_output_tokens'], limits['max_output_tokens']) or
                    cost(per_attempt, 'output') + max_output_tokens > agent_limits['max_output_tokens']):
                raise GenerationError('BUDGET_EXCEEDED')
            session.add(ModelCall(call_id=UUID(call_id), run_id=run.id, attempt_id=UUID(str(attempt_id)),
                payload={'model': model, 'reservation': reservation, 'fingerprint': sha256(encoded).hexdigest(),
                         'started_at': now(), 'finished_at': None, 'status': 'reserved', 'usage': None}))

    def _settle(self, run_id, attempt_id, call_id, usage, status, response=None):
        with self.artifacts.execution.transaction() as session:
            self.artifacts._writing(session, run_id, attempt_id)
            row = session.get(ModelCall, UUID(call_id))
            if row is None or row.run_id != UUID(str(run_id)) or row.attempt_id != UUID(str(attempt_id)):
                raise GenerationError('MODEL_CALL_NOT_FOUND')
            value = deepcopy(row.payload)
            if value['status'] not in {'reserved', 'in_doubt'}:
                if value['usage'] != usage or value['status'] != status:
                    raise GenerationError('MODEL_CALL_SETTLEMENT_CONFLICT')
                return
            value.update(status=status, usage=usage, finished_at=now())
            if response is not None:
                value["response"] = response
            row.payload = value

    def finish(self, *, run_id, attempt_id, call_id, result):
        usage = result.usage
        if usage.calls != 1 or any(v is not None and (type(v) is not int or v < 0)
                                  for v in (usage.tokens_in, usage.tokens_out)) or not math.isfinite(usage.latency_ms) or usage.latency_ms < 0:
            raise GenerationError('MODEL_CALL_INVALID_USAGE')
        self._settle(run_id, attempt_id, call_id,
            {'input_tokens': usage.tokens_in, 'output_tokens': usage.tokens_out,
             'duration_seconds': usage.latency_ms / 1000},
            'succeeded' if result.ok else 'failed', response=__import__('dataclasses').asdict(result))

    def abandon(self, *, run_id, attempt_id, call_id):
        self._settle(run_id, attempt_id, call_id, None, 'in_doubt')

    def usage(self, *, run_id, attempt_id):
        with self.artifacts.sessions() as session:
            self.artifacts._scope(session, run_id)
            rows = list(session.scalars(select(ModelCall).where(ModelCall.run_id == UUID(str(run_id)),
                ModelCall.attempt_id == UUID(str(attempt_id))).order_by(ModelCall.call_id)))
            calls = []
            for row in rows:
                p = row.payload
                usage = p['usage'] or {'input_tokens': None, 'output_tokens': None,
                    'duration_seconds': 0}
                calls.append({'call_id': str(row.call_id), 'model': p['model'],
                    'outcome': p['status'] if p['status'] in {'succeeded', 'failed'} else 'cancelled', **usage})
            def total(key):
                return None if any(c[key] is None for c in calls) else sum(c[key] for c in calls)
            return {'calls': len(calls), 'model_calls': calls, 'input_tokens': total('input_tokens'),
                'output_tokens': total('output_tokens'), 'duration_seconds': total('duration_seconds')}
