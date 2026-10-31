from alembic import context
from app.db.session import engine
from app.models import metadata
if context.is_offline_mode():
    from app.core.config import settings
    context.configure(url=settings.database_url, target_metadata=metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=metadata)
        with context.begin_transaction():
            context.run_migrations()
