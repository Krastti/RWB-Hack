"""C-01 listArtifacts/getArtifact/downloadArchive; server-side owner checks."""
from uuid import UUID
from urllib.parse import quote
from fastapi import APIRouter, Depends, Query, Response
from .dto import ManifestPage, AcceptedArtifacts
from ..auth import current_user
from ..common import ContractRoute, error_response
from ...models.dto.auth import CurrentUser
from ...services.artifacts.snapshots import ArtifactReadService, SnapshotReadError
from ...services.artifacts import ArtifactError


def configure_artifacts(app, sessions):
    service = ArtifactReadService(sessions)
    router = APIRouter(prefix='/api/v1/runs', tags=['artifacts'], route_class=ContractRoute)

    @router.get('/{run_id}/accepted-artifacts', operation_id='listAcceptedArtifacts', response_model=AcceptedArtifacts)
    def accepted_artifacts(run_id: UUID, user: CurrentUser = Depends(current_user)):
        return service.accepted(owner_id=user.id, run_id=run_id)

    @router.get('/{run_id}/artifacts', operation_id='listArtifacts', response_model=ManifestPage)
    def list_artifacts(run_id: UUID, limit: int = Query(100, ge=1, le=100),
                       cursor: str | None = Query(None, min_length=1, max_length=2048),
                       user: CurrentUser = Depends(current_user)):
        return service.list(owner_id=user.id, run_id=run_id, limit=limit, cursor=cursor)

    @router.get('/{run_id}/artifacts/{artifact_id}', operation_id='getArtifact')
    def get_artifact(run_id: UUID, artifact_id: UUID, user: CurrentUser = Depends(current_user)):
        meta, raw = service.get(owner_id=user.id, run_id=run_id, artifact_id=artifact_id)
        return Response(raw, media_type=meta['media_type'], headers={
            'Content-Disposition': "attachment; filename*=UTF-8''" + quote(meta['path'].split('/')[-1], safe=''),
            'X-Content-Type-Options': 'nosniff'})

    @router.get('/{run_id}/archive', operation_id='downloadArchive')
    def archive(run_id: UUID, snapshot_id: UUID, user: CurrentUser = Depends(current_user)):
        return Response(service.archive(owner_id=user.id, run_id=run_id, snapshot_id=snapshot_id),
                        media_type='application/zip', headers={'Content-Disposition': 'attachment; filename="project.zip"'})

    @app.exception_handler(SnapshotReadError)
    async def read_error(request, exc):
        return error_response(request, exc.status, exc.code, 'Artifact request could not be completed')

    @app.exception_handler(ArtifactError)
    async def integrity_error(request, exc):
        return error_response(request, 500, 'INTERNAL_ERROR', 'Artifact integrity check failed')

    app.include_router(router)
