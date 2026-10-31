"""A-22 physical-call admission with conservative reservations and unknown usage."""
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from uuid import UUID
from sqlalchemy import select, text
from ...agents.generation import GenerationError
from ...agents.llm.adapter import request_payload
from ...agents.llm import LLMConfig
from ...models import Run
from ..runtime.models import RunRuntimeState
from .models import ModelCall


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def now():
    return datetime.now(timezone.utc).isoformat()


class RateAdmissionDeferred(Exception):
    def __init__(self, seconds):
        self.seconds = seconds


class BudgetAdmissionError(GenerationError):
    def __init__(self, reason):
        super().__init__('BUDGET_EXCEEDED')
        self.reason = reason


class CallAdmissionStopped(GenerationError):
    """New work is forbidden; already admitted responses can still settle."""


def token_cost(rows, key):
    return sum(r.payload['usage'][key + '_tokens'] if r.payload.get('usage') and
               r.payload['usage'][key + '_tokens'] is not None else r.payload['reservation'][key]
               for r in rows)


class PostgreSQLBudgetLedger:
    def __init__(self, artifacts):
        self.artifacts = artifacts

    def reserve(self, *, run_id, attempt_id, call_id, model, system, prompt, schema,
                max_output_tokens, timeout_seconds, agent_limits, response_format="json_schema", enable_thinking=None, minimum_interval_seconds=0, rate_scope=None):
        # UTF-8 bytes bound the tokenizer input conservatively; includes A-01's
        # schema instruction and provider envelope, not only the subject prompt.
        envelope = request_payload(LLMConfig(base_url='https://unused.invalid/v1', model=model,
            api_key='unused', max_output_tokens=max_output_tokens, timeout_seconds=timeout_seconds,
            response_format=response_format, enable_thinking=enable_thinking),
            system=system, prompt=prompt, schema=schema)
        encoded = canonical(envelope).encode()
        reservation = {'input': len(encoded), 'output': max_output_tokens}
        with self.artifacts.execution.transaction() as session:
            self.artifacts._writing(session, run_id, attempt_id)
            run = session.get(Run, UUID(str(run_id)))  # lease transaction already holds Run lock
            state = session.get(RunRuntimeState, run.id)
            if run.status != 'running':
                raise CallAdmissionStopped('RUN_CANCELLED' if run.status in {'cancelling', 'cancelled'} else 'RUN_NOT_RUNNING')
            if state and state.payload.get('saved_state', {}).get('terminal_intent'):
                raise CallAdmissionStopped('RUN_STOPPING')
            clock = getattr(self.artifacts.execution, 'activity_clock', None)
            if clock is not None:
                remaining_run, remaining_agent = clock.remaining(session, agent_limits)
                if remaining_run <= 0:
                    raise BudgetAdmissionError('RUN_TIMEOUT')
                if remaining_agent <= 0:
                    raise BudgetAdmissionError('AGENT_TIMEOUT')
                timeout_seconds = min(timeout_seconds, remaining_run, remaining_agent,
                                      self.artifacts.profile.data['limits']['model_call']['timeout_seconds'])
            if minimum_interval_seconds and rate_scope:
                session.execute(text('SELECT pg_advisory_xact_lock(hashtext(:scope))'), {'scope': rate_scope})
                latest = session.scalar(select(ModelCall).where(ModelCall.payload['rate_scope'].astext == rate_scope)
                    .order_by(ModelCall.payload['started_at'].astext.desc()).limit(1))
                if latest:
                    elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(latest.payload['started_at'])).total_seconds()
                    if elapsed < minimum_interval_seconds:
                        raise RateAdmissionDeferred(minimum_interval_seconds - elapsed)
            old = session.get(ModelCall, UUID(call_id))
            if old is not None:
                raise GenerationError('MODEL_CALL_ALREADY_RESERVED')
            rows = list(session.scalars(select(ModelCall).where(ModelCall.run_id == run.id)))
            per_attempt = [r for r in rows if r.attempt_id == UUID(str(attempt_id))]
            limits = self.artifacts.profile.data['limits']['run']
            checks = [
                (len(rows) >= min(run.limits['max_calls'], limits['max_calls']), 'MAX_CALLS'),
                (len(per_attempt) >= agent_limits['max_calls'], 'AGENT_MAX_CALLS'),
                (token_cost(rows, 'input') + reservation['input'] > limits['max_input_tokens'], 'MAX_INPUT_TOKENS'),
                (token_cost(rows, 'output') + max_output_tokens > min(run.limits['max_output_tokens'], limits['max_output_tokens']), 'MAX_OUTPUT_TOKENS'),
                (token_cost(per_attempt, 'output') + max_output_tokens > agent_limits['max_output_tokens'], 'AGENT_MAX_OUTPUT_TOKENS'),
            ]
            for exceeded, reason in checks:
                if exceeded:
                    raise BudgetAdmissionError(reason)
            session.add(ModelCall(call_id=UUID(call_id), run_id=run.id, attempt_id=UUID(str(attempt_id)),
                payload={'model': model, 'rate_scope': rate_scope, 'reservation': reservation, 'fingerprint': sha256(encoded).hexdigest(),
                         'started_at': now(), 'finished_at': None, 'status': 'reserved', 'usage': None}))
        return timeout_seconds

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

    def deadline_result(self, result, agent_limits):
        """Preserve physical usage, but reject a response after the active budget."""
        clock = getattr(self.artifacts.execution, 'activity_clock', None)
        if clock is None:
            return result
        with self.artifacts.execution.transaction() as session:
            run_remaining, agent_remaining = clock.remaining(session, agent_limits)
        if run_remaining > 0 and agent_remaining > 0:
            return result
        from dataclasses import replace
        from ...errors.llm import LLMError
        reason = 'RUN_TIMEOUT' if run_remaining <= 0 else 'AGENT_TIMEOUT'
        return replace(result, data=None, error=LLMError('BUDGET_' + reason, 'Active execution time is exhausted.'))

    def abandon(self, *, run_id, attempt_id, call_id):
        self._settle(run_id, attempt_id, call_id, None, 'in_doubt')

    def replay(self, *, run_id, attempt_id, config, system, prompt, schema):
        """Reuse a settled physical response before batch acceptance, within one attempt."""
        fingerprint = sha256(canonical(request_payload(config, system, prompt, schema)).encode()).hexdigest()
        with self.artifacts.sessions() as session:
            self.artifacts._scope(session, run_id)
            row = session.scalar(select(ModelCall).where(ModelCall.run_id == UUID(str(run_id)),
                ModelCall.attempt_id == UUID(str(attempt_id)), ModelCall.payload['fingerprint'].astext == fingerprint,
                ModelCall.payload['status'].astext.in_(['succeeded','failed']))
                .order_by(ModelCall.payload['started_at'].astext.desc()).limit(1))
            if row is None or not row.payload.get('response'):
                return None
            from ...models.dto.llm import LLMResult, LLMUsage
            from ...errors.llm import LLMError
            value = row.payload['response']
            result = LLMResult(data=value['data'],usage=LLMUsage(**value['usage']),
                error=LLMError(**value['error']) if value['error'] else None,
                candidate=value.get('candidate'),validation_feedback=tuple(value.get('validation_feedback', ())))
            return str(row.call_id), result

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
