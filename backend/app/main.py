"""Application factory: PostgreSQL repositories → services → HTTP endpoints."""
from contextlib import asynccontextmanager
import os

from fastapi import FastAPI, Request
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from starlette.exceptions import HTTPException

from .api.auth import configure_auth
from .api.common import error_response
from .api.projects import configure_projects
from .api.runs import configure_run_creation, configure_runs
from .config.auth import AuthConfig
from .config.runs import RunCreationConfig, load_run_profile
from .repositories.postgresql import PostgreSQLProjectRepository, PostgreSQLUserRepository
from .services.auth import AuthService
from .services.projects import ProjectService
from .services.run_queries import RunQueryService
from .services.runs import RunCreationService
from .services.runs.postgresql import PostgreSQLRunCreationRepository, PostgreSQLRunReadRepository
from .services.runs.queue import PostgreSQLRunQueue
from .services.run_control.api import configure_run_control
from .services.run_control.models import Limits as ControlLimits
from .services.lifecycle.models import LifecycleError
from .services.runtime import compose_runtime
from .api.artifacts import configure_artifacts
from .api.answers import configure_answers
from .api.answers.service import AnswersService


def create_app(
    *, database_url: str | None = None, auth_config: AuthConfig | None = None,
    run_config: RunCreationConfig | None = None,
    retry_limits: ControlLimits | None = None,
) -> FastAPI:
    database_url = database_url if database_url is not None else os.environ["DATABASE_URL"]
    auth_config = auth_config if auth_config is not None else AuthConfig(secret_key=os.environ["JWT_SECRET_KEY"])
    run_profile = load_run_profile()
    run_config = run_config if run_config is not None else RunCreationConfig.from_env(profile=run_profile)
    url = make_url(database_url)
    if url.get_backend_name() != "postgresql":
        raise ValueError("DATABASE_URL must use PostgreSQL")
    engine = create_engine(url.set(drivername="postgresql+psycopg"), pool_pre_ping=True, hide_parameters=True)
    sessions = sessionmaker(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(title="RWB-Hack Platform API", version="2.1.0", lifespan=lifespan)
    app.state.engine = engine
    app.state.run_runtime = compose_runtime(
        sessions, retry_limits=retry_limits or ControlLimits.model_validate(run_config.limits.model_dump()),
    )
    configure_auth(app, AuthService(PostgreSQLUserRepository(sessions), auth_config))
    configure_projects(app, ProjectService(PostgreSQLProjectRepository(sessions, cursor_secret=auth_config.secret_key)))
    configure_runs(app, RunQueryService(
        PostgreSQLRunReadRepository(sessions), cursor_secret=auth_config.secret_key,
    ))
    configure_run_creation(app, RunCreationService(
        PostgreSQLRunCreationRepository(sessions, PostgreSQLRunQueue()),
        limits=run_config.limits, profile=run_profile,
    ))
    configure_run_control(app, app.state.run_runtime.control)
    configure_answers(app, AnswersService(app.state.run_runtime))
    configure_artifacts(app, sessions)

    @app.exception_handler(LifecycleError)
    async def lifecycle_error_handler(request: Request, exc: LifecycleError):
        return error_response(request, exc.status, exc.code, exc.message)

    @app.exception_handler(404)
    @app.exception_handler(405)
    async def routing_error_handler(request: Request, exc: HTTPException):
        code, message = (
            ("NOT_FOUND", "Resource not found") if exc.status_code == 404
            else ("METHOD_NOT_ALLOWED", "Method not allowed")
        )
        response = error_response(request, exc.status_code, code, message)
        response.headers.update(exc.headers or {})
        return response

    @app.exception_handler(SQLAlchemyError)
    async def storage_error_handler(request: Request, exc: SQLAlchemyError):
        return error_response(request, 500, "INTERNAL_ERROR", "Storage operation failed")

    return app
