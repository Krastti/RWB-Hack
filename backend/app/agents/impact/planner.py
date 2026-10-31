"""A-35 conservative versioned dependency graph from accepted generation inputs."""
from collections import deque
from hashlib import sha256
from uuid import UUID,uuid5
from contracts.agents.validate import Contracts,loads
from jsonschema import Draft202012Validator,FormatChecker
from ..behavior.model import ROOT,canonical,checksum,require,validate_model

SCHEMA_VERSION='1.0'
SCHEMAS={name:loads((ROOT/('contracts/agents/behavior/v1/'+name+'.schema.json')).read_text()) for name in ('dependency-graph','impact-plan')}

def validate(name,value):
    require(Draft202012Validator(SCHEMAS[name],format_checker=FormatChecker()).is_valid(value),'IMPACT_SCHEMA_INVALID')


def build_graph(*, project_id, snapshot_id, profile, plan, model, requirements, contexts, outputs, source_maps):
    """All inputs are owner-authorized immutable snapshots from storage/A-30/A-19.

    contexts: task_id -> exact PreparedContext.manifest (not reconstructed inputs).
    outputs: task_id -> accepted C-02 artifacts including verified content bytes.
    source_maps: task_id -> accepted rule/file map, or missing => explicit unknown.
    This port performs validation but cannot itself grant storage authorization.
    """
    UUID(project_id);UUID(snapshot_id)
    profile.validate_plan(plan)
    validate_model(model,requirements)
    require(model['contract_version']==plan['contract_version'],'IMPACT_CONTRACT_MISMATCH')
    tasks={t['task_id']:t for t in plan['tasks']}
    require(set(contexts)<=set(tasks) and set(outputs)<=set(tasks) and set(source_maps)<=set(tasks),'IMPACT_UNKNOWN_TASK')
    nodes={};edges=set();unknown=[]
    def node(id,kind,value,version='1',schema='1.0'):
        n=dict(id=id,kind=kind,checksum=checksum(value),version=str(version),schema_version=schema)
        if id in nodes and nodes[id]!=n: unknown.append('Conflicting immutable input: '+id)
        nodes[id]=n
        return id
    def link(a,b,reason): edges.add((a,b,reason))
    global_profile=node('profile','profile',profile.metadata,profile.profile_version)
    contract=node('contract','contract',plan['contract_version'],plan['contract_version'])
    for fr in requirements['functional_requirements']:
        related_ac=[a for a in requirements['acceptance_criteria'] if fr['id'] in a['related_fr_ids']]
        node('fr:'+fr['id'],'fr',{'fr':fr,'acceptance_criteria':related_ac})
    for e in model['entities']:
        binding={} if model['bindings'] is None else {'entity':model['bindings']['entities'].get(e['id']),'fields':{k:v for k,v in model['bindings']['fields'].items() if k.startswith(e['id']+'.')}}
        node('entity:'+e['id'],'entity',{'entity':e,'binding':binding})
    domains=node('behavior-domains','behavior',{'roles':model['roles'],'identities':model['identities']})
    rules={r['id']:r for r in model['rules']}
    for r in rules.values():
        rid=node('rule:'+r['id'],'rule',r)
        link('entity:'+r['entity'],rid,'rule reads/writes entity')
        for fr in r['fr_ids']: link('fr:'+fr,rid,'rule formalizes requirement')
    if model['bindings'] is None:
        unknown.append('Architecture bindings are missing')
    else:
        for a in model['actions']:
            b=model['bindings']['actions'].get(a['id'])
            if b is None: unknown.append('Missing action binding: '+a['id']);continue
            op=node('operation:'+b['operation_id'],'operation',{'action':a,'binding':b})
            screen=node('screen:'+b['screen_id']+':'+b['element_id'],'screen',b)
            link(op,screen,'screen element calls operation')
            for rid in a['guard_ids']+a['effect_ids']: link('rule:'+rid,op,'operation executes rule')
    for task_id,task in sorted(tasks.items()):
        tid=node('task:'+task_id,'task',task)
        link(global_profile,tid,'frozen profile/rules');link(contract,tid,'contract version');link(domains,tid,'declared roles and identities')
        for dependency in task['depends_on']: link('task:'+dependency,tid,'accepted plan dependency')
        manifest=contexts.get(task_id)
        if manifest is None:
            unknown.append('Missing registered A-30 input history: '+task_id)
        else:
            require(manifest['stage_id']==task['stage_id'] and manifest['owner_agent']==task['owner_agent'] and manifest['profile']==profile.metadata and manifest['contract_version']==plan['contract_version'],'IMPACT_CONTEXT_TASK_MISMATCH')
            require({a['artifact_id'] for a in manifest['references']}==set(task['input_artifact_ids']),'IMPACT_CONTEXT_INPUT_MISMATCH')
            for name in ('prompt','template','compiler_configuration','model_configuration'):
                value=manifest.get('execution_inputs',{}).get(name)
                if value is None: unknown.append('Missing execution input: '+task_id+':'+name);continue
                link(node('execution:'+task_id+':'+name,'execution',value),tid,'actual '+name)
            for a in manifest['references']:
                # References were authorized/checksummed by A-30; validate closed metadata locally.
                from jsonschema import Draft202012Validator,FormatChecker
                c=Contracts();common=c.schemas['common']
                validator=Draft202012Validator({'$id':common['$id'],'$defs':common['$defs'],**common['$defs']['artifact_reference']},registry=c.registry,format_checker=FormatChecker())
                require(validator.is_valid(a),'IMPACT_INVALID_REFERENCE')
                aid=node('artifact:'+a['path'],'artifact',{'checksum':a['checksum'],'kind':a['kind'],'schema_version':a['schema_version']},a['version'],a['schema_version'])
                link(aid,tid,'registered immutable input '+a['artifact_id'])
        accepted=outputs.get(task_id)
        if not accepted:
            unknown.append('Missing accepted output history: '+task_id)
            continue
        known={}
        for a in accepted:
            Contracts().validate('artifact',a)
            require(a['author_agent']==task['owner_agent'] and profile.allows(task['owner_agent'],a['path']),'IMPACT_OUTPUT_OWNER_MISMATCH')
            require(a['path'] in task['output_paths'] or any(a['path'].startswith(d+'/') for d in task['output_directories']),'IMPACT_OUTPUT_TASK_GRANT_MISMATCH')
            require(a['path'] not in known,'IMPACT_DUPLICATE_OUTPUT')
            known[a['path']]=a
            fid=node('file:'+a['path'],'file',{'checksum':a['checksum'],'kind':a['kind']},a['version'],a['schema_version'])
            link(tid,fid,'task produces file')
        source_map=source_maps.get(task_id)
        if task['owner_agent'] in {'frontend','backend'}:
            if source_map is None:
                unknown.append('Missing rule-to-file source map: '+task_id)
            else:
                require(source_map['contract_version']==plan['contract_version'] and source_map['owner_agent']==task['owner_agent'],'IMPACT_SOURCE_MAP_VERSION_MISMATCH')
                if source_map['model_checksum']!=checksum(model): unknown.append('Source map is from a prior model: '+task_id)
                link(node('compiler:'+task_id,'compiler',{'compiler_version':source_map['compiler_version']}),tid,'emitter version')
                for path,digest in source_map['files'].items():
                    require(path in known and known[path]['checksum']==digest,'IMPACT_SOURCE_MAP_OUTPUT_MISMATCH')
                expected_rules={r['id'] for r in rules.values() if set(r['fr_ids']) & set(task['related_fr_ids'])}
                if not expected_rules <= {r['rule_id'] for r in source_map['rules']}: unknown.append('Incomplete rule-to-file coverage: '+task_id)
                for entry in source_map['rules']:
                    if entry['rule_id'] not in rules or entry['path'] not in source_map['files']:
                        unknown.append('Dangling source map binding: '+entry['rule_id']);continue
                    link('rule:'+entry['rule_id'],'file:'+entry['path'],'compiler rule-to-symbol '+entry['symbol'])
                    # Regeneration is scheduled by task, not by the owner's name.
                    link('rule:'+entry['rule_id'],tid,'task owns mapped rule output')
    require(len(nodes)<=10000 and len(edges)<=100000,'IMPACT_GRAPH_LIMIT')
    result=dict(schema_version=SCHEMA_VERSION,project_id=project_id,snapshot_id=snapshot_id,contract_version=plan['contract_version'],profile=profile.metadata,
                nodes=sorted(nodes.values(),key=lambda n:n['id']),edges=[dict(source=a,target=b,reason=r) for a,b,r in sorted(edges)],tasks=[tasks[k] for k in sorted(tasks)],unknown=sorted(set(unknown)))
    validate('dependency-graph',result)
    return result


def compare(old, new, *, profile, accepted_plan):
    """Recompute scope only; never automatically reuse, regenerate or merge artifacts."""
    profile.validate_plan(accepted_plan)
    if new.get('schema_version')==SCHEMA_VERSION: validate('dependency-graph',new)
    if old is not None and old.get('schema_version')==SCHEMA_VERSION: validate('dependency-graph',old)
    require(new['profile']==profile.metadata and new['contract_version']==accepted_plan['contract_version'],'IMPACT_PROFILE_MISMATCH')
    tasks={t['task_id']:t for t in accepted_plan['tasks']}
    require(new['tasks']==[tasks[k] for k in sorted(tasks)],'IMPACT_PLAN_MISMATCH')
    if old is not None: require(old['project_id']==new['project_id'],'IMPACT_CROSS_PROJECT')
    unknown=list(new.get('unknown',[]))
    old_compatible=old is not None and old.get('schema_version')==SCHEMA_VERSION
    if not old_compatible: unknown.append('Missing history or incompatible graph schema')
    if new.get('schema_version')!=SCHEMA_VERSION: unknown.append('Incompatible new graph schema')
    old_nodes={n['id']:n for n in old['nodes']} if old_compatible else {}
    new_nodes={n['id']:n for n in new['nodes']}
    require(len(new_nodes)==len(new['nodes']) and (not old_compatible or len(old_nodes)==len(old['nodes'])),'IMPACT_DUPLICATE_NODE')
    if old_compatible: unknown.extend(old.get('unknown',[]))
    changed=sorted(k for k in set(old_nodes)|set(new_nodes) if old_nodes.get(k)!=new_nodes.get(k))
    adjacency={}
    for graph in ([old,new] if old_compatible else [new]):
        ids={n['id'] for n in graph['nodes']}
        required={'profile','contract','behavior-domains'} | {'task:'+t['task_id'] for t in graph['tasks']}
        if not required<=ids: unknown.append('Missing required graph nodes')
        for t in graph['tasks']:
            for name in ('prompt','template','compiler_configuration','model_configuration'):
                if 'execution:'+t['task_id']+':'+name not in ids: unknown.append('Incomplete execution history: '+t['task_id'])
    for graph in ([old,new] if old_compatible else [new]):
        ids={n['id'] for n in graph['nodes']}
        for edge in graph['edges']:
            if edge['source'] not in ids or edge['target'] not in ids: unknown.append('Dangling binding: '+edge['source']+' -> '+edge['target'])
            adjacency.setdefault(edge['source'],set()).add((edge['target'],edge['reason']))
    # Removed entities/rules still propagate through old edges; changed edges seed targets.
    old_edges={(e['source'],e['target'],e['reason']) for e in old['edges']} if old_compatible else set()
    new_edges={(e['source'],e['target'],e['reason']) for e in new['edges']}
    edge_changed={b for a,b,r in old_edges^new_edges}
    seeds=sorted(set(changed)|edge_changed)
    paths={k:[{'node':k,'reason':'changed input' if k in changed else 'changed dependency'}] for k in seeds}
    queue=deque(seeds)
    while queue:
        source=queue.popleft()
        for target,reason in sorted(adjacency.get(source,set())):
            if target not in paths:
                paths[target]=paths[source]+[{'node':target,'reason':reason}];queue.append(target)
    affected=[]
    for task_id,t in sorted(tasks.items()):
        chain=paths.get('task:'+task_id)
        # Changes to accepted output bytes can also require the producing task to rerun.
        if chain is None:
            for key in sorted(paths):
                if key.startswith('file:') and (key[5:] in t['output_paths'] or any(key[5:].startswith(d+'/') for d in t['output_directories'])):
                    chain=paths[key]+[{'node':'task:'+task_id,'reason':'owns changed/dependent output'}];break
        if chain is not None or unknown:
            affected.append({'task_id':task_id,'stage_id':t['stage_id'],'owner_agent':t['owner_agent'],
                             'reason_chain':chain or [{'node':'task:'+task_id,'reason':'conservative expansion: unknown dependencies'}],
                             'input_artifact_ids':t['input_artifact_ids']})
    unaffected=[n['id'][5:] for n in new['nodes'] if n['kind']=='file' and n['id'] not in paths and not unknown and not any(e['task_id'] in tasks and (n['id'][5:] in tasks[e['task_id']]['output_paths'] or any(n['id'][5:].startswith(d+'/') for d in tasks[e['task_id']]['output_directories'])) for e in affected)]
    result=dict(schema_version='1.0',project_id=new['project_id'],old_snapshot=None if old is None else {'snapshot_id':old['snapshot_id'],'checksum':checksum(old),'schema_version':old['schema_version']},
                new_snapshot={'snapshot_id':new['snapshot_id'],'checksum':checksum(new),'schema_version':new['schema_version']},contract_version=new['contract_version'],profile=profile.metadata,
                changed_nodes=changed,affected_tasks=affected,unknown=sorted(set(unknown)),unchanged_recorded_inputs=sorted(unaffected),automatic_reuse_allowed=False)
    validate('impact-plan',result)
    return result


def save_plan(impact,*,request,attempt_id,profile,store):
    validate('impact-plan',impact)
    return _save(impact,request=request,attempt_id=attempt_id,profile=profile,store=store,name='impact-plan',kind='impact_plan')


def save_graph(graph,*,request,attempt_id,profile,store):
    validate('dependency-graph',graph)
    return _save(graph,request=request,attempt_id=attempt_id,profile=profile,store=store,name='dependency-graph',kind='dependency_graph')


def _save(impact,*,request,attempt_id,profile,store,name,kind):
    """Worker-fenced ArtifactStore acceptance; no scheduler side effects."""
    Contracts().validate('agent-input',request)
    require(impact['profile']==profile.metadata and impact['contract_version']==request['contract_version'],'IMPACT_PROFILE_MISMATCH')
    path='contracts/'+name+'.json';require(profile.allows('architecture',path),'IMPACT_GRANT_MISSING')
    content=canonical(impact)
    require(len(content.encode())<=profile.data['limits']['artifacts']['max_file_bytes'],'IMPACT_SIZE_LIMIT')
    artifact=dict(artifact_id=str(uuid5(UUID(attempt_id),path)),attempt_id=attempt_id,path=path,kind=kind,media_type='application/json',version=1,schema_version='1.0',author_agent='architecture',checksum=sha256(content.encode()).hexdigest(),size_bytes=len(content.encode()),related_fr_ids=[],content=content)
    result=dict(schema_version='1.0',**{k:request[k] for k in ('run_id','stage_id','attempt','contract_version')},status='succeeded',output_artifacts=[artifact],questions=[],assumptions=[],usage=dict(calls=0,input_tokens=0,output_tokens=0,duration_seconds=0,model_calls=[]),diagnostics=[])
    Contracts().validate('agent-result',result)
    previous=store.result(request['run_id'],attempt_id)
    require(previous is None or previous==result,'IMPACT_RECEIPT_CONFLICT')
    if previous is None: store.accept(request['run_id'],attempt_id,result)
    return result
