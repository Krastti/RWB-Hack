"""Typed, finite, single-record behavior DSL. No execution of business source text."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from uuid import UUID, uuid5
from jsonschema import Draft202012Validator, FormatChecker
from contracts.agents.validate import Contracts

ROOT = Path(__file__).resolve().parents[4]
SCHEMA = json.loads((ROOT / 'contracts/agents/behavior/v1/behavior-model.schema.json').read_text())
NAMESPACE = UUID('cd402dc4-963e-5dda-8cfe-23276b14a0fd')


class ModelError(ValueError):
    pass


def require(ok, code):
    if not ok:
        raise ModelError(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def checksum(value):
    return sha256(canonical(value).encode()).hexdigest()


def rule_id(decision_id):
    return str(uuid5(NAMESPACE, decision_id))


def index(items, key='id'):
    result = {x[key]: x for x in items}
    require(len(result) == len(items), 'BEHAVIOR_DUPLICATE_ID')
    return result


def valid_value(value, kind, identities):
    if value is None:
        return kind['nullable']
    if kind['kind'] == 'boolean':
        return type(value) is bool
    if kind['kind'] == 'integer':
        return type(value) is int and kind['minimum'] <= value <= kind['maximum']
    return type(value) is str and value in (identities if kind['kind'] == 'identity' else kind['values'])


def value_type(kind, values=(), minimum=None, maximum=None, nullable=False):
    return dict(kind=kind, values=list(values), minimum=minimum, maximum=maximum, nullable=nullable)


def expression_type(expr, fields, parameters, model, *, previous=False, depth=0):
    require(depth < 24, 'BEHAVIOR_EXPRESSION_DEPTH')
    op = expr['op']
    if op == 'literal':
        v = expr['value']
        if v is None:
            return value_type('null', nullable=True)
        if type(v) is bool:
            return value_type('boolean')
        if type(v) is int:
            require(abs(v) <= 9007199254740991, 'BEHAVIOR_UNSAFE_INTEGER')
            return value_type('integer', minimum=v, maximum=v)
        return value_type('enum', [v])
    if op == 'ref':
        scope, name = expr['scope'], expr['name']
        if scope == 'actor':
            require(name in {'id', 'role'}, 'BEHAVIOR_UNKNOWN_REFERENCE')
            return value_type('identity') if name == 'id' else value_type('enum', model['roles'])
        require(scope != 'previous' or previous, 'BEHAVIOR_PREVIOUS_OUTSIDE_INVARIANT')
        source = parameters if scope == 'param' else fields
        require(name in source, 'BEHAVIOR_UNKNOWN_REFERENCE')
        return source[name]['type']
    args = [expression_type(x, fields, parameters, model, previous=previous, depth=depth+1) for x in expr['args']]
    require(len(args) == (1 if op in {'not','present','is_null'} else 2) or op in {'and','or'}, 'BEHAVIOR_ARITY')
    if op in {'and','or','not'}:
        require(all(x['kind'] == 'boolean' for x in args), 'BEHAVIOR_TYPE_ERROR')
    elif op in {'lt','le','gt','ge'}:
        require(all(x['kind'] == 'integer' for x in args), 'BEHAVIOR_TYPE_ERROR')
    elif op in {'eq','ne'}:
        a,b = args
        require(a['kind'] == b['kind'] or {a['kind'],b['kind']} <= {'enum','identity'} or 'null' in {a['kind'],b['kind']}, 'BEHAVIOR_TYPE_ERROR')
    elif op not in {'present','is_null'}:
        raise ModelError('BEHAVIOR_UNSUPPORTED_OPERATOR')
    return value_type('boolean')


def assignable(source, target, identities):
    if source['kind'] == 'null':
        return target['nullable']
    if source['nullable'] and not target['nullable']:
        return False
    if source['kind'] in {'enum','identity'} and target['kind'] in {'enum','identity'}:
        a = identities if source['kind'] == 'identity' else source['values']
        b = identities if target['kind'] == 'identity' else target['values']
        return set(a) <= set(b)
    if source['kind'] != target['kind']:
        return False
    return source['kind'] != 'integer' or target['minimum'] <= source['minimum'] <= source['maximum'] <= target['maximum']


def validate_model(model, requirements=None, answers=None, *, max_bytes=262144):
    require(len(canonical(model).encode()) <= max_bytes, 'BEHAVIOR_SIZE_LIMIT')
    require(Draft202012Validator(SCHEMA, format_checker=FormatChecker()).is_valid(model), 'BEHAVIOR_SCHEMA_INVALID')
    entities, actions, rules = index(model['entities']), index(model['actions']), index(model['rules'])
    require(len(rules) <= 128, 'BEHAVIOR_RULE_LIMIT')
    require(len(set(model['roles'])) == len(model['roles']) and len(set(model['identities'])) == len(model['identities']), 'BEHAVIOR_DUPLICATE_DOMAIN')
    require(len(model['identities']) <= 64 and len(model['roles']) <= 64, 'BEHAVIOR_DOMAIN_LIMIT')
    for owner in list(entities.values()) + list(actions.values()):
        for f in index(owner.get('fields', owner.get('parameters', []))).values():
            t = f['type']
            require(len(t['values']) == len(set(t['values'])) <= 64, 'BEHAVIOR_INVALID_DOMAIN')
            if t['kind'] == 'integer':
                require(type(t['minimum']) is int and type(t['maximum']) is int and t['minimum'] <= t['maximum'] and not t['values'], 'BEHAVIOR_INVALID_RANGE')
            else:
                require(t['minimum'] is None and t['maximum'] is None and (bool(t['values']) if t['kind'] == 'enum' else not t['values']), 'BEHAVIOR_INVALID_DOMAIN')
    used = set()
    for a in actions.values():
        require(a['entity'] in entities, 'BEHAVIOR_UNKNOWN_ENTITY')
        for kind,key in [('guard','guard_ids'),('effect','effect_ids')]:
            require(len(a[key]) == len(set(a[key])), 'BEHAVIOR_DUPLICATE_RULE')
            for rid in a[key]:
                require(rid in rules and rid not in used and rules[rid]['kind'] == kind and rules[rid]['action'] == a['id'] and rules[rid]['entity'] == a['entity'], 'BEHAVIOR_ACTION_RULE_MISMATCH')
                used.add(rid)
        effect_fields = [rules[r]['field'] for r in a['effect_ids']]
        require(len(set(effect_fields)) == len(effect_fields), 'BEHAVIOR_CONFLICTING_EFFECTS')
    for r in rules.values():
        require(r['id'] == rule_id(r['decision_id']), 'BEHAVIOR_UNSTABLE_RULE_ID')
        require(r['entity'] in entities, 'BEHAVIOR_UNKNOWN_ENTITY')
        invariant = r['kind'] == 'invariant'
        require((r['action'] is None and r['id'] not in used) if invariant else r['id'] in used, 'BEHAVIOR_ORPHAN_RULE')
        require(r['field'] is None if r['kind'] != 'effect' else r['field'] in index(entities[r['entity']]['fields']), 'BEHAVIOR_UNKNOWN_FIELD')
        require((r['expression'] is not None) == (r['status'] == 'supported'), 'BEHAVIOR_STATUS_EXPRESSION_MISMATCH')
        if r['status'] == 'supported':
            require(not any(e['source'] == 'assumption' for e in r['evidence']) or not r['critical'], 'BEHAVIOR_UNCONFIRMED_CRITICAL_RULE')
            fields = index(entities[r['entity']]['fields'])
            parameters = {} if invariant else index(actions[r['action']]['parameters'])
            t = expression_type(r['expression'], fields, parameters, model, previous=invariant)
            require(assignable(t, fields[r['field']]['type'], model['identities']) if r['kind'] == 'effect' else t['kind'] == 'boolean', 'BEHAVIOR_EFFECT_TYPE_ERROR')
    coverage = index(model['coverage'], 'fr_id')
    if requirements is not None:
        Contracts().validate('requirements', requirements)
        require(model['requirements_checksum'] == checksum(requirements), 'BEHAVIOR_REQUIREMENTS_MISMATCH')
        fr = index(requirements['functional_requirements'])
        ac = index(requirements['acceptance_criteria'])
        require(set(coverage) == set(fr), 'BEHAVIOR_INCOMPLETE_COVERAGE')
        require(set(model['roles']) <= {r['id'] for r in requirements['roles']}, 'BEHAVIOR_UNKNOWN_ROLE')
        answers = answers or {}
        assumptions = index(requirements['assumptions'])
        for r in rules.values():
            require(set(r['fr_ids']) <= set(fr) and set(r['ac_ids']) <= set(ac), 'BEHAVIOR_UNKNOWN_TRACE_REFERENCE')
            require(all(set(ac[i]['related_fr_ids']) & set(r['fr_ids']) for i in r['ac_ids']), 'BEHAVIOR_AC_FR_MISMATCH')
            for e in r['evidence']:
                if e['source'] == 'requirement':
                    require(e['source_id'] in set(r['fr_ids']) | set(r['ac_ids']), 'BEHAVIOR_EVIDENCE_MISMATCH')
                    source = (fr | ac)[e['source_id']]
                    require(e['quote'] in canonical(source), 'BEHAVIOR_EVIDENCE_MISMATCH')
                elif e['source'] == 'answer':
                    require(e['source_id'] in answers and answers[e['source_id']]['accepted'] is True and e['quote'] in answers[e['source_id']]['answer'], 'BEHAVIOR_UNCONFIRMED_ANSWER')
                else:
                    require(e['source_id'] in assumptions and e['quote'] in assumptions[e['source_id']]['text'], 'BEHAVIOR_EVIDENCE_MISMATCH')
    for fid,c in coverage.items():
        relevant = [r for r in rules.values() if fid in r['fr_ids']]
        if c['status'] == 'full':
            require(relevant and all(r['status'] == 'supported' for r in relevant), 'BEHAVIOR_FALSE_FULL_COVERAGE')
            if requirements is not None:
                expected = {a['id'] for a in requirements['acceptance_criteria'] if fid in a['related_fr_ids']}
                require(expected <= {a for r in relevant for a in r['ac_ids']}, 'BEHAVIOR_AC_COVERAGE_MISSING')
        if c['status'] == 'not_applicable':
            require(not relevant, 'BEHAVIOR_FALSE_NOT_APPLICABLE')
    return deepcopy(model)


def blockers(model):
    return [r['id'] for r in model['rules'] if r['critical'] and r['status'] != 'supported']


def bind(model, bindings, catalog):
    """catalog is an A-09 accepted, owner-authorized snapshot; never raw LLM names.

    {contract_version, entities: {entity: [field]}, operations: {op: entity},
     screens: {screen: {element: operation_id}}}. A-09 adapter is external.
    """
    validate_model(model)
    require(bindings['contract_version'] == catalog['contract_version'] == model['contract_version'], 'BEHAVIOR_CONTRACT_MISMATCH')
    entities, actions = index(model['entities']), index(model['actions'])
    require(set(bindings['entities']) == set(entities) and set(bindings['actions']) == set(actions), 'BEHAVIOR_BINDING_MISSING')
    require(set(bindings['fields']) == {f"{e['id']}.{f['id']}" for e in entities.values() for f in e['fields']}, 'BEHAVIOR_BINDING_MISSING')
    require(len(set(bindings['entities'].values())) == len(entities), 'BEHAVIOR_BINDING_COLLISION')
    for e in entities.values():
        physical = bindings['entities'][e['id']]
        require(physical in catalog['entities'], 'BEHAVIOR_BINDING_UNKNOWN')
        mapped = [bindings['fields'][f"{e['id']}.{f['id']}"] for f in e['fields']]
        require(len(mapped) == len(set(mapped)) and set(mapped) <= set(catalog['entities'][physical]), 'BEHAVIOR_BINDING_UNKNOWN')
    used = set()
    for a in actions.values():
        b = bindings['actions'][a['id']]
        require(b['operation_id'] not in used and catalog['operations'].get(b['operation_id']) == bindings['entities'][a['entity']], 'BEHAVIOR_OPERATION_BINDING_INVALID')
        used.add(b['operation_id'])
        require(catalog['screens'].get(b['screen_id'], {}).get(b['element_id']) == b['operation_id'], 'BEHAVIOR_SCREEN_BINDING_INVALID')
        require(set(b['public_fields']) <= set(index(entities[a['entity']]['fields'])), 'BEHAVIOR_PUBLIC_FIELD_UNKNOWN')
    result = deepcopy(model)
    result.update(bindings=deepcopy(bindings), version=model['version']+1, parent_checksum=checksum(model))
    return validate_model(result)
