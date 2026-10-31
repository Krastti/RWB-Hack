"""Offline data copy to a freshly migrated database; never rewrite Flyway history.

Stop all source/target writers first. URLs are read from environment, never logged.
The source stays intact for validation/rollback. See docs/legacy-clarifications.md.
"""
import os
from sqlalchemy import MetaData, create_engine, select, text


def copy_data(source, target):
    """Caller owns read-only source snapshot and destination transaction."""
    old, new = MetaData(), MetaData()
    old.reflect(bind=source)
    new.reflect(bind=target)
    names = set(old.tables) - {'flyway_schema_history'}
    if not names <= set(new.tables):
        raise ValueError('Source contains unsupported tables')
    for name in set(new.tables) - {'flyway_schema_history'}:
        if target.execute(select(new.tables[name]).limit(1)).first() is not None:
            raise ValueError('Destination must be empty')
    for table in new.sorted_tables:
        if table.name not in names:
            continue
        previous = old.tables[table.name]
        columns = list(previous.columns.keys())
        if not set(columns) <= set(table.columns.keys()):
            raise ValueError('Destination is missing source columns')
        keys = list(previous.primary_key.columns)
        if not keys:
            raise ValueError('Source table lacks a primary key')
        rows = source.execute(select(previous).order_by(*keys)).mappings().all()
        if rows:
            target.execute(table.insert(), [dict(row) for row in rows])
        copied = target.execute(select(*(table.c[c] for c in columns)).order_by(
            *(table.c[c.name] for c in keys))).mappings().all()
        if [dict(row) for row in rows] != [dict(row) for row in copied]:
            raise ValueError('Destination verification failed')
    return len(names)


def main():
    source_engine = create_engine(os.environ['SOURCE_DATABASE_URL'])
    target_engine = create_engine(os.environ['TARGET_DATABASE_URL'])
    try:
        with source_engine.connect().execution_options(isolation_level='REPEATABLE READ') as source:
            with source.begin():
                source.execute(text('SET TRANSACTION READ ONLY'))
                with target_engine.begin() as target:
                    identity = text('SELECT current_database(), current_schema(), inet_server_addr(), inet_server_port()')
                    if source.execute(identity).one() == target.execute(identity).one():
                        raise ValueError('Source and destination must be distinct')
                    source_history = source.execute(text('SELECT version, script, success FROM flyway_schema_history ORDER BY installed_rank')).all()
                    if not source_history or source_history[-1] != ('7', 'V7__clarifications.sql', True):
                        raise ValueError('Expected legacy clarifications V7 history')
                    target_history = target.execute(text('SELECT version, script, success FROM flyway_schema_history ORDER BY installed_rank')).all()
                    if not target_history or target_history[-1] != ('9', 'V9__clarifications.sql', True):
                        raise ValueError('Expected freshly migrated V9 destination')
                    copy_data(source, target)
        print('Data copy and row verification completed; source retained. Validate Flyway and API before cutover.')
    finally:
        source_engine.dispose()
        target_engine.dispose()


if __name__ == '__main__':
    main()
