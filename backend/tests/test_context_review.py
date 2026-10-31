import json
import pytest
from app.agents.context.builder import ContextBuilder, ContextError


def test_architecture_requires_correct_requirement_kind():
    requirement={'artifact_id':'r','kind':'data_model'}
    architecture={'metadata':{'artifact_id':'a','kind':'architecture'},'content':json.dumps({'requirements_artifact_id':'r','contracts':{}})}
    with pytest.raises(ContextError):
        ContextBuilder._check_references([architecture,{'metadata':requirement}],{'a','r'},'integrations')


def test_optional_contract_reference_may_be_absent_but_included_is_validated():
    req={'metadata':{'artifact_id':'r','kind':'requirements'}}
    architecture={'metadata':{'artifact_id':'a','kind':'architecture'},'content':json.dumps({'requirements_artifact_id':'r','contracts':{'data_model':{'artifact_id':'d','kind':'data_model'}}})}
    ContextBuilder._check_references([req,architecture],{'r','a'},'integrations')
    with pytest.raises(ContextError):
        ContextBuilder._check_references([req,architecture,{'metadata':{'artifact_id':'d','kind':'requirements'}}],{'r','a','d'},'integrations')


def test_integration_contract_must_be_present():
    req = {'metadata': {'artifact_id': 'r', 'kind': 'requirements'}}
    architecture = {'metadata': {'artifact_id': 'a', 'kind': 'architecture'},
                    'content': json.dumps({'requirements_artifact_id': 'r', 'contracts': {},
                                           'integrations': [{'contract_artifact_id': 'missing'}]})}
    with pytest.raises(ContextError):
        ContextBuilder._check_references([req, architecture], {'r', 'a'}, 'integrations')


def test_execution_configuration_is_not_sent_to_model():
    from app.agents.context.example import prepare_example
    context = prepare_example('frontend')
    data = json.loads(context.data)
    assert 'model_configuration' not in data['execution_inputs']
    assert 'compiler_configuration' not in data['execution_inputs']
    assert 'model_configuration' in context.manifest['execution_inputs']


def test_documentation_requires_authoritative_results():
    from app.agents.context.example import fixture_inputs, FixtureResolver, OWNER
    from app.agents.profiles import ProfileLoader
    artifact = fixture_inputs()['requirements']
    request = {'schema_version': '1.0', 'run_id': artifact.run_id, 'stage_id': 'documentation',
               'attempt': 1, 'contract_version': artifact.contract_version,
               'profile_id': 'web-fastapi-angular', 'limits': {'max_calls': 1, 'max_output_tokens': 3000},
               'input_artifact_ids': [artifact.metadata['artifact_id']]}
    builder = ContextBuilder(FixtureResolver([artifact]), max_context_bytes=256 * 1024)
    kwargs = dict(owner_id=OWNER, owner_agent='documentation',
                  profile=ProfileLoader().load('web-fastapi-angular'),
                  references={artifact.metadata['artifact_id']: artifact.metadata},
                  execution_inputs={'prompt': b'', 'template': b'', 'model_configuration': b'{}',
                                    'compiler_configuration': b'{}'})
    with pytest.raises(ContextError, match='CONTEXT_RESULTS_REQUIRED'):
        builder.prepare(request, **kwargs)
    with pytest.raises(ContextError, match='CONTEXT_RESULTS_INCOMPLETE'):
        builder.prepare(request, accepted_result_references={'missing': {}}, **kwargs)


def test_fr_selection_closes_transitive_relationships():
    requirements = {'functional_requirements': [{'id': 'FR-001'}, {'id': 'FR-002'}, {'id': 'FR-003'}],
                    'links': [{'related_fr_ids': ['FR-001', 'FR-002']},
                              {'related_fr_ids': ['FR-002', 'FR-003']}]}
    assert ContextBuilder._selection(requirements, ('FR-001',)) == ['FR-001', 'FR-002', 'FR-003']


def test_architecture_and_plan_reject_other_profile(monkeypatch):
    from dataclasses import replace
    from hashlib import sha256
    from app.agents.context import example
    from app.agents.profiles import ProfileLoader
    for kind in ('architecture', 'generation_plan'):
        fixtures = example.fixture_inputs()
        artifact = fixtures[kind]
        payload = json.loads(artifact.content)
        payload['profile_id'] = 'other-profile'
        raw = json.dumps(payload).encode()
        metadata = dict(artifact.metadata, checksum=sha256(raw).hexdigest(), size_bytes=len(raw))
        changed = replace(artifact, metadata=metadata, content=raw)
        artifacts = [fixtures['requirements'], changed]
        request = {'schema_version': '1.0', 'run_id': artifact.run_id, 'stage_id': 'integrations',
                   'attempt': 1, 'contract_version': artifact.contract_version,
                   'profile_id': 'web-fastapi-angular', 'limits': {'max_calls': 1, 'max_output_tokens': 3000},
                   'input_artifact_ids': [a.metadata['artifact_id'] for a in artifacts]}
        with pytest.raises(ContextError, match='CONTEXT_PROFILE_MISMATCH'):
            ContextBuilder(example.FixtureResolver(artifacts), max_context_bytes=256 * 1024).prepare(
                request, owner_id=example.OWNER, owner_agent='integrations',
                profile=ProfileLoader().load('web-fastapi-angular'),
                references={a.metadata['artifact_id']: a.metadata for a in artifacts},
                execution_inputs={'prompt': b'', 'template': b'', 'model_configuration': b'{}',
                                  'compiler_configuration': b'{}'})
