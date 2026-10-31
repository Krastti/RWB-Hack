"""C-05 contest policy: compatibility, frozen rules and conditional ownership."""
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from app.agents.profiles import ProfileLoader, ProfileError


@pytest.mark.parametrize('name', ['web-fastapi-angular', 'russtech-fullstack'])
def test_roundtrip(name):
    loader = ProfileLoader()
    profile = loader.load(name)
    restored = loader.restore(profile.snapshot(), expected_checksum=profile.checksum,
                              expected_rules_checksum=profile.metadata['rules_checksum'])
    assert restored.data == profile.data
    assert restored.rules_text == profile.rules_text
    assert restored.profile_id == name


def test_contest_conditions_and_ownership():
    profile = ProfileLoader().load('russtech-fullstack')
    with pytest.raises(ProfileError, match='PROFILE_CONDITION_REQUIRED'):
        profile.required_outputs()
    base = profile.required_outputs(conditions={'has_android': False, 'has_integrations': False})
    mobile = profile.required_outputs(conditions={'has_android': True, 'has_integrations': False})
    assert not any(a['path'].startswith('android/') for a in base)
    assert any(a['path'].endswith('MainActivity.kt') for a in mobile)
    for item in mobile:
        assert profile.allows(item['owner'], item['path'], conditions={'has_android': True})
    assert not profile.allows('android', 'android/app/build.gradle.kts')
    assert profile.allows('android', 'android/app/build.gradle.kts', conditions={'has_android': True})
    assert not profile.allows('backend', 'backend/migrations/Initial.cs')
    assert not profile.allows('android', '.gitlab-ci.yml', conditions={'has_android': True})
    assert profile.allows('verification', '.gitlab-ci.yml')


def test_profiles_keep_different_stack_and_rules():
    loader = ProfileLoader()
    legacy = loader.load('web-fastapi-angular')
    contest = loader.load('russtech-fullstack')
    assert legacy.data['stack']['migrations'] == 'Alembic'
    assert contest.data['stack']['migrations'] == 'EF Core migrations'
    assert legacy.data['stack']['frontend'][0] == 'Angular 22'
    assert contest.data['stack']['frontend'][0] == 'Angular 20.1.6'
    assert legacy.metadata['rules_checksum'] != contest.metadata['rules_checksum']
    assert 'Kotlin 2.2.21' in contest.rules_text
    changed = contest.data
    changed['stack']['backend'] = ['FastAPI']
    snapshot = deepcopy(contest.snapshot())
    snapshot['profile_json'] = json.dumps(changed)
    snapshot['checksum'] = sha256(snapshot['profile_json'].encode()).hexdigest()
    with pytest.raises(ProfileError, match='PROFILE_SCHEMA_INVALID'):
        loader.restore(snapshot, expected_checksum=snapshot['checksum'],
                       expected_rules_checksum=contest.metadata['rules_checksum'])


def test_unknown_profile_cannot_select_rules_path():
    from contracts.agents.validate import load_profile_rules, ContractError
    with pytest.raises(ContractError):
        load_profile_rules({'profile_id': '../russtech-fullstack', 'rules_document': 'README.md'})
