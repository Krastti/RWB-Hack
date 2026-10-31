"""PR #150 regressions against emitted FastAPI/SQLAlchemy code, not a mock service."""

from contextlib import asynccontextmanager
from copy import deepcopy
from hashlib import sha256
import importlib
import importlib.util
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from uuid import UUID, uuid4


def available(name):
    try:
        return importlib.util.find_spec(name) is not None
    except ModuleNotFoundError:
        return False


BEHAVIOR_READY = all(available(name) for name in (
    'backend.app.agents.behavior.model', 'backend.app.agents.behavior.interpreter.runtime'))
ASYNC_READY = all(available(name) for name in ('sqlalchemy', 'fastapi', 'httpx', 'aiosqlite', 'greenlet'))


def emitted(kind, profile=None):
    from backend.app.agents.behavior.codegen.python.example import inputs
    from backend.app.agents.behavior.codegen.python.emitter import emit
    from backend.app.agents.behavior.profile import behavior_profile
    model, catalog, routes = inputs()
    routes['cancelTicket']['id_type'] = kind
    catalog['routes'] = deepcopy(routes)
    return emit(model, catalog=catalog, routes=routes, profile=profile or behavior_profile())


@asynccontextmanager
async def application(kind, *, expire=True, fail_commit=False, response_fields=('id', 'status', 'revision')):
    from fastapi import APIRouter, FastAPI
    from fastapi.encoders import jsonable_encoder
    from fastapi.responses import JSONResponse
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import Integer, String, Uuid, event, text
    from sqlalchemy.dialects.postgresql.psycopg import dialect
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.orm import DeclarativeBase, mapped_column
    from backend.app.agents.behavior.codegen.python.emitter import POLICY, SERVICE

    files = emitted(kind)
    package = 'emitted_review_' + uuid4().hex
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    with TemporaryDirectory() as directory:
        root = Path(directory) / package
        root.mkdir()
        (root / '__init__.py').write_text('')
        (root / 'domain_policy.py').write_text(files[POLICY])
        (root / 'domain_transitions.py').write_text(files[SERVICE])
        sys.path.insert(0, directory)
        try:
            policy = importlib.import_module(package + '.domain_policy')
            service = importlib.import_module(package + '.domain_transitions')

            class Base(DeclarativeBase):
                pass

            class Ticket(Base):
                __tablename__ = 'tickets'
                id = mapped_column({'integer': Integer, 'string': String(80), 'uuid': Uuid}[kind], primary_key=True)
                owner_id = mapped_column(String, nullable=False, deferred=True)
                status = mapped_column(String, nullable=False)
                priority = mapped_column(Integer, nullable=False)
                revision = mapped_column(Integer, nullable=False, server_default=text('0'))
                note = mapped_column(String, nullable=False, deferred=True)

            keys = {'integer': [1, 2, 3], 'string': ['one', 'two', 'three'],
                    'uuid': [UUID(int=i) for i in (1, 2, 3)]}[kind]
            sessions = async_sessionmaker(engine) if expire else async_sessionmaker(engine, expire_on_commit=False)
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
                await connection.exec_driver_sql('CREATE TRIGGER advance_revision AFTER UPDATE OF status ON tickets '
                    'BEGIN UPDATE tickets SET revision = OLD.revision + 1 WHERE id = NEW.id; END')
            async with sessions.begin() as session:
                session.add_all([Ticket(id=key, owner_id=owner, status=status, priority=1, note='column value')
                    for key, owner, status in zip(keys, ('alice', 'bob', 'alice'), ('draft', 'draft', 'cancelled'))])
            statements, snapshots, response_io = [], [], []
            sql_count = []

            @event.listens_for(engine.sync_engine, 'before_cursor_execute')
            def count_sql(*args):
                sql_count.append(1)

            @event.listens_for(engine.sync_engine, 'before_execute')
            def capture(connection, clause, multiparams, params, options):
                if getattr(clause, '_for_update_arg', None) is not None:
                    statements.append(clause.compile(dialect=dialect()))

            async def database():
                async with sessions() as session:
                    if fail_commit:
                        @event.listens_for(session.sync_session, 'before_commit')
                        def reject_commit(sync_session):
                            raise RuntimeError('CONTROLLED_COMMIT_FAILURE')
                    yield session

            async def authenticated():
                return policy.TrustedActor('alice', 'ROLE-001')

            def success(operation, snapshot):
                before = len(sql_count)
                if not isinstance(snapshot, dict):
                    raise AssertionError('Response adapter must receive a detached column snapshot')
                snapshots.append(deepcopy(snapshot))
                result = JSONResponse(jsonable_encoder({key: snapshot[key] for key in response_fields}))
                response_io.append(len(sql_count) - before)
                return result

            def failure(operation, error):
                return JSONResponse({'reason': error.reason}, status_code=404 if error.reason == 'NOT_FOUND' else 403)

            app, router = FastAPI(), APIRouter()
            service.install_routes(router, session_dependency=database, actor_dependency=authenticated,
                entity_models={'Ticket': Ticket}, success_response=success, failure_response=failure)
            app.include_router(router)
            async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url='http://example') as client:
                yield SimpleNamespace(client=client, sessions=sessions, entity=Ticket, service=service, policy=policy,
                    keys=keys, statements=statements, snapshots=snapshots, response_io=response_io, app=app)
        finally:
            await engine.dispose()
            sys.path.remove(directory)
            for name in list(sys.modules):
                if name == package or name.startswith(package + '.'):
                    del sys.modules[name]


@unittest.skipUnless(BEHAVIOR_READY, 'requires pending A-31 #147 and A-32 #148')
class EmitterContractTests(unittest.TestCase):
    def test_key_type_required_and_closed(self):
        from backend.app.agents.behavior.codegen.python.example import inputs
        from backend.app.agents.behavior.codegen.python.emitter import emit
        from backend.app.agents.behavior.model import ModelError
        from backend.app.agents.behavior.profile import behavior_profile
        for kind in (None, 'decimal', 'boolean', False, {'type': 'integer'}):
            with self.subTest(kind=kind):
                model, catalog, routes = inputs()
                if kind is None:
                    routes['cancelTicket'].pop('id_type')
                else:
                    routes['cancelTicket']['id_type'] = kind
                catalog['routes'] = deepcopy(routes)
                with self.assertRaisesRegex(ModelError, 'BEHAVIOR_ROUTE_UNSUPPORTED'):
                    emit(model, catalog=catalog, routes=routes, profile=behavior_profile())

    def test_deterministic_files_and_source_map(self):
        from backend.app.agents.behavior.codegen.python.emitter import MAP
        for kind in ('integer', 'string', 'uuid'):
            with self.subTest(kind=kind):
                files = emitted(kind)
                self.assertEqual(files, emitted(kind))
                source_map = json.loads(files[MAP])
                self.assertEqual(source_map['compiler_version'], 'python-1.1.1')
                for path, digest in source_map['files'].items():
                    self.assertEqual(sha256(files[path].encode()).hexdigest(), digest)
                    compile(files[path], path, 'exec')

    def test_default_profile_rejected_and_behavior_versions_supported(self):
        from backend.app.agents.behavior.model import ModelError
        from backend.app.agents.profiles import ProfileLoader, ProfileSource
        with self.assertRaisesRegex(ModelError, 'BEHAVIOR_PROFILE_MISMATCH'):
            emitted('integer', ProfileLoader().load('web-fastapi-angular'))
        for version in ('1.1.0', '1.2.0'):
            source = f'templates/profiles/web-fastapi-angular/{version}'
            profile = ProfileLoader(registry={('web-fastapi-angular', version): ProfileSource(
                source + '/profile.json', source + '/profile.schema.json')}).load('web-fastapi-angular', version=version)
            self.assertEqual(len(emitted('integer', profile)), 3)

    def test_all_frozen_behavior_limits_apply(self):
        from backend.app.agents.behavior.codegen.python.example import inputs
        from backend.app.agents.behavior.model import ModelError, canonical
        from backend.app.agents.behavior.profile import behavior_profile
        from backend.app.agents.profiles import ProfileLoader, ProfileSource
        loader = ProfileLoader(registry={('web-fastapi-angular', '1.1.0'): ProfileSource(
            'templates/profiles/web-fastapi-angular/1.1.0/profile.json',
            'templates/profiles/web-fastapi-angular/1.1.0/profile.schema.json')})
        size = len(canonical(inputs()[0]).encode())
        for key, value, code in (
            ('max_model_bytes', size - 1, 'BEHAVIOR_SIZE_LIMIT'),
            ('max_expression_depth', 1, 'BEHAVIOR_EXPRESSION_DEPTH'),
            ('max_rules', 2, 'BEHAVIOR_RULE_LIMIT'),
            ('max_domain_values', 2, 'BEHAVIOR_DOMAIN_LIMIT'),
        ):
            with self.subTest(limit=key):
                snapshot = behavior_profile().snapshot()
                data = json.loads(snapshot['profile_json'])
                data['limits']['behavior'][key] = value
                snapshot['profile_json'] = json.dumps(data)
                snapshot['checksum'] = sha256(snapshot['profile_json'].encode()).hexdigest()
                profile = loader.restore(snapshot, expected_checksum=snapshot['checksum'],
                                         expected_rules_checksum=snapshot['rules_checksum'])
                with self.assertRaisesRegex(ModelError, code):
                    emitted('integer', profile)


@unittest.skipUnless(BEHAVIOR_READY and ASYNC_READY, 'requires A-31/A-32, SQLAlchemy async, FastAPI and aiosqlite')
class GeneratedServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_expiry_and_native_primary_keys(self):
        from sqlalchemy import select
        for kind in ('integer', 'string', 'uuid'):
            for expire in (True, False):
                with self.subTest(kind=kind, expire_on_commit=expire):
                    async with application(kind, expire=expire) as case:
                        response = await case.client.post(f'/tickets/{case.keys[0]}/cancel', json={})
                        self.assertEqual(response.status_code, 200, response.text)
                        self.assertEqual(response.json()['status'], 'cancelled')
                        self.assertEqual(response.json()['revision'], 1)
                        self.assertEqual(case.snapshots[0]['note'], 'column value')
                        self.assertEqual(case.response_io, [0])
                        async with case.sessions() as session:
                            status = await session.scalar(select(case.entity.status).where(case.entity.id == case.keys[0]))
                        self.assertEqual(status, 'cancelled')
                        self.assertTrue(case.statements)
                        compiled = case.statements[0]
                        self.assertIn('FOR UPDATE', str(compiled))
                        self.assertIs(type(next(iter(compiled.params.values()))), type(case.keys[0]))
                        if kind == 'integer':
                            self.assertNotIn('::VARCHAR', str(compiled))
                        parameter = case.app.openapi()['paths']['/tickets/{record_id}/cancel']['post']['parameters'][0]
                        self.assertEqual(parameter['schema']['type'], 'integer' if kind == 'integer' else 'string')
                        if kind == 'uuid':
                            self.assertEqual(parameter['schema']['format'], 'uuid')

    async def test_invalid_keys_are_rejected_before_sql(self):
        for kind in ('integer', 'uuid'):
            with self.subTest(kind=kind):
                async with application(kind) as case:
                    response = await case.client.post('/tickets/not-a-valid-id/cancel', json={})
                    self.assertEqual(response.status_code, 422)
                    self.assertEqual(case.statements, [])
                    self.assertEqual(case.snapshots, [])

    async def test_commit_failure_never_returns_success_snapshot(self):
        from sqlalchemy import select
        async with application('integer', fail_commit=True) as case:
            response = await case.client.post('/tickets/1/cancel', json={})
            self.assertEqual(response.status_code, 500)
            self.assertEqual(case.snapshots, [])
            async with case.sessions() as session:
                status = await session.scalar(select(case.entity.status).where(case.entity.id == 1))
            self.assertEqual(status, 'draft')

    async def test_owner_and_state_denials_do_not_change_rows(self):
        from sqlalchemy import select
        async with application('integer') as case:
            for key in (2, 3):
                response = await case.client.post(f'/tickets/{key}/cancel', json={'actor_id': 'alice', 'owner_id': 'alice', 'status': 'draft'})
                self.assertEqual(response.status_code, 403)
            self.assertEqual(case.snapshots, [])
            async with case.sessions() as session:
                rows = (await session.execute(select(case.entity.id, case.entity.status, case.entity.revision))).all()
            self.assertEqual({row.id: (row.status, row.revision) for row in rows},
                             {1: ('draft', 0), 2: ('draft', 0), 3: ('cancelled', 0)})

    async def test_direct_caller_cannot_bypass_key_type(self):
        async with application('integer') as case:
            for key in ('1', True):
                async with case.sessions() as session:
                    with self.assertRaisesRegex(TypeError, 'RECORD_ID_TYPE_MISMATCH'):
                        await case.service.transition(session, {'Ticket': case.entity}, 'cancelTicket', key,
                                                      case.policy.TrustedActor('alice', 'ROLE-001'), {})
                    self.assertFalse(session.in_transaction())
            self.assertEqual(case.statements, [])

    async def test_mismatched_or_non_primary_binding_installs_no_route(self):
        from fastapi import APIRouter
        from sqlalchemy import Integer, String
        from sqlalchemy.orm import DeclarativeBase, mapped_column
        async with application('integer') as case:
            class Base(DeclarativeBase):
                pass

            class WrongType(Base):
                __tablename__ = 'wrong_type'
                id = mapped_column(String, primary_key=True)

            class WrongKey(Base):
                __tablename__ = 'wrong_key'
                actual_id = mapped_column(Integer, primary_key=True)
                id = mapped_column(Integer)

            class Composite(Base):
                __tablename__ = 'composite'
                id = mapped_column(Integer, primary_key=True)
                tenant_id = mapped_column(Integer, primary_key=True)

            for entity in (WrongType, WrongKey, Composite):
                with self.subTest(entity=entity.__name__):
                    router = APIRouter()
                    with self.assertRaises(TypeError):
                        case.service.install_routes(router, session_dependency=lambda: None, actor_dependency=lambda: None,
                            entity_models={'Ticket': entity}, success_response=lambda *args: None, failure_response=lambda *args: None)
                    self.assertEqual(router.routes, [])
