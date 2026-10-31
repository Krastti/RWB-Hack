from pathlib import Path
from uuid import uuid4
import importlib.util
from sqlalchemy import text
from test_runtime_recovery_postgresql import db
from test_answers_concurrency import waiting
from app.api.answers.service import AnswersService


def test_copy_legacy_data_preserves_answers_and_runtime_defaults(db):
    rt, run_id, owner, body = waiting(db)
    AnswersService(rt).accept(owner_id=owner,run_id=run_id,key='legacy',body=body)
    module_path=Path(__file__).resolve().parents[1]/'tools/migrate_legacy_clarifications.py'
    spec=importlib.util.spec_from_file_location('legacy_copy',module_path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    engine=db.kw['bind']
    legacy='legacy_'+uuid4().hex
    migrated='target_'+uuid4().hex
    with engine.begin() as c:
        source_schema=c.execute(text('SELECT current_schema()')).scalar_one()
        c.execute(text(f'CREATE SCHEMA "{legacy}"'))
        c.execute(text(f'CREATE SCHEMA "{migrated}"'))
        paths=sorted((module_path.parents[1]/'migrations').glob('V*__*.sql'))
        c.exec_driver_sql(f'SET LOCAL search_path TO "{legacy}"')
        for p in paths:
            if int(p.name.split('__')[0][1:]) in [1,2,3,4,5,6,9]:
                c.exec_driver_sql(p.read_text())
        c.exec_driver_sql(f'SET LOCAL search_path TO "{migrated}"')
        for p in paths:c.exec_driver_sql(p.read_text())
        c.exec_driver_sql(f'SET LOCAL search_path TO "{source_schema}"')
        from sqlalchemy import MetaData
        metadata=MetaData();metadata.reflect(bind=c)
        for table in metadata.sorted_tables:
            if table.name in ('flyway_schema_history',):continue
            columns=[col.name for col in table.columns if col.name not in ('recovery_failures','recovery_error','failures','next_attempt_at','failure_code') and not (table.name=='run_queue_jobs' and col.name=='state')]
            names=', '.join('"'+name+'"' for name in columns)
            c.exec_driver_sql(f'INSERT INTO "{legacy}"."{table.name}" ({names}) SELECT {names} FROM "{source_schema}"."{table.name}"')
    try:
        with engine.connect() as src, engine.begin() as dst:
            src.exec_driver_sql(f'SET search_path TO "{legacy}"')
            dst.exec_driver_sql(f'SET LOCAL search_path TO "{migrated}"')
            assert module.copy_data(src,dst)>0
            assert dst.execute(text('SELECT answer FROM clarifications')).scalar_one()=='yes'
            assert dst.execute(text('SELECT count(*) FROM answer_replays')).scalar_one()==1
    finally:
        with engine.begin() as c:
            c.exec_driver_sql(f'DROP SCHEMA "{legacy}" CASCADE')
            c.exec_driver_sql(f'DROP SCHEMA "{migrated}" CASCADE')
