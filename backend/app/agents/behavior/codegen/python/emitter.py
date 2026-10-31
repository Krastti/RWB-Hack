"""A-33 deterministic standalone Python policy and locked single-record service."""
import re
import json
from hashlib import sha256
from pathlib import Path
from ...model import bind, blockers, canonical, checksum, require, validate_model

COMPILER_VERSION='python-1.1.1'
POLICY='backend/app/services/domain_policy.py'
SERVICE='backend/app/services/domain_transitions.py'
MAP='backend/app/services/domain_policy.map.json'

POLICY_API='''
from dataclasses import dataclass

@dataclass(frozen=True)
class TrustedActor:
    # Construct only in the generated application's authenticated dependency.
    id: str
    role: str


def decide(operation_id, record, actor, parameters):
    if not isinstance(actor, TrustedActor):
        raise TypeError('AUTH_CONTEXT_REQUIRED')
    action_id = next((key for key,value in MODEL['bindings']['actions'].items()
                      if value['operation_id'] == operation_id), None)
    if action_id is None:
        raise ValueError('UNBOUND_OPERATION')
    action = next(a for a in MODEL['actions'] if a['id']==action_id)
    entity = action['entity']
    fields = next(e['fields'] for e in MODEL['entities'] if e['id']==entity)
    values = {f['id']:record[MODEL['bindings']['fields'][entity+'.'+f['id']]]
              for f in fields if MODEL['bindings']['fields'][entity+'.'+f['id']] in record}
    return step_validated(MODEL, {'entity':entity,'values':values},
                          {'id':actor.id,'role':actor.role}, {'id':action_id,'parameters':parameters})
'''
SERVICE_API='''
from copy import deepcopy
from uuid import UUID
from sqlalchemy import Integer, String, Uuid, inspect, select
from sqlalchemy.orm import undefer
from .domain_policy import MODEL, TrustedActor, decide

class DomainFailure(Exception):
    def __init__(self, reason, decision=None):
        self.reason, self.decision = reason, decision
        super().__init__(reason)


def _record_id_type(spec, entity):
    mapper = inspect(entity)
    column = mapper.columns[spec['id_field']]
    if len(mapper.primary_key) != 1 or column is not mapper.primary_key[0]:
        raise TypeError('SINGLE_PRIMARY_KEY_REQUIRED')
    expected, sql_type = {
        'integer': (int, Integer), 'string': (str, String), 'uuid': (UUID, Uuid),
    }[spec['id_type']]
    if not isinstance(column.type, sql_type) or column.type.python_type is not expected:
        raise TypeError('PRIMARY_KEY_TYPE_MISMATCH')
    return expected


async def transition(session, entity_models, operation_id, record_id, actor, parameters):
    """Own transaction; caller supplies a fresh session without an active transaction.

    The caller must not perform external side effects before this transaction commits.
    entity_models and actor are trusted server dependencies, never request objects.
    """
    if not isinstance(actor, TrustedActor):
        raise TypeError('AUTH_CONTEXT_REQUIRED')
    spec = ROUTES[operation_id]
    entity = entity_models[spec['entity']]
    if type(record_id) is not _record_id_type(spec, entity):
        raise TypeError('RECORD_ID_TYPE_MISMATCH')
    async with session.begin():
        statement = (select(entity).options(undefer('*'))
                     .where(getattr(entity,spec['id_field'])==record_id).with_for_update())
        row = (await session.execute(statement)).scalar_one_or_none()
        if row is None:
            raise DomainFailure('NOT_FOUND')
        # Owner and state come only from the locked database row, never body fields.
        logical = spec['logical_entity']
        fields = next(e['fields'] for e in MODEL['entities'] if e['id']==logical)
        record = {MODEL['bindings']['fields'][logical+'.'+f['id']]:
                  getattr(row,MODEL['bindings']['fields'][logical+'.'+f['id']]) for f in fields}
        decision = decide(operation_id,record,actor,parameters)
        if decision['status']!='allowed':
            raise DomainFailure(decision['reason'],decision)
        action = next(a for a in MODEL['actions'] if a['id']==spec['action_id'])
        effects = {r['id']:r for r in MODEL['rules']}
        for rid in action['effect_ids']:
            field=effects[rid]['field']
            setattr(row,MODEL['bindings']['fields'][logical+'.'+field],decision['state']['values'][field])
        await session.flush()
        # Materialize before commit expires ORM state; never return a live row.
        columns = list(inspect(row).mapper.column_attrs.keys())
        await session.refresh(row, attribute_names=columns)
        snapshot = deepcopy({name: getattr(row, name) for name in columns})
    return snapshot


def install_routes(router, *, session_dependency, actor_dependency, entity_models,
                   success_response, failure_response):
    """A-13 calls this with its actual auth/schema/response adapters.

    failure_response maps DomainFailure to accepted OpenAPI errors. There is no
    guessed error DTO. success_response receives a detached column-value dict.
    The actor never comes from the request body.
    """
    from fastapi import Body, Depends
    def handler_for(operation_id, record_id_type):
        async def handler(record_id: record_id_type, parameters: dict = Body(...),
                          session=Depends(session_dependency), actor=Depends(actor_dependency)):
            try:
                snapshot=await transition(session,entity_models,operation_id,record_id,actor,parameters)
            except DomainFailure as error:
                return failure_response(operation_id,error)
            return success_response(operation_id,snapshot)
        return handler
    # Validate the entire registry before installing any route or mutating data.
    key_types = {operation_id: _record_id_type(spec, entity_models[spec['entity']])
                 for operation_id, spec in ROUTES.items()}
    for operation_id,spec in ROUTES.items():
        router.add_api_route(spec['path'],handler_for(operation_id,key_types[operation_id]),methods=[spec['method']],
                             operation_id=operation_id)
'''


def emit(model, *, catalog, routes, profile):
    """routes is an accepted A-13 integration descriptor, keyed by operationId.

    Each {method, path, id_field, id_type}; catalog additionally supplies routes (identical)
    and entity field names. Actual A-09/A-13 adapters remain their owners' scope.
    """
    model=json.loads(canonical(model))
    require(profile.profile_id == 'web-fastapi-angular' and
            profile.profile_version in {'1.1.0', '1.2.0'} and
            profile.allows('architecture', 'behavior-bindings.json'), 'BEHAVIOR_PROFILE_MISMATCH')
    limits=profile.data['limits'].get('behavior')
    require(isinstance(limits,dict), 'BEHAVIOR_PROFILE_MISMATCH')
    validate_model(model,max_bytes=limits['max_model_bytes'],
                   max_expression_depth=limits['max_expression_depth'],
                   max_rules=limits['max_rules'],max_domain_values=limits['max_domain_values'])
    require(model['bindings'] is not None and not blockers(model),'BEHAVIOR_CODEGEN_BLOCKED')
    require(all(r['status']=='supported' for r in model['rules']),'BEHAVIOR_CODEGEN_UNSUPPORTED')
    bind(model,model['bindings'],catalog)
    operations={v['operation_id'] for v in model['bindings']['actions'].values()}
    require(set(routes)==operations and routes==catalog.get('routes'),'BEHAVIOR_ROUTE_BINDING_MISSING')
    descriptors={}
    for a in model['actions']:
        b=model['bindings']['actions'][a['id']]
        route=routes[b['operation_id']]
        physical=model['bindings']['entities'][a['entity']]
        require(set(route)=={'method','path','id_field','id_type'} and route['id_type'] in ('integer','string','uuid') and route['method'] in {'POST','PUT','PATCH','DELETE'} and route['path'].startswith('/') and route['path'].count('{record_id}')==1 and route['path'].count('{')==1,'BEHAVIOR_ROUTE_UNSUPPORTED')
        require(route['id_field'] in catalog['entities'][physical],'BEHAVIOR_PRIMARY_KEY_UNBOUND')
        descriptors[b['operation_id']]=dict(route,entity=physical,logical_entity=a['entity'],action_id=a['id'])
    names=list(model['bindings']['entities'].values())+list(model['bindings']['fields'].values())+[r['id_field'] for r in routes.values()]
    require(all(re.fullmatch('[A-Za-z][A-Za-z0-9_]*',x) for x in names),'BEHAVIOR_IDENTIFIER_UNSUPPORTED')
    runtime=(Path(__file__).resolve().parents[2]/'interpreter/runtime.py').read_text()
    header='# Generated by '+COMPILER_VERSION+'; model '+checksum(model)+'\n'
    files={POLICY:header+runtime+'\nMODEL = '+repr(model)+'\n'+POLICY_API,
           SERVICE:header+'ROUTES = '+repr(json.loads(canonical(descriptors)))+'\n'+SERVICE_API}
    mapping=dict(schema_version='1.0',compiler_version=COMPILER_VERSION,model_checksum=checksum(model),contract_version=model['contract_version'],owner_agent='backend',input_checksum=checksum({'model':model,'catalog':catalog,'routes':routes,'profile':profile.metadata}),
                 files={path:sha256(content.encode()).hexdigest() for path,content in sorted(files.items())},rules=[{'rule_id':r['id'],'fr_ids':r['fr_ids'],'path':POLICY,'symbol':'decide' if r['kind']!='invariant' else 'invariants','action_id':r['action']} for r in sorted(model['rules'],key=lambda x:x['id'])],
                 invariants_enforced=False,limitations=['Invariants are independent simulation assertions, not authorization guards.','A-13 must install routes with accepted auth/response/database dependencies.','Single integer/string/UUID primary keys; success adapter consumes a column snapshot.'])
    files[MAP]=canonical(mapping)+'\n'
    require(all(profile.allows('backend',path) for path in files),'BEHAVIOR_CODEGEN_GRANT_MISSING')
    require(all(len(content.encode())<=profile.data['limits']['artifacts']['max_file_bytes'] for content in files.values()),'BEHAVIOR_CODEGEN_SIZE_LIMIT')
    return files
