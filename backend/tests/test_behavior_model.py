import asyncio
from copy import deepcopy
import json
from uuid import uuid4

import httpx
import pytest

from backend.app.agents.behavior.example import prepare_example
from backend.app.agents.behavior.fixtures import bound_example, example_model
from backend.app.agents.behavior.model import (
    ModelError, bind, blockers, canonical, checksum, validate_model, value_type,
)
from backend.app.agents.behavior.node import BehaviorNode, artifact
from backend.app.agents.behavior.profile import behavior_profile
from backend.app.agents.generation.example import ExampleLedger
from backend.app.agents.llm import LLMConfig
from backend.app.agents.profiles import ProfileLoader, ProfileSource
from contracts.agents.validate import Contracts


def limited_profile(**changes):
    profile = behavior_profile()
    data = profile.data
    data['limits']['behavior'].update(changes)
    snapshot = profile.snapshot()
    snapshot.update(profile_json=canonical(data), checksum=checksum(data))
    loader = ProfileLoader(registry={('web-fastapi-angular', '1.1.0'): ProfileSource(
        'templates/profiles/web-fastapi-angular/1.1.0/profile.json',
        'templates/profiles/web-fastapi-angular/1.1.0/profile.schema.json',
    )})
    return loader.restore(snapshot, expected_checksum=snapshot['checksum'],
                          expected_rules_checksum=snapshot['rules_checksum'])


def node_call(requirements, model, profile=None):
    profile = profile or behavior_profile()
    config = LLMConfig(base_url='https://fixture.invalid/v1', model='behavior-fixture', api_key='fixture',
                       max_output_tokens=4096, timeout_seconds=10)
    request, context, configuration = prepare_example(requirements, profile=profile, config=config)
    calls = []

    def respond(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={
            'choices': [{'message': {'content': canonical(model)}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 40, 'total_tokens': 140},
        })

    node = BehaviorNode(config=config, ledger=ExampleLedger(), transport=httpx.MockTransport(respond))
    options = dict(attempt_id=str(uuid4()), context=context, profile=profile, accepted_answers={},
                   model_configuration=configuration)
    return node, request, options, calls


@pytest.mark.parametrize('status', ['partial', 'unsupported'])
def test_incomplete_coverage_without_rules_stays_blocked(status):
    requirements, model = example_model()
    model.update(actions=[], rules=[])
    model['coverage'][0].update(status=status, reason='The requirement cannot be formalized.')
    validate_model(model, requirements)
    assert 'FR-001' in blockers(model)
    node, request, options, calls = node_call(requirements, model)
    result = asyncio.run(node.run(request, **options))
    assert result['status'] == 'failed'
    assert len(result['output_artifacts']) == 1
    assert result['diagnostics'][0]['related_fr_ids'] == ['FR-001']
    assert len(calls) == result['usage']['calls'] == 1
    Contracts().validate('agent-result', result)


def test_noncritical_unresolved_rules_do_not_hide_partial_fr():
    requirements, model = example_model(unresolved=True)
    for rule in model['rules']:
        rule['critical'] = False
    validate_model(model, requirements)
    assert 'FR-001' in blockers(model)
    node, request, options, _ = node_call(requirements, model)
    result = asyncio.run(node.run(request, **options))
    assert result['status'] == 'failed'
    assert result['diagnostics'][0]['related_fr_ids'] == ['FR-001']


@pytest.mark.parametrize(('limits', 'code'), [
    ({'max_bytes': 1}, 'BEHAVIOR_SIZE_LIMIT'),
    ({'max_rules': 1}, 'BEHAVIOR_RULE_LIMIT'),
    ({'max_expression_depth': 1}, 'BEHAVIOR_EXPRESSION_DEPTH'),
    ({'max_domain_values': 1}, 'BEHAVIOR_DOMAIN_LIMIT'),
])
def test_validator_enforces_effective_limits(limits, code):
    _, model = example_model()
    with pytest.raises(ModelError, match='^' + code + '$'):
        validate_model(model, **limits)


@pytest.mark.parametrize(('limits', 'code'), [
    ({'max_model_bytes': 1}, 'BEHAVIOR_SIZE_LIMIT'),
    ({'max_rules': 1}, 'BEHAVIOR_RULE_LIMIT'),
    ({'max_expression_depth': 1}, 'BEHAVIOR_EXPRESSION_DEPTH'),
    ({'max_domain_values': 1}, 'BEHAVIOR_DOMAIN_LIMIT'),
])
def test_node_and_artifact_use_restored_profile_limits(limits, code):
    requirements, model = example_model()
    profile = limited_profile(**limits)
    node, request, options, calls = node_call(requirements, model, profile)
    result = asyncio.run(node.run(request, **options))
    assert result['status'] == 'failed'
    assert result['output_artifacts'] == []
    assert result['diagnostics'][0]['code'] == code
    assert len(calls) == result['usage']['calls'] == 1
    assert result['usage']['input_tokens'] == 100
    assert result['usage']['output_tokens'] == 40
    with pytest.raises(ModelError, match='^' + code + '$'):
        artifact(model, attempt_id=str(uuid4()), profile=profile)


def test_previous_model_is_checked_before_dispatch():
    requirements, model = example_model()
    node, request, options, calls = node_call(requirements, model, limited_profile(max_rules=1))
    with pytest.raises(ModelError, match='^BEHAVIOR_RULE_LIMIT$'):
        asyncio.run(node.run(request, previous=model, **options))
    assert calls == []


def test_node_rejects_oversized_integer_domain():
    requirements, model = example_model()
    model['entities'][0]['fields'][2]['type']['maximum'] = 1000
    node, request, options, calls = node_call(requirements, model)
    result = asyncio.run(node.run(request, **options))
    assert result['status'] == 'failed'
    assert result['output_artifacts'] == []
    assert result['diagnostics'][0]['code'] == 'BEHAVIOR_DOMAIN_LIMIT'
    assert len(calls) == result['usage']['calls'] == 1
    assert model['entities'][0]['fields'][2]['type']['maximum'] == 1000


def test_limits_accept_exact_boundaries():
    _, model = example_model()

    def depth(expression):
        return 1 + max((depth(child) for child in expression.get('args', [])), default=0)

    maximum_depth = max(depth(rule['expression']) for rule in model['rules'])
    validate_model(model, max_bytes=len(canonical(model).encode()), max_rules=len(model['rules']),
                   max_expression_depth=maximum_depth, max_domain_values=3)
    with pytest.raises(ModelError, match='^BEHAVIOR_EXPRESSION_DEPTH$'):
        validate_model(model, max_expression_depth=maximum_depth - 1)


@pytest.mark.parametrize('parameters', [False, True])
@pytest.mark.parametrize('maximum', [64, 1000, 9007199254740991])
def test_integer_cardinality_is_bounded_without_truncation(parameters, maximum):
    _, model = example_model()
    kind = value_type('integer', minimum=0, maximum=maximum)
    if parameters:
        model['actions'][0]['parameters'] = [{'id': 'amount', 'type': kind}]
    else:
        model['entities'][0]['fields'][2]['type'] = kind
    before = deepcopy(model)
    with pytest.raises(ModelError, match='^BEHAVIOR_DOMAIN_LIMIT$'):
        validate_model(model)
    assert model == before
    kind['maximum'] = 63
    validate_model(model)


def test_nullable_domain_counts_the_null_value():
    _, model = example_model()
    model['entities'][0]['fields'][2]['type'] = value_type('integer', minimum=0, maximum=2, nullable=True)
    with pytest.raises(ModelError, match='^BEHAVIOR_DOMAIN_LIMIT$'):
        validate_model(model, max_domain_values=3)
    validate_model(model, max_domain_values=4)


@pytest.mark.parametrize('bound', [False, True])
def test_artifact_rejects_the_wrong_binding_phase(bound):
    _, model = example_model() if bound else bound_example()
    with pytest.raises(ModelError, match='^BEHAVIOR_BINDING_PHASE_MISMATCH$'):
        artifact(model, attempt_id=str(uuid4()), profile=behavior_profile(), bound=bound)


@pytest.mark.parametrize('mutation', ['version', 'entities', 'fields', 'actions', 'unknown_field'])
def test_local_binding_consistency_is_checked_without_catalog(mutation):
    _, model = bound_example()
    if mutation == 'version':
        model['bindings']['contract_version'] = 'another-version'
    elif mutation == 'unknown_field':
        model['bindings']['actions']['cancel']['public_fields'].append('absent')
    else:
        model['bindings'][mutation] = {}
    with pytest.raises(ModelError):
        validate_model(model)
    with pytest.raises(ModelError):
        artifact(model, attempt_id=str(uuid4()), profile=behavior_profile(), bound=True)


def test_binding_still_checks_the_accepted_physical_catalog():
    _, model = example_model()
    _, bound = bound_example()
    catalog = {'contract_version': model['contract_version'],
               'entities': {'Ticket': ['owner_id', 'status', 'priority']},
               'operations': {'cancelTicket': 'AnotherEntity'},
               'screens': {'ticket_detail': {'cancel_button': 'cancelTicket'}}}
    with pytest.raises(ModelError, match='^BEHAVIOR_OPERATION_BINDING_INVALID$'):
        bind(model, bound['bindings'], catalog)


def test_valid_bound_artifact_preserves_immutable_lineage():
    _, logical = example_model()
    _, bound = bound_example()
    before = deepcopy(bound)
    result = artifact(bound, attempt_id=str(uuid4()), profile=behavior_profile(), bound=True)
    assert json.loads(result['content']) == bound
    assert bound == before
    assert bound['parent_checksum'] == checksum(logical)
    assert bound['version'] == logical['version'] + 1
    assert result['path'] == 'behavior-bindings.json'
    assert result['author_agent'] == 'architecture'


def test_real_context_keeps_model_configuration_private():
    requirements, model = example_model()
    node, request, options, calls = node_call(requirements, model)
    result = asyncio.run(node.run(request, **options))
    assert result['status'] == 'succeeded'
    prompt = json.loads(calls[0]['messages'][1]['content'])
    assert 'model_configuration' not in prompt['context']['execution_inputs']
    assert 'compiler_configuration' not in prompt['context']['execution_inputs']


def test_changed_model_configuration_is_rejected_before_dispatch():
    requirements, model = example_model()
    node, request, options, calls = node_call(requirements, model)
    configuration = json.loads(options['model_configuration'])
    configuration['model'] = 'changed'
    options['model_configuration'] = canonical(configuration).encode()
    with pytest.raises(ModelError, match='^BEHAVIOR_MODEL_CONFIG_MISMATCH$'):
        asyncio.run(node.run(request, **options))
    assert calls == []
