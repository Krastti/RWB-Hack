"""JSON login endpoint and Bearer current_user dependency."""
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..common import ContractRoute, error_response
from ...errors.auth import Unauthorized
from ...models.dto.auth import CurrentUser, Token, TokenRequest
from ...models.dto.errors import Error
from ...services.auth import AuthService


def get_auth_service(request: Request) -> AuthService:
    return request.app.state.auth_service


_bearer = HTTPBearer(auto_error=False)


def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    service: AuthService = Depends(get_auth_service),
) -> CurrentUser:
    if credentials is None:
        raise Unauthorized()
    return service.current_user(credentials.credentials)


router = APIRouter(prefix="/api/v1/auth", tags=["auth"], route_class=ContractRoute)


@router.post(
    "/token",
    response_model=Token,
    operation_id="issueToken",
    responses={
        401: {
            "model": Error,
            "description": "UNAUTHORIZED: invalid credentials or inactive user",
            "headers": {
                "WWW-Authenticate": {"schema": {"type": "string"}},
            },
        },
        422: {
            "model": Error,
            "description": "VALIDATION_ERROR: invalid login request",
        },
    },
)
def issue_token(
    body: TokenRequest, service: AuthService = Depends(get_auth_service),
) -> Token:
    return service.issue_token(login=body.login, password=body.password)


def configure_auth(app: FastAPI, service: AuthService) -> None:
    """Call once during application setup with a real repository-backed service."""
    app.state.auth_service = service
    app.include_router(router)

    @app.exception_handler(Unauthorized)
    async def unauthorized_handler(request: Request, exc: Unauthorized):
        return error_response(request, 401, "UNAUTHORIZED", "Authentication required")


__all__ = ["router", "current_user", "configure_auth", "get_auth_service"]
