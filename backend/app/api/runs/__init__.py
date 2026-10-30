"""Configurable run read endpoints. Authentication is shared with project APIs."""
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Query, Request

from ..auth import current_user
from ..common import ContractRoute, error_response
from ...models.dto.auth import CurrentUser
from ...models.dto.errors import Error
from ...services.run_queries import (
    EventPage, InvalidReadInput, InvalidReadSnapshot, ReadNotFound,
    Run, RunPage, RunQueryService,
)


def get_run_query_service(request: Request) -> RunQueryService:
    return request.app.state.run_query_service


def create_runs_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["runs"], route_class=ContractRoute,
                       responses={status: {"model": Error} for status in (401, 404, 409, 422, 429, 500)})

    @router.get("/projects/{project_id}/runs", response_model=RunPage, operation_id="listRuns")
    def list_runs(
        project_id: UUID,
        limit: int = Query(default=100, ge=1, le=100),
        cursor: str | None = Query(default=None, min_length=1, max_length=2048),
        user: CurrentUser = Depends(current_user),
        service: RunQueryService = Depends(get_run_query_service),
    ) -> RunPage:
        return service.list_runs(owner_id=user.id, project_id=project_id, limit=limit, cursor=cursor)

    @router.get("/runs/{run_id}", response_model=Run, operation_id="getRun")
    def get_run(
        run_id: UUID,
        user: CurrentUser = Depends(current_user),
        service: RunQueryService = Depends(get_run_query_service),
    ) -> Run:
        return service.get_run(owner_id=user.id, run_id=run_id)

    @router.get("/runs/{run_id}/events", response_model=EventPage, operation_id="listEvents")
    def list_events(
        run_id: UUID,
        after_seq: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=100),
        user: CurrentUser = Depends(current_user),
        service: RunQueryService = Depends(get_run_query_service),
    ) -> EventPage:
        return service.list_events(owner_id=user.id, run_id=run_id, after_seq=after_seq, limit=limit)

    return router


def configure_runs(app: FastAPI, service: RunQueryService) -> None:
    """Call once at composition time after configure_auth, with an explicit adapter."""
    app.state.run_query_service = service
    app.include_router(create_runs_router())

    @app.exception_handler(ReadNotFound)
    async def not_found(request: Request, exc: ReadNotFound):
        return error_response(request, 404, "NOT_FOUND", "Resource not found")

    @app.exception_handler(InvalidReadInput)
    async def invalid_input(request: Request, exc: InvalidReadInput):
        return error_response(request, 422, "VALIDATION_ERROR", "Invalid run query")

    @app.exception_handler(InvalidReadSnapshot)
    async def invalid_snapshot(request: Request, exc: InvalidReadSnapshot):
        return error_response(request, 500, "INTERNAL_ERROR", "Invalid stored run snapshot")


__all__ = ["create_runs_router", "configure_runs", "get_run_query_service"]
