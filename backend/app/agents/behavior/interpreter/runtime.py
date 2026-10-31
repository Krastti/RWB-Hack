"""Pure runtime over an already validated model. Also emitted verbatim by A-33.

No eval, imports of generated code, networking, I/O, mutable globals or coercion.
"""
MISSING = object()
UNKNOWN = object()
INTERPRETER_VERSION = '1.0.0'


def valid(value, t, identities):
    if value is None:
        return t['nullable']
    if t['kind'] == 'boolean':
        return type(value) is bool
    if t['kind'] == 'integer':
        return type(value) is int and t['minimum'] <= value <= t['maximum']
    return type(value) is str and value in (identities if t['kind']=='identity' else t['values'])


def valid_state(model, state):
    if not isinstance(state, dict) or not isinstance(state.get('values'), dict):
        return False
    entity = next((item for item in model['entities'] if item['id'] == state.get('entity')), None)
    if entity is None:
        return False
    fields = {item['id']: item['type'] for item in entity['fields']}
    return all(name in fields and valid(value, fields[name], model['identities'])
               for name, value in state['values'].items())


def evaluate(expr, env):
    op = expr.get('op')
    if op == 'literal':
        return expr['value']
    if op == 'ref':
        return env.get(expr['scope'], {}).get(expr['name'], MISSING)
    args = [evaluate(x,env) for x in expr.get('args',[])]
    if op == 'present':
        return args[0] is not MISSING and args[0] is not UNKNOWN
    if op == 'is_null':
        return UNKNOWN if args[0] is MISSING or args[0] is UNKNOWN else args[0] is None
    if op == 'and':
        if any(x is False for x in args): return False
        return True if all(x is True for x in args) else UNKNOWN
    if op == 'or':
        if any(x is True for x in args): return True
        return False if all(x is False for x in args) else UNKNOWN
    if op == 'not':
        return not args[0] if type(args[0]) is bool else UNKNOWN
    if any(x is MISSING or x is UNKNOWN or x is None for x in args):
        return UNKNOWN
    if len(args)!=2:
        return UNKNOWN
    a,b=args
    if type(a) is not type(b):
        return UNKNOWN
    if op=='eq': return a==b
    if op=='ne': return a!=b
    if op not in {'lt','le','gt','ge'} or type(a) is not int:
        return UNKNOWN
    if op=='lt': return a<b
    if op=='le': return a<=b
    if op=='gt': return a>b
    return a>=b


def _result(status,state,rule_ids=(),fr_ids=(),reason=''):
    return {'status':status,'state':{'entity':state.get('entity'),'values':dict(state.get('values',{}))},
            'rule_ids':sorted(set(rule_ids)),'fr_ids':sorted(set(fr_ids)),'reason':reason}


def step_validated(model,state,actor,action):
    """One record, simultaneous effects from pre-state. Input dictionaries unchanged."""
    entities={x['id']:x for x in model['entities']}
    actions={x['id']:x for x in model['actions']}
    rules={x['id']:x for x in model['rules']}
    entity=state.get('entity')
    a=actions.get(action.get('id'))
    if entity not in entities or a is None or a['entity']!=entity:
        return _result('unknown',state,reason='UNKNOWN_ACTION_OR_ENTITY')
    fields={f['id']:f['type'] for f in entities[entity]['fields']}
    params={f['id']:f['type'] for f in a['parameters']}
    values=state.get('values',{})
    arguments=action.get('parameters',{})
    if not isinstance(values,dict) or not isinstance(arguments,dict) or not isinstance(actor,dict):
        return _result('unknown',{'entity':entity,'values':{}},reason='INVALID_INPUT')
    if not valid_state(model, state):
        return _result('unknown',state,reason='INVALID_STATE_VALUE')
    if any(k not in params or not valid(v,params[k],model['identities']) for k,v in arguments.items()):
        return _result('unknown',state,reason='INVALID_PARAMETER_VALUE')
    if any(k not in {'id','role'} for k in actor) or ('id' in actor and actor['id'] not in model['identities']) or ('role' in actor and actor['role'] not in model['roles']):
        return _result('unknown',state,reason='INVALID_ACTOR')
    env={'state':values,'previous':values,'actor':actor,'param':arguments}
    applied=[]
    unknown=[]
    denied=[]
    for rid in a['guard_ids']:
        r=rules[rid]
        v=evaluate(r['expression'],env) if r['status']=='supported' else UNKNOWN
        if v is False: denied.append(r)
        elif v is not True: unknown.append(r)
        else: applied.append(r)
    if denied:
        return _result('denied',state,[r['id'] for r in denied],[fr for r in denied for fr in r['fr_ids']],'GUARD_DENIED')
    if unknown:
        return _result('unknown',state,[r['id'] for r in unknown],[fr for r in unknown for fr in r['fr_ids']],'RULE_UNRESOLVED')
    updated=dict(values)
    for rid in a['effect_ids']:
        r=rules[rid]
        v=evaluate(r['expression'],env) if r['status']=='supported' else UNKNOWN
        if v is MISSING or v is UNKNOWN or not valid(v,fields[r['field']],model['identities']):
            return _result('unknown',state,[rid],r['fr_ids'],'EFFECT_UNRESOLVED')
        updated[r['field']]=v
        applied.append(r)
    return _result('allowed',{'entity':entity,'values':updated},[r['id'] for r in applied],[fr for r in applied for fr in r['fr_ids']],'ALLOWED')


def invariants(model,previous,state,actor,action):
    """Independent assertions; never derived from guards or used to mask bad transitions."""
    env={'state':state['values'],'previous':previous['values'],'actor':actor,'param':action.get('parameters',{})}
    results=[]
    for r in model['rules']:
        if r['kind']=='invariant' and r['entity']==state['entity']:
            value=evaluate(r['expression'],env) if r['status']=='supported' else UNKNOWN
            results.append({'rule_id':r['id'],'fr_ids':r['fr_ids'],'status':'holds' if value is True else 'violated' if value is False else 'unknown'})
    return results
