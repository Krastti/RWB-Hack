"""Explicit synthetic examples, never a production fallback or inferred requirements."""
from copy import deepcopy
import json
from .model import ROOT, checksum, rule_id, value_type


def literal(value):
    return {'op':'literal', 'value':value}


def ref(scope, name):
    return {'op':'ref', 'scope':scope, 'name':name}


def op(name, *args):
    return {'op':name, 'args':list(args)}


def example_model(*, unresolved=False, missing_guard=False):
    req = json.loads((ROOT/'contracts/agents/examples/requirements.json').read_text())
    text = 'Сотрудник может отменить только свою заявку в статусе draft.'
    req['functional_requirements'][0]['text'] = text
    req['acceptance_criteria'][0].update(given='Заявка сотрудника А в статусе draft.', when='Сотрудник А отменяет заявку.', then='Статус становится cancelled.')
    req['questions'] = []
    base = dict(entity='ticket', fr_ids=['FR-001'], ac_ids=['AC-001'], critical=True,
                status='supported', reason='Явное правило отмены.', evidence=[{'source':'requirement','source_id':'FR-001','quote':text}])
    def rule(decision,kind,expression,field=None):
        return dict(deepcopy(base), id=rule_id(decision), decision_id=decision, kind=kind,
                    action=None if kind=='invariant' else 'cancel', field=field, expression=expression)
    owner = op('eq', ref('state','owner'), ref('actor','id'))
    guard = op('and', op('eq',ref('actor','role'),literal('ROLE-001')), op('eq',ref('state','status'),literal('draft')))
    if not missing_guard:
        guard = op('and',guard,owner)
    rules = [rule('cancel_permission','guard',guard), rule('cancel_transition','effect',literal('cancelled'),'status'),
             rule('owner_invariant','invariant', op('or',op('ne',ref('state','status'),literal('cancelled')),op('eq',ref('previous','status'),literal('cancelled')),op('eq',ref('previous','owner'),ref('actor','id'))))]
    if unresolved:
        req['functional_requirements'][0]['text'] = 'Сотрудник может отменить заявку в статусе draft.'
        for r in rules:
            r['evidence'][0]['quote'] = req['functional_requirements'][0]['text']
        for r in (rules[0],rules[2]):
            r.update(status='unresolved',expression=None,reason='Не определено, можно ли отменять чужую заявку.')
    model = dict(schema_version='1.0', version=1,parent_checksum=None,contract_version='demo-v1',requirements_checksum=checksum(req),roles=['ROLE-001'],identities=['alice','bob'],
                 entities=[{'id':'ticket','fields':[{'id':'owner','type':value_type('identity',nullable=True)}, {'id':'status','type':value_type('enum',['draft','cancelled'])}, {'id':'priority','type':value_type('integer',minimum=0,maximum=2)}]}],
                 actions=[{'id':'cancel','entity':'ticket','parameters':[],'guard_ids':[rules[0]['id']],'effect_ids':[rules[1]['id']]}],rules=rules,
                 coverage=[{'fr_id':f['id'],'status':('partial' if unresolved else 'full') if f['id']=='FR-001' else 'not_applicable','reason':'Сценарий отмены.' if f['id']=='FR-001' else 'Вне синтетического примера отмены.'} for f in req['functional_requirements']],bindings=None)
    return req,model


def bound_example():
    from .model import bind
    req,model=example_model()
    bindings={'contract_version':'demo-v1','entities':{'ticket':'Ticket'},'fields':{'ticket.owner':'owner_id','ticket.status':'status','ticket.priority':'priority'},'actions':{'cancel':{'operation_id':'cancelTicket','screen_id':'ticket_detail','element_id':'cancel_button','public_fields':['owner','status','priority']}}}
    catalog={'contract_version':'demo-v1','entities':{'Ticket':['owner_id','status','priority']},'operations':{'cancelTicket':'Ticket'},'screens':{'ticket_detail':{'cancel_button':'cancelTicket'}}}
    return req, bind(model,bindings,catalog)
