"""JSON login endpoint and Bearer current_user dependency."""
from uuid import uuid4

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ...errors.auth import Unauthorized
from ...models.dto.auth import CurrentUser, Token, TokenRequest
from ...models.dto.errors import Error
from ...services.auth import AuthService


def _error(request: Request, status: int, code: str, message: str) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None) or str(uuid4())
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message,
                           "details": {}, "request_id": str(request_id)}},
        headers={"WWW-Authenticate": "Bearer"} if status == 401 else None,
    )


class _AuthRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def wrapped(request: Request):
            try:
                return await handler(request)
            except RequestValidationError:
                # Never echo validation input: it can contain the password.
                return _error(request, 422, "VALIDATION_ERROR", "Invalid login request")
        return wrapped


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


router = APIRouter(prefix="/api/v1/auth", tags=["auth"], route_class=_AuthRoute)


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
        return _error(request, 401, "UNAUTHORIZED", "Authentication required")


__all__ = ["router", "current_user", "configure_auth", "get_auth_service"]
