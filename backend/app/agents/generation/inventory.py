"""Versioned, bounded web MVP inventory. Permissions remain owned by C-05."""
VERSION = 'web-mvp/1'
FRONTEND_TEMPLATE = (
    'frontend/package.json', 'frontend/package-lock.json', 'frontend/angular.json',
    'frontend/tsconfig.json', 'frontend/tsconfig.app.json', 'frontend/proxy.conf.json',
    'frontend/src/index.html', 'frontend/src/main.ts', 'frontend/src/styles.scss',
    'frontend/src/app/app.config.ts', 'frontend/THIRD_PARTY_NOTICES.md',
)
BACKEND_TEMPLATE = (
    'backend/pyproject.toml', 'backend/app/__init__.py', 'backend/app/core/config.py',
    'backend/app/db/session.py', 'backend/.env.example', 'backend/alembic.ini',
    'backend/app/core/security.py',
)
MODELS = ('backend/app/models/entities.py', 'backend/app/models/__init__.py')
FRONTEND = ('frontend/src/app/api/client.ts', 'frontend/src/app/screens.ts',
            'frontend/src/app/app.routes.ts', 'frontend/src/app/app.ts')
BACKEND = ('backend/app/schemas/dto.py', 'backend/app/services/domain.py',
           'backend/app/api/router.py', 'backend/app/main.py')
MIGRATIONS = ('backend/migrations/env.py', 'backend/migrations/versions/0001_initial.py',
              'backend/migrations/README.md')
INTEGRATIONS = ('backend/app/integrations/adapters.py', 'integrations/README.md')
DOCS = ('README.md', 'docs/product.md', 'docs/architecture.md', 'docs/data-model.md',
        'docs/user-guide.md', 'docs/development-guide.md', 'docs/limitations.md')
ASSEMBLY = ('.gitignore', 'traceability.json', 'manifest.json')


def tasks(has_integrations):
    result = [
        ('frontend-template', 'frontend', (), FRONTEND_TEMPLATE),
        ('backend-template', 'backend', (), BACKEND_TEMPLATE),
        ('models', 'backend', ('backend-template',), MODELS),
        ('migrations', 'migrations', ('models',), MIGRATIONS),
    ]
    if has_integrations:
        result.append(('integrations', 'integrations', ('models',), INTEGRATIONS))
    result += [
        ('backend', 'backend', ('models',) + (('integrations',) if has_integrations else ()), BACKEND),
        ('frontend', 'frontend', ('frontend-template', 'backend'), FRONTEND),
    ]
    result.append(('documentation', 'documentation', tuple(t[0] for t in result), DOCS))
    result.append(('assembly', 'assembly', tuple(t[0] for t in result), ASSEMBLY))
    return result


def make_tasks(requirements_id, fr_ids, has_integrations):
    return [dict(task_id=name, stage_id=name, owner_agent=owner, depends_on=list(deps),
                 input_artifact_ids=[requirements_id], output_paths=list(paths),
                 output_directories=[], related_fr_ids=list(fr_ids))
            for name, owner, deps, paths in tasks(has_integrations)]


def kind(path, profile):
    required = next((a['kind'] for a in profile['required_artifacts'] if a['path'] == path), None)
    return required or ('documentation' if path.endswith('.md') else
                        'configuration' if path.endswith(('.json', '.toml', '.ini')) else 'source')


def media_type(path):
    return ('application/json' if path.endswith('.json') else 'text/markdown' if path.endswith('.md')
            else 'text/x-python' if path.endswith('.py') else 'text/plain')
