"""Deterministic bounded BFS. Domains explicitly state full versus sampled."""
from collections import deque
from itertools import product
from time import monotonic
from ..model import ModelError, canonical, checksum, validate_model, require
from ..interpreter.runtime import INTERPRETER_VERSION, invariants, step_validated


def domain(t,identities,cap):
    if t['kind']=='integer':
        count=t['maximum']-t['minimum']+1
        values=list(range(t['minimum'],t['maximum']+1)) if count<=cap else sorted({t['minimum'],t['maximum'],max(t['minimum'],min(0,t['maximum']))})
        complete=count<=cap
    else:
        values=([False,True] if t['kind']=='boolean' else identities if t['kind']=='identity' else t['values'])
        complete=len(values)<=cap
        values=list(values[:cap])
    if t['nullable']: values=[None]+values
    return values,complete


def explore(model,initial,*,max_depth=5,max_states=1000,timeout_seconds=2.0,max_domain_values=64,max_scenarios=10000):
    require(type(max_depth) is int and 0<=max_depth<=100 and type(max_states) is int and 1<=max_states<=100000 and 0<timeout_seconds<=60 and type(max_domain_values) is int and 3<=max_domain_values<=64 and type(max_scenarios) is int and 1<=max_scenarios<=100000, 'BEHAVIOR_INVALID_SEARCH_BOUNDS')
    start=monotonic()
    report=dict(schema_version='1.0',model_checksum=checksum(model),interpreter_version=INTERPRETER_VERSION,
                initial_state=initial,initial_actor=None,bounds=dict(max_depth=max_depth,max_states=max_states,timeout_seconds=timeout_seconds,max_domain_values=max_domain_values,max_scenarios=max_scenarios),
                status='inconclusive',reason='',domain_complete=True,states_visited=0,transitions_checked=0,trace=[],invariant_id=None,fr_ids=[],unknown_rules=[],depth_frontier=False)
    def finish(status,reason):
        report.update(status=status,reason=reason)
        return report
    try:
        validate_model(model)
    except (ModelError,RecursionError,TypeError,ValueError):
        return finish('unsupported','INVALID_OR_UNSUPPORTED_MODEL')
    if not isinstance(initial,dict) or initial.get('entity') not in {e['id'] for e in model['entities']} or not isinstance(initial.get('values'),dict):
        return finish('unsupported','INVALID_INITIAL_STATE')
    # Enumeration and exploration share one deadline and finite scenario budget.
    scenarios=[]
    complete=True
    for action in sorted(model['actions'],key=lambda a:a['id']):
        if action['entity']!=initial['entity']: continue
        params=sorted(action['parameters'],key=lambda p:p['id'])
        domains=[]
        for p in params:
            values,full=domain(p['type'],model['identities'],max_domain_values)
            domains.append(values);complete=complete and full
        for identity,role,values in product(sorted(model['identities']),sorted(model['roles']),product(*domains)):
            if monotonic()-start>=timeout_seconds: return finish('inconclusive','TIME_LIMIT')
            if len(scenarios)>=max_scenarios:
                report['domain_complete']=False
                return finish('inconclusive','SCENARIO_LIMIT')
            scenarios.append(({'id':identity,'role':role},{'id':action['id'],'parameters':dict(zip([p['id'] for p in params],values))}))
    report['domain_complete']=complete
    unknown=set(r['id'] for r in model['rules'] if r['status']!='supported')
    if not scenarios:
        return finish('unsupported','NO_ACTION_SCENARIOS')
    for actor, action in scenarios:
        for inv in invariants(model,initial,initial,actor,action):
            if inv['status']=='unknown': unknown.add(inv['rule_id'])
            if inv['status']=='violated':
                report.update(initial_actor=actor,invariant_id=inv['rule_id'],fr_ids=inv['fr_ids'])
                return finish('counterexample','INITIAL_INVARIANT_VIOLATED')
    queue=deque([(initial,[])])
    seen={canonical(initial)}
    report['states_visited']=1
    while queue:
        if monotonic()-start>=timeout_seconds: return finish('inconclusive','TIME_LIMIT')
        state,trace=queue.popleft()
        if len(trace)>=max_depth:
            report['depth_frontier']=True
            continue
        for actor,action in scenarios:
            if monotonic()-start>=timeout_seconds: return finish('inconclusive','TIME_LIMIT')
            result=step_validated(model,state,actor,action)
            report['transitions_checked']+=1
            if result['status']=='unknown':
                unknown.update(result['rule_ids'] or ['input'])
                continue
            if result['status']!='allowed': continue
            edge={'actor':actor,'action':action,'result':result}
            next_trace=trace+[edge]
            for inv in invariants(model,state,result['state'],actor,action):
                if inv['status']=='unknown': unknown.add(inv['rule_id'])
                if inv['status']=='violated':
                    report.update(trace=next_trace,invariant_id=inv['rule_id'],fr_ids=inv['fr_ids'])
                    return finish('counterexample','INVARIANT_VIOLATED')
            key=canonical(result['state'])
            if key not in seen:
                if len(seen)>=max_states: return finish('inconclusive','STATE_LIMIT')
                seen.add(key);queue.append((result['state'],next_trace))
                report['states_visited']=len(seen)
    report['unknown_rules']=sorted(unknown)
    if unknown or not complete:
        return finish('inconclusive','UNKNOWN_OR_SAMPLED_DOMAIN')
    # Depth is a declared bound, not an unbounded proof; surface the frontier explicitly.
    return finish('no_counterexample_within_bounds','DEPTH_BOUND' if report['depth_frontier'] else 'REACHABLE_DOMAIN_EXHAUSTED')


def replay(model,report):
    validate_model(model)
    require(report['model_checksum']==checksum(model) and report['interpreter_version']==INTERPRETER_VERSION, 'BEHAVIOR_REPLAY_VERSION_MISMATCH')
    state=report['initial_state']
    for edge in report['trace']:
        result=step_validated(model,state,edge['actor'],edge['action'])
        require(result==edge['result'] and result['status']=='allowed','BEHAVIOR_REPLAY_MISMATCH')
        previous,state=state,result['state']
    if report['trace']:
        checks=invariants(model,previous,state,edge['actor'],edge['action'])
    else:
        require(report.get('initial_actor') is not None,'BEHAVIOR_REPLAY_EMPTY')
        checks=invariants(model,state,state,report['initial_actor'],{'parameters':{}})
    require(any(x['rule_id']==report['invariant_id'] and x['status']=='violated' for x in checks),'BEHAVIOR_REPLAY_NO_VIOLATION')
    return state
