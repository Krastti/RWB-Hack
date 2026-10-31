"""Extract behavior models using a worker-owned ledger and receipt fencing."""
from dataclasses import replace
from uuid import UUID, uuid4, uuid5
from hashlib import sha256
from contracts.agents.validate import Contracts, loads
from ..llm import LLMAdapter
from .model import SCHEMA, ModelError, blockers, canonical, checksum, require, rule_id, validate_model


def artifact(model, *, attempt_id, profile, bound=False):
    validate_model(model)
    path,owner = ('behavior-bindings.json','architecture') if bound else ('behavior-model.json','analysis')
    require(profile.allows(owner,path), 'BEHAVIOR_PROFILE_GRANT_MISSING')
    raw = canonical(model)
    require(len(raw.encode()) <= profile.data['limits']['behavior']['max_model_bytes'], 'BEHAVIOR_SIZE_LIMIT')
    value = dict(artifact_id=str(uuid5(UUID(attempt_id),path+':'+str(model['version']))), attempt_id=attempt_id,
                 path=path,kind='behavior_model',media_type='application/json',version=model['version'],schema_version='1.0',author_agent=owner,
                 checksum=sha256(raw.encode()).hexdigest(),size_bytes=len(raw.encode()),related_fr_ids=sorted(c['fr_id'] for c in model['coverage']),content=raw)
    Contracts().validate('artifact',value)
    return value


class BehaviorNode:
    def __init__(self, *, config, ledger, transport=None):
        self.config,self.ledger,self.transport = config,ledger,transport

    async def run(self, request, *, attempt_id, context, profile, accepted_answers, previous=None):
        """Caller fences/replays StageAttempt before invoking. Never reuses call_id.

        accepted_answers is the owner-authorized B-12 snapshot keyed by question UUID,
        each {accepted: true, answer: str}; hypotheses must never enter this mapping.
        The returned partial artifact can enter clarification but diagnostics block codegen.
        Worker alone accepts/publishes result and performs lifecycle transitions.
        """
        Contracts().validate('agent-input',request)
        UUID(attempt_id)
        payload=loads(context.data)
        require(payload['agent_input']==request and context.manifest['profile']==profile.metadata and payload['profile']==profile.data, 'BEHAVIOR_CONTEXT_MISMATCH')
        require(profile.profile_id == 'web-fastapi-angular' and
                profile.profile_version in {'1.1.0', '1.2.0'} and
                profile.allows('analysis', 'behavior-model.json'), 'BEHAVIOR_PROFILE_MISMATCH')
        reqs=[loads(a['content']) for a in payload['artifacts'] if a['metadata']['kind']=='requirements']
        require(len(reqs)==1, 'BEHAVIOR_REQUIREMENTS_REQUIRED')
        requirements=reqs[0]
        Contracts().validate('requirements',requirements)
        require(all(v.get('accepted') is True and isinstance(v.get('answer'),str) for v in accepted_answers.values()), 'BEHAVIOR_UNCONFIRMED_ANSWER')
        if previous is not None:
            validate_model(previous)
            require(previous['contract_version']==request['contract_version'], 'BEHAVIOR_CONTRACT_MISMATCH')
        model_config={k:v for k,v in vars(self.config).items() if k!='api_key'}
        require(loads(payload['execution_inputs']['model_configuration'])==model_config, 'BEHAVIOR_MODEL_CONFIG_MISMATCH')
        limits=profile.data['limits']
        cap=min(self.config.max_output_tokens, request['limits']['max_output_tokens'],limits['agent']['max_output_tokens'])
        timeout=min(self.config.timeout_seconds,limits['model_call']['timeout_seconds'],request['limits'].get('timeout_seconds',limits['agent']['timeout_seconds']))
        require(request['limits']['max_calls']>0, 'BEHAVIOR_BUDGET_EXHAUSTED')
        config=replace(self.config,max_output_tokens=cap,timeout_seconds=timeout)
        system=context.system+'\nExtract only evidenced finite single-record rules. Unknown rights are unresolved, never allow by default. Preserve semantic decision_id across revisions. Assumptions cannot authorize critical actions. No architectural names or code strings.'
        prompt=canonical({'context':payload,'accepted_answers':accepted_answers,'previous_model':previous})
        accounting=dict(run_id=request['run_id'],attempt_id=attempt_id)
        call_id=str(uuid4())
        self.ledger.reserve(**accounting,call_id=call_id,model=config.model,system=system,prompt=prompt,schema=SCHEMA,max_output_tokens=cap,timeout_seconds=timeout,agent_limits=request['limits'])
        try:
            response=await LLMAdapter(config,transport=self.transport).generate(system=system,prompt=prompt,schema=SCHEMA)
        except BaseException:
            self.ledger.abandon(**accounting,call_id=call_id)
            raise
        self.ledger.finish(**accounting,call_id=call_id,result=response)
        outputs,diagnostics=[],[]
        try:
            require(response.ok, response.error.code if response.error else 'BEHAVIOR_LLM_FAILED')
            model=response.data
            # Header/provenance/version are server-owned, not model-controlled.
            require(isinstance(model,dict), 'BEHAVIOR_SCHEMA_INVALID')
            model.update(schema_version='1.0',version=previous['version']+1 if previous else 1,parent_checksum=checksum(previous) if previous else None,
                         contract_version=request['contract_version'],requirements_checksum=checksum(requirements),bindings=None)
            # Semantic IDs come from extraction; UUID representation is deterministic server code.
            mapping={r['id']:rule_id(r['decision_id']) for r in model['rules']}
            require(len(mapping)==len(model['rules']), 'BEHAVIOR_DUPLICATE_ID')
            for r in model['rules']:
                r['id']=mapping[r['id']]
            for a in model['actions']:
                a['guard_ids']=[mapping[r] for r in a['guard_ids']]
                a['effect_ids']=[mapping[r] for r in a['effect_ids']]
            model=validate_model(model,requirements,accepted_answers,max_bytes=limits['behavior']['max_model_bytes'])
            outputs=[artifact(model,attempt_id=attempt_id,profile=profile)]
            if blockers(model):
                diagnostics.append('BEHAVIOR_CRITICAL_UNRESOLVED')
        except ModelError as exc:
            diagnostics.append(str(exc))
        except (KeyError,TypeError,ValueError,RecursionError):
            diagnostics.append('BEHAVIOR_INVALID_RESPONSE')
        result=dict(schema_version='1.0',**{k:request[k] for k in ('run_id','stage_id','attempt','contract_version')},status='failed' if diagnostics else 'succeeded',
                    output_artifacts=outputs,questions=[],assumptions=[],usage=self.ledger.usage(**accounting),
                    diagnostics=[dict(code=c,message=c,severity='error',retryable=False,related_fr_ids=[]) for c in diagnostics])
        Contracts().validate('agent-result',result)
        return result
