"""C-02 bridge uses A-02's strict extraction/provenance and Markdown renderer.

The legacy requirements_analyst uses local 0.1 DTOs and Q-NNN IDs; its envelopes
cannot be emitted as C-02. This adapter reuses the accepted A-02 C-02 validator.
"""
import asyncio
from dataclasses import replace
from hashlib import sha256
import json
from uuid import UUID, uuid5
from ...agents.prompts.analysis import build_analysis_prompt, generate_analysis, render_system_analysis
from ...agents.llm import LLMAdapter
from contracts.agents.validate import Contracts


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def make_artifact(attempt_id, path, kind, content, owner, version, fr_ids=()):
    raw = content.encode()
    return dict(artifact_id=str(uuid5(UUID(attempt_id), path)), attempt_id=attempt_id, path=path, kind=kind,
        media_type='application/json' if path.endswith('.json') else 'text/markdown', version=version,
        schema_version='1.0', author_agent=owner, checksum=sha256(raw).hexdigest(), size_bytes=len(raw),
        related_fr_ids=list(fr_ids), content=content)


class AnalysisNode:
    def __init__(self, store, config, *, transport=None):
        self.store, self.config, self.transport = store, config, transport

    def __call__(self, request, *, attempt_id):
        inputs = [self.store.read(request['run_id'], a) for a in request['input_artifact_ids']]
        source = json.loads(next(a['content'] for a in inputs if a['kind'] == 'business_requirements'))
        previous = next((json.loads(a['content']) for a in inputs if a['kind'] == 'requirements'), None)
        prepared = build_analysis_prompt(request, source, previous_requirements=previous,
                                         frozen_profile=self.store.profile.data)
        from ...agents.context import ContextBuilder
        prepared_context = ContextBuilder(self.store, max_context_bytes=1048576).prepare(request,
            owner_id=str(self.store.owner_id), owner_agent='analysis', profile=self.store.profile,
            references={a['artifact_id']: {k:v for k,v in a.items() if k != 'content'} for a in inputs},
            execution_inputs={'prompt': prepared.prompt.encode(), 'template': b'',
                'model_configuration': canonical({k:v for k,v in vars(self.config).items() if k != 'api_key'}).encode(),
                'compiler_configuration': canonical(self.store.profile.data['stack']).encode()})
        prepared = replace(prepared, system=prepared_context.system + '\n' + prepared.system)
        config = replace(self.config, max_output_tokens=min(self.config.max_output_tokens, prepared.max_output_tokens),
                         timeout_seconds=min(self.config.timeout_seconds, prepared.timeout_seconds))
        response = asyncio.run(generate_analysis(LLMAdapter(config, transport=self.transport), prepared))
        # The A-01 call ledger survives even if draft validation rejects the response.
        from ..artifacts.budget import PostgreSQLBudgetLedger
        usage = PostgreSQLBudgetLedger(self.store).usage(run_id=request['run_id'], attempt_id=attempt_id)
        result = {k: request[k] for k in ('schema_version', 'run_id', 'stage_id', 'attempt', 'contract_version')}
        if not response.ok:
            result.update(status='failed', output_artifacts=[], questions=[], assumptions=[], usage=usage,
                diagnostics=[dict(code=response.error.code, message='Analysis response rejected', severity='error',
                                  retryable=response.error.retryable, related_fr_ids=[])])
        else:
            draft = response.data
            req = draft['requirements']
            pending = any(q['required'] and 'answer' not in q for q in req['questions'])
            fr_ids = [r['id'] for r in req['functional_requirements']]
            documents = [('requirements.json', 'requirements', canonical(req)),
                ('questions.json', 'questions', canonical({'schema_version': '1.0',
                    'questions_revision': req['questions_revision'], 'questions': req['questions']})),
                ('docs/system-analysis.md', 'system_analysis', render_system_analysis(draft, prepared))]
            result.update(status='waiting_for_input' if pending else 'succeeded',
                output_artifacts=[make_artifact(attempt_id, p, k, c, 'analysis', request['attempt'], fr_ids)
                                  for p, k, c in documents], questions=req['questions'],
                assumptions=req['assumptions'], usage=usage, diagnostics=[])
        Contracts().validate('agent-result', result)
        return result
