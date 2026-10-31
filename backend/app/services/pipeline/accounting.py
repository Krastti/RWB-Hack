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
                from dataclasses import replace
                from hashlib import sha256
                from ..artifacts.budget import canonical
                expected = request_payload(replace(adapter._config, response_format='json_schema'), system, prompt, schema)
                if sha256(canonical(expected).encode()).hexdigest() != row.payload['fingerprint']:
                    raise ValueError('MODEL_CALL_REPLAY_INPUT_CHANGED')
                saved = row.payload['response']
                return LLMResult(data=saved['data'], usage=LLMUsage(**saved['usage']),
                    error=LLMError(**saved['error']) if saved['error'] else None)
        config = adapter._config
        self.ledger.reserve(**scope, model=config.model, system=system, prompt=prompt, schema=schema,
            max_output_tokens=config.max_output_tokens, timeout_seconds=config.timeout_seconds, agent_limits=self.limits)
        try:
            result = await adapter._generate(system=system, prompt=prompt, schema=schema)
        except BaseException:
            self.ledger.abandon(**scope)
            raise
        self.ledger.finish(**scope, result=result)
        return result
