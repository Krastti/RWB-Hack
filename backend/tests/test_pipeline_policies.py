from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

import httpx
import pytest

from app.services.pipeline.policies import (
    PolicyPreparation, PolicyQuestion, add_question, approve, canonical, proposal_schema,
)

ROOT = Path(__file__).resolve().parents[2]


def inputs():
    requirements = json.loads((ROOT / 'templates/analysis/examples/draft.answered.json').read_text())['requirements']
    proposal = json.loads((ROOT / 'config/tickets-policy.example.json').read_text())
    checksum = sha256(canonical(requirements).encode()).hexdigest()
    for policy in proposal.values():
        policy['requirements_checksum'] = checksum
    for operation in proposal['api_policy']['operations']:
        for key in ('authentication', 'authorization_rules', 'grants', 'security_requirement_indices'):
            del operation[key]
    return requirements, proposal, checksum


@pytest.mark.parametrize('domain', ['repair', 'library'])
def test_source_derived_policy_has_no_model_selected_grants(domain):
    requirements, proposal, checksum = inputs()
    if domain == 'library':
        substitutions = [('заявк', 'запрос'), ('Заявк', 'Запрос'), ('оборудования', 'книг'),
                         ('неисправности', 'издания'), ('Сотрудник', 'Читатель'), ('сотрудник', 'читатель')]
        raw = canonical(requirements)
        for old, new in substitutions:
            raw = raw.replace(old, new)
        requirements = json.loads(raw)
        proposal = json.loads(canonical(proposal).replace('Ticket', 'BookRequest').replace('tickets', 'book_requests'))
        checksum = sha256(canonical(requirements).encode()).hexdigest()
        for p in proposal.values():
            p['requirements_checksum'] = checksum
    original = deepcopy(proposal)
    result = approve(proposal, requirements, checksum)
    assert proposal == original
    assert result['api_policy']['operations'][0]['grants'] == [
        {'role_id': 'ROLE-001', 'permission_indices': [0]}]
    for op in result['api_policy']['operations']:
        assert op['authentication'] == 'bearer'
        assert requirements['security_requirements'][0]['text'] in op['authorization_rules']
    assert approve(proposal, requirements, checksum) == result


def test_proposal_cannot_authorize_itself_or_silently_drop_fr():
    r, p, checksum = inputs()
    p['api_policy']['operations'][0]['grants'] = [{'role_id': 'ROLE-002', 'permission_indices': [0]}]
    with pytest.raises(ValueError, match='INVALID_PROPOSAL'):
        approve(p, r, checksum)
    r, p, checksum = inputs()
    p['api_policy']['operations'][0]['method'] = 'delete'
    with pytest.raises(PolicyQuestion):
        approve(p, r, checksum)
    r, p, checksum = inputs()
    p['api_policy']['coverage'][0].update(kind='outside_api', operation_ids=[])
    with pytest.raises(PolicyQuestion):
        approve(p, r, checksum)


def test_unknown_source_permission_requires_stable_bounded_question():
    r, p, checksum = inputs()
    r['roles'][0]['permissions'] = ['Неопределённое действие']
    with pytest.raises(PolicyQuestion) as error:
        approve(p, r, checksum)
    old = deepcopy(r)
    add_question(r, old, run_id='a42e19f4-80a9-5f04-941c-6d3ae4437744',
                 fr_ids=error.value.fr_ids, max_rounds=2)
    assert r['questions_revision'] == 2
    assert r['questions'][-1]['required'] and 'answer' not in r['questions'][-1]
    assert r['questions'][0] == old['questions'][0]
    with pytest.raises(ValueError, match='UNRESOLVED_AFTER_ANSWER'):
        add_question(r, old, run_id='a42e19f4-80a9-5f04-941c-6d3ae4437744',
                     fr_ids=error.value.fr_ids, max_rounds=2)


def test_changed_checksum_and_ambiguous_naming_are_rejected():
    r, p, checksum = inputs()
    with pytest.raises(ValueError, match='SNAPSHOT_MISMATCH'):
        approve(p, r, 'f' * 64)
    p['naming_policy']['bindings'].append(deepcopy(p['naming_policy']['bindings'][0]))
    with pytest.raises(ValueError, match='INVALID_NAMING_POLICY'):
        approve(p, r, checksum)
