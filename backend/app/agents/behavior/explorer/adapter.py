"""Persist report+receipt through the existing graph ArtifactStore port."""
from hashlib import sha256
from uuid import UUID, uuid5
from jsonschema import Draft202012Validator
from contracts.agents.validate import Contracts, loads
from ..model import ROOT, canonical, checksum, require
from .search import explore

SCHEMA=loads((ROOT/'contracts/agents/behavior/v1/behavior-report.schema.json').read_text())


def run_report(request, *, attempt_id, store, profile, initial_state):
    """Worker-authorized/fenced store only; single accepted model input, no arbitrary URI."""
    Contracts().validate('agent-input',request)
    require(len(request['input_artifact_ids'])==1 and request['profile_id']==profile.profile_id,'BEHAVIOR_REPORT_INPUT_INVALID')
    source=store.read(request['run_id'],request['input_artifact_ids'][0])
    Contracts().validate('artifact',source)
    require(source['artifact_id']==request['input_artifact_ids'][0] and source['kind']=='behavior_model' and 'content' in source,'BEHAVIOR_REPORT_MODEL_REQUIRED')
    model=loads(source['content'])
    require(model['contract_version']==request['contract_version'],'BEHAVIOR_CONTRACT_MISMATCH')
    bounds=profile.data['limits']['behavior']['search']
    path='contracts/behavior-report.json'
    require(profile.allows('architecture',path),'BEHAVIOR_PROFILE_GRANT_MISSING')
    previous=store.result(request['run_id'],attempt_id)
    if previous is not None:
        Contracts().validate('agent-result',previous)
        require(all(previous[k]==request[k] for k in ('run_id','stage_id','attempt','contract_version')) and len(previous['output_artifacts'])==1,'BEHAVIOR_RECEIPT_CONFLICT')
        a=previous['output_artifacts'][0]
        Contracts().validate('artifact',a)
        report=loads(a['content'])
        require(a['attempt_id']==attempt_id and a['path']==path and report['model_checksum']==checksum(model) and report['initial_state']==initial_state and report['bounds']==bounds,'BEHAVIOR_RECEIPT_CONFLICT')
        require(Draft202012Validator(SCHEMA).is_valid(report),'BEHAVIOR_REPORT_SCHEMA_INVALID')
        return previous
    report=explore(model,initial_state,**bounds)
    require(Draft202012Validator(SCHEMA).is_valid(report),'BEHAVIOR_REPORT_SCHEMA_INVALID')
    content=canonical(report)
    require(len(content.encode())<=profile.data['limits']['artifacts']['max_file_bytes'],'BEHAVIOR_REPORT_SIZE_LIMIT')
    artifact=dict(artifact_id=str(uuid5(UUID(attempt_id),path)),attempt_id=attempt_id,path=path,kind='behavior_report',media_type='application/json',version=1,schema_version='1.0',author_agent='architecture',checksum=sha256(content.encode()).hexdigest(),size_bytes=len(content.encode()),related_fr_ids=report['fr_ids'],content=content)
    ok=report['status']=='no_counterexample_within_bounds'
    result=dict(schema_version='1.0',**{k:request[k] for k in ('run_id','stage_id','attempt','contract_version')},status='succeeded' if ok else 'failed',output_artifacts=[artifact],questions=[],assumptions=[],usage=dict(calls=0,input_tokens=0,output_tokens=0,duration_seconds=0,model_calls=[]),diagnostics=[] if ok else [dict(code='BEHAVIOR_SEARCH_'+report['status'].upper(),message=report['reason'],severity='error',retryable=False,related_fr_ids=report['fr_ids'])])
    Contracts().validate('agent-result',result)
    store.accept(request['run_id'],attempt_id,result)
    return result
