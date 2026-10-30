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
from .config.auth import AuthConfig
from .repositories.postgresql import PostgreSQLProjectRepository, PostgreSQLUserRepository
from .services.auth import AuthService
from .services.projects import ProjectService


def create_app(
    *, database_url: str | None = None, auth_config: AuthConfig | None = None,
) -> FastAPI:
    database_url = database_url if database_url is not None else os.environ["DATABASE_URL"]
    auth_config = auth_config if auth_config is not None else AuthConfig(secret_key=os.environ["JWT_SECRET_KEY"])
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

    app = FastAPI(title="RWB-Hack Platform API", version="1.0.0", lifespan=lifespan)
    app.state.engine = engine
    configure_auth(app, AuthService(PostgreSQLUserRepository(sessions), auth_config))
    configure_projects(app, ProjectService(PostgreSQLProjectRepository(sessions, cursor_secret=auth_config.secret_key)))

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
