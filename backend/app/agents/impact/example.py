"""Explicit accepted-history fixtures; no automatic regeneration or storage fallback."""
from copy import deepcopy
from hashlib import sha256
import json
from uuid import UUID,uuid5
from ..behavior.fixtures import bound_example
from ..behavior.model import ROOT,checksum,canonical
from ..behavior.profile import behavior_profile
from .planner import build_graph,compare,validate

NS=UUID('41c82cf9-28dc-56c6-b8f1-60336c85a7be')

def a(path,owner,content,kind='source'):
    return dict(artifact_id=str(uuid5(NS,path)),attempt_id=str(NS),path=path,kind=kind,media_type='text/plain',version=1,schema_version='1.0',author_agent=owner,checksum=sha256(content.encode()).hexdigest(),size_bytes=len(content.encode()),related_fr_ids=['FR-001'],content=content)


def fixtures():
    req,model=bound_example();profile=behavior_profile()
    plan=json.loads((ROOT/'templates/profiles/web-fastapi-angular/examples/generation-plan.json').read_text())
    plan['contract_version']=model['contract_version'];plan['tasks']=plan['tasks'][:2]
    theme=a('frontend/src/styles.scss','frontend','body { color: black; }','configuration')
    requirements=a('requirements.json','analysis',canonical(req),'requirements')
    contexts={};outputs={};maps={}
    for task in plan['tasks']:
        owner=task['owner_agent'];refs=[requirements]+([theme] if owner=='frontend' else [])
        task['input_artifact_ids']=[x['artifact_id'] for x in refs]
        contexts[task['task_id']]=dict(stage_id=task['stage_id'],owner_agent=owner,profile=profile.metadata,contract_version=model['contract_version'],references=[{k:v for k,v in x.items() if k!='content'} for x in refs],execution_inputs={k:{'checksum':sha256(k.encode()).hexdigest(),'size_bytes':len(k)} for k in ('prompt','template','compiler_configuration','model_configuration')})
        path='frontend/src/app/domain/policy.ts' if owner=='frontend' else 'backend/app/services/domain_policy.py'
        out=a(path,owner,'synthetic accepted output '+owner)
        outputs[task['task_id']]=[out]
        maps[task['task_id']]=dict(model_checksum=checksum(model),contract_version=model['contract_version'],owner_agent=owner,compiler_version='fixture-1',files={path:out['checksum']},rules=[{'rule_id':r['id'],'path':path,'symbol':'decide'} for r in model['rules']])
    return dict(project_id=str(NS),snapshot_id=str(uuid5(NS,'old')),profile=profile,plan=plan,model=model,requirements=req,contexts=contexts,outputs=outputs,source_maps=maps)


def examples():
    args=fixtures();old=build_graph(**args)
    assert not old['unknown'],old['unknown']
    results={}
    # Compare revisions of an immutable graph; IDs stable, digests change.
    changed=deepcopy(old);changed['snapshot_id']=str(uuid5(NS,'owner-change'))
    rule=next(n for n in changed['nodes'] if n['id']=='rule:'+args['model']['rules'][0]['id'])
    rule['checksum']=sha256(b'only administrator may cancel').hexdigest()
    results['ownership']=compare(old,changed,profile=args['profile'],accepted_plan=args['plan'])
    assert {t['owner_agent'] for t in results['ownership']['affected_tasks']}=={'frontend','backend'}
    theme=deepcopy(old);theme['snapshot_id']=str(uuid5(NS,'theme'))
    next(n for n in theme['nodes'] if n['id']=='artifact:frontend/src/styles.scss')['checksum']=sha256(b'new theme').hexdigest()
    results['theme']=compare(old,theme,profile=args['profile'],accepted_plan=args['plan'])
    assert [t['owner_agent'] for t in results['theme']['affected_tasks']]==['frontend']
    assert results['theme']['unchanged_recorded_inputs']==['backend/app/services/domain_policy.py']
    removed=deepcopy(old);removed['snapshot_id']=str(uuid5(NS,'removed-entity'));removed['nodes']=[n for n in removed['nodes'] if n['id']!='entity:ticket']
    results['removed_entity']=compare(old,removed,profile=args['profile'],accepted_plan=args['plan'])
    assert any('Dangling' in x for x in results['removed_entity']['unknown'])
    results['missing_history']=compare(None,theme,profile=args['profile'],accepted_plan=args['plan'])
    assert len(results['missing_history']['affected_tasks'])==2
    bad=deepcopy(old);bad['schema_version']='future'
    results['unknown_schema']=compare(bad,theme,profile=args['profile'],accepted_plan=args['plan'])
    assert len(results['unknown_schema']['affected_tasks'])==2
    assert compare(old,old,profile=args['profile'],accepted_plan=args['plan'])['affected_tasks']==[]
    missing=fixtures();missing['contexts'].pop('backend');assert build_graph(**missing)['unknown']
    for result in results.values(): validate('impact-plan',result)
    return old,results


if __name__=='__main__':
    graph,results=examples()
    print(json.dumps({'graph':graph,'results':results},ensure_ascii=False,indent=2))
