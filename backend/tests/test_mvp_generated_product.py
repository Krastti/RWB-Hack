"""Execute only trusted controlled-provider fixtures in a disposable database."""
import json
import os
from pathlib import Path
import subprocess
import sys
from sqlalchemy import text
from test_runtime_recovery_postgresql import db
from mvp_source_fixture import FILES
from app.agents.generation.scaffold import template_files, model_files, migration_files


def test_generated_postgresql_upgrade_api_permissions_and_downgrade(db, tmp_path):
    model = json.loads((Path(__file__).resolve().parents[1] / 'app/agents/generators/data_model/examples/data-model.json').read_text())
    files = template_files('backend-template') | model_files(model) | migration_files(model) | FILES
    for name, content in files.items():
        if name.startswith('backend/'):
            path = tmp_path / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
    with db() as session:
        schema = session.scalar(text('select current_schema()'))
    env = os.environ.copy()
    env.update(PYTHONPATH=str(tmp_path / 'backend'), JWT_SECRET='controlled-test-only-' * 4,
        DATABASE_URL=db.kw['bind'].url.update_query_dict({'options':'-csearch_path='+schema+',public'}).render_as_string(hide_password=False))
    def run(*args):
        result = subprocess.run([sys.executable, '-B', *args], cwd=tmp_path/'backend', env=env, capture_output=True, text=True)
        assert result.returncode == 0, (result.stdout + result.stderr).replace(env['DATABASE_URL'],'<test-db>')
    run('-m','alembic','upgrade','head')
    run('-c', '''
import jwt, os
from uuid import uuid4
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from app.main import app
first, second, dispatcher = [str(uuid4()) for _ in range(3)]
def headers(subject, role):
    token = jwt.encode(dict(sub=subject, roles=[role], iss='generated-project', aud='generated-project',
        exp=datetime.now(timezone.utc)+timedelta(minutes=5)), os.environ['JWT_SECRET'], algorithm='HS256')
    return {'Authorization':'Bearer '+token}
with TestClient(app) as client:
    assert client.get('/api/v1/tickets').status_code == 401
    bad={'Authorization':'Bearer '+jwt.encode(dict(sub=first,roles=['ROLE-002']), 'wrong',algorithm='HS256')}
    assert client.get('/api/v1/dispatch/tickets',headers=bad).status_code == 401
    owner=headers(first,'ROLE-001'); other=headers(second,'ROLE-001'); admin=headers(dispatcher,'ROLE-002')
    response=client.post('/api/v1/tickets',headers=owner,json={'description':'Printer broken'})
    assert response.status_code == 201, response.text
    ticket=response.json(); assert ticket['owner_id'] == first
    assert client.post('/api/v1/tickets',headers=owner,json={'description':' ', 'owner_id':second}).status_code == 422
    assert len(client.get('/api/v1/tickets',headers=owner).json()) == 1
    assert client.get('/api/v1/tickets',headers=other).json() == []
    assert client.get('/api/v1/dispatch/tickets',headers=owner).status_code == 403
    path='/api/v1/tickets/'+ticket['id']+'/status'
    assert client.patch(path,headers=owner,json={'status':'done'}).status_code == 403
    assert client.patch(path,headers=admin,json={'status':'done'}).status_code == 200
    assert client.get('/api/v1/tickets',headers=owner).json()[0]['status'] == 'done'
    assert client.delete('/api/v1/tickets/'+ticket['id'],headers=admin).status_code in (404,405)
    assert client.patch('/api/v1/tickets/'+str(uuid4())+'/status',headers=admin,json={'status':'done'}).status_code == 404
''')
    run('-m','alembic','downgrade','base')
    with db() as session:
        assert session.scalar(text("SELECT to_regclass('tickets')")) is None
    run('-m','alembic','upgrade','head')
    with db() as session:
        assert session.scalar(text('SELECT count(*) FROM tickets')) == 0
