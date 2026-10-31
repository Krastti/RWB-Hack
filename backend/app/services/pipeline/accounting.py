"""A-01 dispatch accounting context shared by analysis and A-09 child adapters."""
from uuid import UUID, uuid5
from ...models.dto.llm import LLMResult, LLMUsage
from ...errors.llm import LLMError
from ..artifacts.models import ModelCall

from backend.app.agents.llm.accounting import active_accounting


class AccountedCalls:
    def __init__(self, ledger, run_id, attempt_id, limits):
        self.ledger, self.run_id, self.attempt_id, self.limits = ledger, run_id, attempt_id, limits
        self.index = 0

    async def generate(self, adapter, system, prompt, schema):
        call_id = str(uuid5(UUID(self.attempt_id), 'physical-call:' + str(self.index)))
        self.index += 1
        scope = dict(run_id=self.run_id, attempt_id=self.attempt_id, call_id=call_id)
        with self.ledger.artifacts.sessions() as s:
            row = s.get(ModelCall, UUID(call_id))
            if row and row.payload.get('response'):
                from ...agents.llm.adapter import request_payload
                from hashlib import sha256
                from ..artifacts.budget import canonical
                expected = request_payload(adapter._config, system, prompt, schema)
                if sha256(canonical(expected).encode()).hexdigest() != row.payload['fingerprint']:
                    raise ValueError('MODEL_CALL_REPLAY_INPUT_CHANGED')
                saved = row.payload['response']
                return LLMResult(data=saved['data'], usage=LLMUsage(**saved['usage']),
                    error=LLMError(**saved['error']) if saved['error'] else None,
                    candidate=saved.get('candidate'), validation_feedback=tuple(saved.get('validation_feedback', ())))
        config = adapter._config
        from importlib import import_module
        BudgetAdmissionError = import_module(type(self.ledger).__module__).BudgetAdmissionError
        import asyncio, os
        from hashlib import sha256
        module = import_module(type(self.ledger).__module__)
        interval = 0 if adapter._transport is not None else float(os.environ.get('LLM_MIN_CALL_INTERVAL_SECONDS', '0'))
        rate_scope = sha256((config.base_url + ':' + config.api_key).encode()).hexdigest()
        try:
            while True:
                try:
                    admitted_timeout = self.ledger.reserve(**scope, model=config.model, system=system, prompt=prompt, schema=schema,
                        max_output_tokens=config.max_output_tokens, timeout_seconds=config.timeout_seconds, agent_limits=self.limits,
                        response_format=config.response_format, enable_thinking=config.enable_thinking,
                        minimum_interval_seconds=interval, rate_scope=rate_scope)
                    break
                except module.RateAdmissionDeferred as deferred:
                    await asyncio.sleep(min(deferred.seconds, 30))
        except BudgetAdmissionError as error:
            # Admission denied: no physical call and no fabricated usage row.
            return LLMResult(usage=LLMUsage(), error=LLMError('BUDGET_' + error.reason,
                'Model call was not sent because the durable budget is exhausted.'))
        except module.CallAdmissionStopped as error:
            return LLMResult(usage=LLMUsage(), error=LLMError(error.code,
                'Model call was not sent because the Run no longer admits new work.'))
        try:
            if admitted_timeout is not None and admitted_timeout < config.timeout_seconds:
                from dataclasses import replace
                from ...agents.llm import LLMAdapter
                adapter = LLMAdapter(replace(config, timeout_seconds=admitted_timeout), transport=adapter._transport)
            result = await adapter._generate(system=system, prompt=prompt, schema=schema)
        except BaseException:
            self.ledger.abandon(**scope)
            raise
        result = self.ledger.deadline_result(result, self.limits)
        self.ledger.finish(**scope, result=result)
        return result
