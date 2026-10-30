"""HTTP-интерфейс создания запуска с сохранением исходного ответа."""
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Header, Request
from fastapi.responses import JSONResponse

from ..auth import current_user
from ..common import ContractRoute, error_response
from ...models.dto.auth import CurrentUser
from ...models.dto.errors import Error
from ...services.run_queries import Run
from ...services.runs import RunCreate, RunCreationError, RunCreationService


def get_run_creation_service(request: Request) -> RunCreationService:
    return request.app.state.run_creation_service


def create_run_creation_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["runs"], route_class=ContractRoute)

    @router.post(
        "/projects/{project_id}/runs", operation_id="createRun", status_code=202,
        response_model=Run,
        responses={status: {"model": Error} for status in (401, 404, 409, 422, 429, 500)},
    )
    def create_run(
        project_id: UUID,
        body: RunCreate,
        idempotency_key: str = Header(
            alias="Idempotency-Key", min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$",
        ),
        user: CurrentUser = Depends(current_user),
        service: RunCreationService = Depends(get_run_creation_service),
    ) -> JSONResponse:
        response = service.create_run(
            owner_id=user.id, project_id=project_id, idempotency_key=idempotency_key, body=body,
        )
        return JSONResponse(status_code=response.status_code, content=response.body)

    return router


def configure_run_creation(app: FastAPI, service: RunCreationService) -> None:
    """Подключить POST независимо от настройки существующих GET-эндпоинтов."""
    app.state.run_creation_service = service
    app.include_router(create_run_creation_router())

    @app.exception_handler(RunCreationError)
    async def creation_error(request: Request, exc: RunCreationError):
        return error_response(request, exc.status_code, exc.code, exc.message)
