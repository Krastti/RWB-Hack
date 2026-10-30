"""Opt-in C-01 routes; uses the existing real Bearer authentication dependency."""
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Header, Request

from ...api.auth import current_user
from ...api.common import ContractRoute, error_response
from ...models.dto.auth import CurrentUser
from ...models.dto.errors import Error
from .models import ControlError, Retry, Run
from .service import RunControlService


def get_service(request: Request) -> RunControlService:
    return request.app.state.run_control_service


router = APIRouter(prefix="/api/v1/runs", tags=["run-control"], route_class=ContractRoute,
                   responses={status: {"model": Error} for status in (401, 404, 409, 422, 429, 500)})


@router.post("/{run_id}/cancel", response_model=Run, response_model_exclude_none=True,
             operation_id="cancelRun")
def cancel_run(run_id: UUID, user: CurrentUser = Depends(current_user),
               service: RunControlService = Depends(get_service)) -> Run:
    return service.cancel(owner_id=user.id, run_id=run_id)


@router.post("/{run_id}/retry", response_model=Run, response_model_exclude_none=True,
             status_code=202, operation_id="retryRun")
def retry_run(run_id: UUID, body: Retry,
              key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128,
                                         pattern=r"^[A-Za-z0-9._:-]+$")],
              user: CurrentUser = Depends(current_user),
              service: RunControlService = Depends(get_service)) -> Run:
    return service.retry(owner_id=user.id, run_id=run_id, key=key, body=body)


def configure_run_control(app: FastAPI, service: RunControlService) -> None:
    """Call after configure_auth, with a shared transactional store and policy."""
    app.state.run_control_service = service
    app.include_router(router)

    @app.exception_handler(ControlError)
    async def control_error(request: Request, exc: ControlError):
        response = error_response(request, exc.status, exc.code, exc.message)
        if exc.status == 429:
            response.headers["Retry-After"] = str(exc.retry_after)
        return response
