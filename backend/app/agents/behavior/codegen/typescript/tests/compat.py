"""Explicit-run profile checks and optional joint Python/TypeScript regressions."""
from copy import deepcopy
from hashlib import sha256
import importlib.util
import itertools
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from ....fixtures import bound_example
from ....model import ModelError, canonical
from ....profile import behavior_profile
from .....profiles import ProfileLoader, ProfileSource
from ..emitter import emit, POLICY, MAP


def available(name):
    try:
        return importlib.util.find_spec(name) is not None
    except ModuleNotFoundError:
        return False


PYTHON_READY = available('backend.app.agents.behavior.codegen.python.emitter')
HTTP_READY = PYTHON_READY and available('backend.tests.test_python_behavior_codegen') and all(
    available(name) for name in ('sqlalchemy', 'aiosqlite', 'greenlet', 'fastapi', 'httpx'))


def inputs():
    _, model = bound_example()
    routes = {'cancelTicket': {'method': 'POST', 'path': '/tickets/{record_id}/cancel',
                              'id_field': 'id', 'id_type': 'integer'}}
    catalog = {'contract_version': 'demo-v1', 'entities': {'Ticket': ['id', 'owner_id', 'status', 'priority']},
               'operations': {'cancelTicket': 'Ticket'}, 'screens': {'ticket_detail': {'cancel_button': 'cancelTicket'}},
               'routes': routes}
    return model, catalog, routes


def profile_version(version):
    root = f'templates/profiles/web-fastapi-angular/{version}'
    loader = ProfileLoader(registry={('web-fastapi-angular', version): ProfileSource(
        root + '/profile.json', root + '/profile.schema.json')})
    return loader, loader.load('web-fastapi-angular', version=version)


def decide_many(files, cases):
    with TemporaryDirectory() as directory:
        root = Path(directory)
        (root / 'package.json').write_text('{"type":"module"}')
        (root / 'policy.ts').write_text(files[POLICY])
        (root / 'run.mjs').write_text("import {decide} from './policy.ts';\nconst cases=" + json.dumps(cases) +
            ";console.log(JSON.stringify(cases.map(([record,actor])=>decide('cancelTicket',record,actor,{}))));")
        return json.loads(subprocess.check_output([os.environ.get('NODE_BINARY', 'node'), str(root / 'run.mjs')], text=True))


class FrozenProfileTests(unittest.TestCase):
    def test_default_profile_is_not_a_behavior_opt_in(self):
        model, catalog, _ = inputs()
        with self.assertRaisesRegex(ModelError, 'BEHAVIOR_PROFILE_MISMATCH'):
            emit(model, catalog=catalog, profile=ProfileLoader().load('web-fastapi-angular'))

    def test_both_supported_versions_preserve_model_identity(self):
        model, catalog, _ = inputs()
        for version in ('1.1.0', '1.2.0'):
            _, profile = profile_version(version)
            files = emit(model, catalog=catalog, profile=profile)
            self.assertEqual(files, emit(deepcopy(model), catalog=deepcopy(catalog), profile=profile))
            self.assertEqual(json.loads(files[MAP])['compiler_version'], 'typescript-1.0.2')
            self.assertEqual(json.loads(files[MAP])['contract_version'], model['contract_version'])

    def test_all_actual_frozen_limits_apply(self):
        model, catalog, _ = inputs()
        loader, profile = profile_version('1.1.0')
        for key, value, code in (
            ('max_model_bytes', len(canonical(model).encode()) - 1, 'BEHAVIOR_SIZE_LIMIT'),
            ('max_expression_depth', 1, 'BEHAVIOR_EXPRESSION_DEPTH'),
            ('max_rules', 2, 'BEHAVIOR_RULE_LIMIT'),
            ('max_domain_values', 2, 'BEHAVIOR_DOMAIN_LIMIT'),
        ):
            with self.subTest(limit=key):
                snapshot = profile.snapshot()
                data = json.loads(snapshot['profile_json'])
                data['limits']['behavior'][key] = value
                snapshot['profile_json'] = json.dumps(data)
                snapshot['checksum'] = sha256(snapshot['profile_json'].encode()).hexdigest()
                limited = loader.restore(snapshot, expected_checksum=snapshot['checksum'],
                                         expected_rules_checksum=snapshot['rules_checksum'])
                with self.assertRaisesRegex(ModelError, code):
                    emit(model, catalog=catalog, profile=limited)


@unittest.skipUnless(PYTHON_READY, 'optional joint check requires PR #150')
class JointProjectionTests(unittest.TestCase):
    def test_same_model_has_identical_decisions_on_216_visible_inputs(self):
        from ...python.emitter import emit as emit_python, POLICY as PYTHON_POLICY, MAP as PYTHON_MAP
        model, catalog, routes = inputs()
        profile = behavior_profile()
        python_files = emit_python(model, catalog=catalog, routes=routes, profile=profile)
        ts_files = emit(model, catalog=catalog, profile=profile)
        self.assertEqual(json.loads(python_files[PYTHON_MAP])['model_checksum'], json.loads(ts_files[MAP])['model_checksum'])
        missing = object()
        cases, expected = [], []
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'policy.py'
            path.write_text(python_files[PYTHON_POLICY])
            name = 'joint_python_policy'
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            try:
                spec.loader.exec_module(module)
                for owner, status, priority, (actor, role) in itertools.product(
                    ('alice', 'bob', None, missing), ('draft', 'cancelled', missing),
                    (0, 2, 3, True, None, missing),
                    (('alice', 'ROLE-001'), ('bob', 'ROLE-001'), ('alice', 'ROLE-999')),
                ):
                    record = {key: value for key, value in (
                        ('owner_id', owner), ('status', status), ('priority', priority)) if value is not missing}
                    cases.append([record, {'id': actor, 'role': role}])
                    expected.append(module.decide('cancelTicket', record, module.TrustedActor(actor, role), {}))
            finally:
                del sys.modules[name]
        self.assertEqual(len(cases), 216)
        self.assertEqual(decide_many(ts_files, cases), expected)

    def test_private_owner_stays_unknown_in_frontend(self):
        from ...python.emitter import emit as emit_python
        model, catalog, routes = inputs()
        model['bindings']['actions']['cancel']['public_fields'].remove('owner')
        emit_python(model, catalog=catalog, routes=routes, profile=behavior_profile())
        files = emit(model, catalog=catalog, profile=behavior_profile())
        result = decide_many(files, [[{'owner_id': 'alice', 'status': 'draft', 'priority': 1},
                                     {'id': 'alice', 'role': 'ROLE-001'}]])
        self.assertEqual(result[0]['status'], 'unknown')


@unittest.skipUnless(HTTP_READY, 'optional HTTP bridge requires updated PR #150 and async test dependencies')
class JointHttpTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_record_ids_and_committed_dto_reach_client_policy(self):
        from fastapi.encoders import jsonable_encoder
        from sqlalchemy import select
        from backend.tests.test_python_behavior_codegen import application
        model, catalog, _ = inputs()
        files = emit(model, catalog=catalog, profile=behavior_profile())
        for kind in ('integer', 'string', 'uuid'):
            with self.subTest(id_type=kind):
                async with application(kind, response_fields=('id', 'owner_id', 'status', 'priority')) as case:
                    async with case.sessions() as session:
                        row = (await session.execute(select(case.entity.id, case.entity.owner_id,
                            case.entity.status, case.entity.priority).where(case.entity.id == case.keys[0]))).mappings().one()
                    before = jsonable_encoder(dict(row))
                    response = await case.client.post(f'/tickets/{case.keys[0]}/cancel', json={})
                    self.assertEqual(response.status_code, 200)
                    after = response.json()
                    self.assertEqual(after['id'], before['id'])
                    self.assertEqual(after['status'], 'cancelled')
                    decisions = decide_many(files, [[record, {'id': 'alice', 'role': 'ROLE-001'}]
                                                   for record in (before, after)])
                    self.assertEqual([decision['status'] for decision in decisions], ['allowed', 'denied'])


if __name__ == '__main__':
    unittest.main()
