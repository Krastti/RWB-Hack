"""Trusted scaffold and deterministic PostgreSQL model rendering; never runs generated code."""
from pathlib import Path
from .inventory import VERSION, FRONTEND_TEMPLATE, BACKEND_TEMPLATE

ROOT = Path(__file__).resolve().parents[4] / 'templates' / 'web-mvp' / '1'


def template_files(stage):
    paths = FRONTEND_TEMPLATE if stage == 'frontend-template' else BACKEND_TEMPLATE
    return {path: (ROOT / path).read_text() for path in paths}


def model_source(model, *, migration=False):
    """Compile the closed A-07 data types/constraints, including composite keys.

    A migration embeds its own metadata so future ORM edits cannot change history.
    SQLAlchemy escapes identifiers and literal values when it compiles this schema.
    """
    if model['unresolved_items']:
        raise ValueError('GENERATION_UNRESOLVED_DATA_MODEL')
    types = {'uuid': 'sa.Uuid()', 'text': 'sa.Text()', 'integer': 'sa.Integer()',
             'bigint': 'sa.BigInteger()', 'boolean': 'sa.Boolean()', 'date': 'sa.Date()',
             'timestamptz': 'sa.DateTime(timezone=True)', 'jsonb': 'JSONB()'}
    source = ['"""Generated from accepted data-model.json; schema version ' + VERSION + '."""',
              'import uuid', 'import sqlalchemy as sa', 'from sqlalchemy.dialects.postgresql import JSONB',
              'from sqlalchemy.orm import registry', 'mapper_registry = registry()',
              'metadata = mapper_registry.metadata', '']
    names = set()
    for entity in model['entities']:
        name = entity['name']
        class_name = ''.join(p.capitalize() for p in name.split('_'))
        if class_name in names:
            raise ValueError('GENERATION_MODEL_NAME_COLLISION')
        names.add(class_name)
        source.append(f'{name}_table = sa.Table({name!r}, metadata,')
        for field in entity['fields']:
            type_name = (f"sa.String({field['max_length']})" if field['type'] == 'varchar' else
                         f"sa.Numeric({field['precision']}, {field['scale']})" if field['type'] == 'numeric' else types[field['type']])
            default = field['default']
            extra = ''
            if default['kind'] == 'application_uuid4':
                extra = ', default=uuid.uuid4'
            elif default['kind'] == 'server_now':
                extra = ', server_default=sa.func.now()'
            elif default['kind'] == 'literal':
                extra = f", default={default['value']!r}, server_default=sa.literal({default['value']!r})"
            source.append(f"    sa.Column({field['name']!r}, {type_name}, nullable={field['nullable']!r}{extra}),")
        key = entity['primary_key']
        source.append(f"    sa.PrimaryKeyConstraint(*{key['fields']!r}, name={key['name']!r}),")
        for key in entity['unique_constraints']:
            source.append(f"    sa.UniqueConstraint(*{key['fields']!r}, name={key['name']!r}),")
        for check in entity['check_constraints']:
            col, op, value = f"sa.column({check['field']!r})", check['operator'], repr(check['value'])
            operators = {'eq': '==', 'ne': '!=', 'lt': '<', 'lte': '<=', 'gt': '>', 'gte': '>='}
            expression = (f'{col} {operators[op]} {value}' if op in operators else
                          f'{col}.in_({value})' if op == 'in' else f'{col}.not_in({value})' if op == 'not_in' else
                          f'{col}.is_(None)' if op == 'is_null' else f'{col}.is_not(None)')
            source.append(f"    sa.CheckConstraint({expression}, name={check['name']!r}),")
        for relationship in model['relationships']:
            if relationship['source_entity'] == name:
                targets = [relationship['target_entity'] + '.' + f for f in relationship['target_fields']]
                source.append(f"    sa.ForeignKeyConstraint({relationship['source_fields']!r}, {targets!r}, "
                              f"name={relationship['name']!r}, ondelete={relationship['on_delete'].replace('_', ' ').upper()!r}, "
                              f"match={relationship['match'].upper()!r}),")
        source.append(')')
        for index in entity['indexes']:
            columns = ', '.join(f"{name}_table.c[{f!r}]" for f in index['fields'])
            source.append(f"sa.Index({index['name']!r}, {columns}, unique={index['unique']!r}, postgresql_using='btree')")
        source += [f'class {class_name}:', '    pass', f'mapper_registry.map_imperatively({class_name}, {name}_table)', '']
    if migration:
        source += ["from alembic import op", "revision = '0001'", 'down_revision = None',
                   'branch_labels = None', 'depends_on = None', '', 'def upgrade():',
                   '    metadata.create_all(bind=op.get_bind(), checkfirst=False)', '', 'def downgrade():',
                   '    metadata.drop_all(bind=op.get_bind(), checkfirst=False)', '']
    return '\n'.join(source)


def model_files(model):
    return {'backend/app/models/entities.py': model_source(model),
            'backend/app/models/__init__.py': 'from .entities import metadata, mapper_registry\n'}


def migration_files(model):
    return {
        'backend/migrations/env.py': (ROOT / 'backend/migrations/env.py').read_text(),
        'backend/migrations/versions/0001_initial.py': model_source(model, migration=True),
        'backend/migrations/README.md': '# Database migrations\n\nFrom backend/: `alembic upgrade head`. '
        'Set DATABASE_URL first. The initial revision embeds frozen metadata; future model changes require a new revision. '
        '`alembic downgrade base` deletes the generated tables and their data; use only on a disposable database.\n',
    }
