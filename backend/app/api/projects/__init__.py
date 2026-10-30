"""Owner-scoped project HTTP endpoints, C-01 v2.0.0."""
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Query, Request

from ..auth import current_user
from ..common import ContractRoute, error_response
from ...errors.projects import InvalidProjectInput, ProjectNotFound
from ...models.dto import Project, ProjectCreate, ProjectPage
from ...models.dto.auth import CurrentUser
from ...models.dto.errors import Error
from ...services.projects import ProjectService


def get_project_service(request: Request) -> ProjectService:
    return request.app.state.project_service


router = APIRouter(
    prefix="/api/v1/projects", tags=["projects"], route_class=ContractRoute,
    responses={
        401: {"model": Error, "description": "UNAUTHORIZED"},
        404: {"model": Error, "description": "NOT_FOUND"},
        409: {
            "model": Error,
            "description": "Reserved by C-01; project conflict handling is deferred",
        },
        422: {"model": Error, "description": "VALIDATION_ERROR"},
        429: {
            "model": Error,
            "description": "RATE_LIMIT_EXCEEDED; rate limiting is deferred",
            "headers": {
                "Retry-After": {"schema": {"type": "integer", "minimum": 1}},
            },
        },
        500: {"model": Error, "description": "INTERNAL_ERROR"},
    },
)


@router.post("", response_model=Project, status_code=201, operation_id="createProject")
def create_project(
    body: ProjectCreate,
    user: CurrentUser = Depends(current_user),
    service: ProjectService = Depends(get_project_service),
) -> Project:
    return service.create_project(owner_id=user.id, name=body.name, requirements_text=body.requirements_text)


@router.get("", response_model=ProjectPage, operation_id="listProjects")
def list_projects(
    limit: int = Query(default=100, ge=1, le=100),
    cursor: str | None = Query(default=None, min_length=1),
    user: CurrentUser = Depends(current_user),
    service: ProjectService = Depends(get_project_service),
) -> ProjectPage:
    return service.list_projects(owner_id=user.id, limit=limit, cursor=cursor)


@router.get("/{project_id}", response_model=Project, operation_id="getProject")
def get_project(
    project_id: UUID,
    user: CurrentUser = Depends(current_user),
    service: ProjectService = Depends(get_project_service),
) -> Project:
    return service.get_project(owner_id=user.id, project_id=project_id)


def configure_projects(app: FastAPI, service: ProjectService) -> None:
    app.state.project_service = service
    app.include_router(router)

    @app.exception_handler(InvalidProjectInput)
    async def invalid_input_handler(request: Request, exc: InvalidProjectInput):
        return error_response(request, 422, "VALIDATION_ERROR", "Invalid project request")

    @app.exception_handler(ProjectNotFound)
    async def not_found_handler(request: Request, exc: ProjectNotFound):
        return error_response(request, 404, "NOT_FOUND", "Project not found")


__all__ = ["router", "configure_projects", "get_project_service"]
