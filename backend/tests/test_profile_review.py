from pathlib import Path
import shutil
import json

import pytest
from app.agents.profiles.loader import ProfileLoader, ProfileError
from contracts.agents.validate import Contracts, ContractError

ROOT=Path(__file__).resolve().parents[2]


def test_conditional_grants_require_true_and_preserve_path_policy():
    profile=ProfileLoader().load('web-fastapi-angular')
    assert profile.allows('integrations','integrations/client.py',conditions={'has_integrations':True})
    assert not profile.allows('integrations','integrations/client.py',conditions={'has_integrations':False})
    assert not profile.allows('integrations','integrations/client.py')
    assert not profile.allows('integrations','backend/app/main.py',conditions={'has_integrations':True})
    with pytest.raises(ProfileError):
        profile.allows('integrations','integrations/../secret')


def test_repository_root_is_used_for_schema_registry(tmp_path):
    shutil.copytree(ROOT/'templates',tmp_path/'templates')
    shutil.copytree(ROOT/'docs',tmp_path/'docs')
    # No C-02 schemas: loading must not silently use another checkout.
    with pytest.raises((ProfileError,ContractError)):
        ProfileLoader(repository_root=tmp_path).load('web-fastapi-angular')


def test_rules_size_is_bounded(tmp_path):
    for directory in ('templates','docs','contracts'):
        shutil.copytree(ROOT/directory,tmp_path/directory)
    (tmp_path/'docs/agents.md').write_bytes(b'x'*(1024*1024+1))
    with pytest.raises(ProfileError) as exc:
        ProfileLoader(repository_root=tmp_path).load('web-fastapi-angular')
    assert exc.value.code=='PROFILE_SIZE_LIMIT'


def test_plan_uses_frozen_max_path_length():
    from app.agents.profiles.loader import FrozenProfile
    loaded=ProfileLoader().load('web-fastapi-angular')
    data=loaded.data
    data['limits']['artifacts']['max_path_length']=128
    profile=FrozenProfile(json.dumps(data).encode(),loaded._rules,ROOT)
    plan=json.loads((ROOT/'contracts/agents/examples/generation-plan.json').read_text())
    task=plan['tasks'][0]
    prefix='backend/app/api/'
    task['output_paths']=[prefix+'a'*(128-len(prefix))]
    task['output_directories']=[]
    profile.validate_plan(plan)
    task['output_paths'][0]+='a'
    with pytest.raises(ProfileError):
        profile.validate_plan(plan)


def test_rules_reject_directory_symlink(tmp_path):
    from contracts.agents.validate import read_policy_bytes
    outside=tmp_path/'outside';outside.mkdir();(outside/'rules').write_text('secret')
    root=tmp_path/'root';root.mkdir();(root/'link').symlink_to(outside,target_is_directory=True)
    with pytest.raises(OSError):
        read_policy_bytes(root,'link/rules')


def test_plan_conditional_grants_match_allows():
    profile=ProfileLoader().load('web-fastapi-angular')
    plan=json.loads((ROOT/'contracts/agents/examples/generation-plan.json').read_text())
    task=dict(plan['tasks'][0])
    task.update(task_id='integrations',stage_id='integrations',owner_agent='integrations',output_paths=['integrations/README.md'],output_directories=[],depends_on=[])
    plan['tasks'].append(task)
    profile.validate_plan(plan,conditions={'has_integrations':True})
    with pytest.raises(ProfileError):
        profile.validate_plan(plan,conditions={'has_integrations':False})


def restore(loader, snapshot):
    return loader.restore(snapshot, expected_checksum=snapshot['checksum'],
                          expected_rules_checksum=snapshot['rules_checksum'])


@pytest.mark.parametrize('field', ['profile_json', 'rules_text'])
def test_restore_rejects_changed_snapshot_bytes(field):
    loader = ProfileLoader()
    snapshot = loader.load('web-fastapi-angular').snapshot()
    snapshot[field] += ' '
    with pytest.raises(ProfileError) as exc:
        restore(loader, snapshot)
    assert exc.value.code == 'PROFILE_SNAPSHOT_CHANGED'


def test_restore_uses_saved_bytes_after_current_profile_and_defaults_change(tmp_path):
    for directory in ('templates', 'docs', 'contracts'):
        shutil.copytree(ROOT / directory, tmp_path / directory)
    original = ProfileLoader(repository_root=tmp_path).load('web-fastapi-angular')
    snapshot = original.snapshot()
    (tmp_path / 'templates/profiles/web-fastapi-angular/profile.json').write_text('{}')
    (tmp_path / 'docs/agents.md').write_text('changed rules')
    loader = ProfileLoader(repository_root=tmp_path, defaults={'web-fastapi-angular': '2.0.0'})
    restored = restore(loader, snapshot)
    assert restored.snapshot() == snapshot
    assert restored.rules_text == original.rules_text
    with pytest.raises(ProfileError):
        loader.load('web-fastapi-angular')


def test_restore_old_version_with_registered_schema():
    from app.agents.profiles.loader import ProfileSource
    source = ProfileSource('templates/profiles/web-fastapi-angular/profile.json',
                           'templates/profiles/web-fastapi-angular/profile.schema.json')
    snapshot = ProfileLoader().load('web-fastapi-angular').snapshot()
    loader = ProfileLoader(registry={('web-fastapi-angular', '1.0.0'): source},
                           defaults={'web-fastapi-angular': '2.0.0'})
    assert restore(loader, snapshot).snapshot() == snapshot


def test_restore_rejects_unknown_snapshot_version():
    loader = ProfileLoader()
    snapshot = loader.load('web-fastapi-angular').snapshot()
    snapshot['snapshot_version'] = '2.0'
    with pytest.raises(ProfileError) as exc:
        restore(loader, snapshot)
    assert exc.value.code == 'PROFILE_SNAPSHOT_INVALID'


def test_restore_rejects_unregistered_profile_version():
    loader = ProfileLoader(registry={})
    snapshot = ProfileLoader().load('web-fastapi-angular').snapshot()
    with pytest.raises(ProfileError) as exc:
        restore(loader, snapshot)
    assert exc.value.code == 'PROFILE_NOT_SUPPORTED'
