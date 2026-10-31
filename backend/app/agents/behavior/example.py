"""Offline verification, including A-01 with explicitly synthetic HTTP responses."""
import asyncio
from copy import deepcopy
import json
from uuid import uuid4
import httpx
from .fixtures import example_model, bound_example
from .model import ModelError, canonical, checksum, validate_model
from .profile import behavior_profile
from .node import BehaviorNode, artifact
from ..context import ContextBuilder, ResolvedArtifact
from ..context.example import FixtureResolver, OWNER
from ..llm import LLMConfig
from ..generation.example import ExampleLedger


def prepare_example(requirements, *, profile, config):
    run_id = str(uuid4())
    text = '\n'.join(item['text'] for item in requirements['functional_requirements'])
    business = dict(schema_version='1.0', questions_revision=0, answers=[],
                    requirements_text=text,
                    previous_requirements_artifact_id=None, analysis_rules_artifact_id=None)
    inputs = []
    for path, kind, owner, payload in [
        ('requirements.json', 'requirements', 'analysis', requirements),
        ('inputs/business.json', 'business_requirements', 'orchestrator', business),
    ]:
        raw = canonical(payload).encode('utf-8')
        metadata = dict(artifact_id=str(uuid4()), attempt_id=str(uuid4()), path=path, kind=kind,
                        media_type='application/json', version=1, schema_version='1.0', author_agent=owner,
                        checksum=checksum(payload), size_bytes=len(raw), related_fr_ids=[])
        inputs.append(ResolvedArtifact(OWNER, run_id, 'demo-v1', True, metadata, raw))
    request = dict(schema_version='1.0', run_id=run_id, stage_id='behavior_model', attempt=1,
                   input_artifact_ids=[item.metadata['artifact_id'] for item in inputs],
                   contract_version='demo-v1', profile_id=profile.profile_id,
                   limits={'max_calls': 1, 'max_output_tokens': 4096, 'timeout_seconds': 10})
    model_config = {key: value for key, value in vars(config).items() if key != 'api_key'}
    configuration = canonical(model_config).encode('utf-8')
    context = ContextBuilder(FixtureResolver(inputs), max_context_bytes=262144).prepare(
        request, owner_id=OWNER, owner_agent='analysis', profile=profile,
        references={item.metadata['artifact_id']: item.metadata for item in inputs},
        execution_inputs={'prompt': b'Extract the finite behavior model.', 'template': b'',
                          'model_configuration': configuration, 'compiler_configuration': b'{}'})
    return request, context, configuration


async def main():
    req,model=example_model()
    validate_model(model,req)
    partial_req,partial=example_model(unresolved=True)
    validate_model(partial,partial_req)
    _,bound=bound_example()
    assert bound['version']==2 and bound['parent_checksum']==checksum(model)
    profile=behavior_profile()
    artifact(bound,attempt_id=str(uuid4()),profile=profile,bound=True)
    bad=[]
    for mutation in ('operator','reference','conflict','coverage','evidence','critical_assumption'):
        value=deepcopy(model)
        if mutation=='operator': value['rules'][0]['expression']['op']='eval'
        if mutation=='reference': value['rules'][0]['expression']={'op':'ref','scope':'state','name':'absent'}
        if mutation=='conflict': value['actions'][0]['effect_ids']*=2
        if mutation=='coverage': value['coverage']=[]
        if mutation=='evidence': value['rules'][0]['evidence'][0]['quote']='invented'
        if mutation=='critical_assumption': value['rules'][0]['evidence'][0]['source']='assumption'
        try: validate_model(value,req)
        except ModelError: bad.append(mutation)
        else: raise AssertionError(mutation)
    config=LLMConfig(base_url='https://fixture.invalid/v1',model='fixture',api_key='fixture',max_output_tokens=4096,timeout_seconds=10)
    results=[]
    for requirements,response_model in [(req,model),(partial_req,partial)]:
        request, context, configuration = prepare_example(requirements, profile=profile, config=config)
        def handler(_):
            return httpx.Response(200,json={'choices':[{'message':{'content':canonical(response_model)},'finish_reason':'stop'}],'usage':{'prompt_tokens':100,'completion_tokens':40,'total_tokens':140}})
        ledger=ExampleLedger()
        result=await BehaviorNode(config=config,ledger=ledger,transport=httpx.MockTransport(handler)).run(
            request,attempt_id=str(uuid4()),context=context,profile=profile,accepted_answers={},
            model_configuration=configuration)
        assert len(result['output_artifacts'])==1,result
        results.append(result['status'])
    assert results==['succeeded','failed']
    print(json.dumps({'node_results':results,'negative_cases':bad,'model_checksum':checksum(model),'partial_blocked':True,'bound_version':bound['version']}))


if __name__=='__main__':
    asyncio.run(main())
