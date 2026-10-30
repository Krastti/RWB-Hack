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
from ..context import PreparedContext
from ..llm import LLMConfig
from ..generation.example import ExampleLedger


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
    # Hand-built PreparedContext fixture: resolver authorization is tested in A-30.
    # This tests the node/adapter boundary, not owner-scoped storage or live extraction.
    config=LLMConfig(base_url='https://fixture.invalid/v1',model='fixture',api_key='fixture',max_output_tokens=4096,timeout_seconds=10)
    results=[]
    for requirements,response_model in [(req,model),(partial_req,partial)]:
        request=dict(schema_version='1.0',run_id=str(uuid4()),stage_id='behavior_model',attempt=1,input_artifact_ids=[str(uuid4())],contract_version='demo-v1',profile_id=profile.profile_id,limits={'max_calls':1,'max_output_tokens':4096,'timeout_seconds':10,'max_critical_questions':3})
        payload=dict(agent_input=request,profile=profile.data,artifacts=[{'metadata':{'kind':'requirements'},'content':canonical(requirements)}],execution_inputs={'model_configuration':canonical({k:v for k,v in vars(config).items() if k!='api_key'})})
        context=PreparedContext('Synthetic fixture',canonical(payload),canonical({'profile':profile.metadata}))
        def handler(_):
            return httpx.Response(200,json={'choices':[{'message':{'content':canonical(response_model)},'finish_reason':'stop'}],'usage':{'prompt_tokens':100,'completion_tokens':40,'total_tokens':140}})
        ledger=ExampleLedger()
        result=await BehaviorNode(config=config,ledger=ledger,transport=httpx.MockTransport(handler)).run(request,attempt_id=str(uuid4()),context=context,profile=profile,accepted_answers={})
        assert len(result['output_artifacts'])==1,result
        results.append(result['status'])
    assert results==['succeeded','failed']
    print(json.dumps({'node_results':results,'negative_cases':bad,'model_checksum':checksum(model),'partial_blocked':True,'bound_version':bound['version']}))


if __name__=='__main__':
    asyncio.run(main())
