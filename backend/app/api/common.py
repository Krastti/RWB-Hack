"""Shared C-01 error responses without echoing request input."""
from uuid import uuid4

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute


def error_response(request: Request, status: int, code: str, message: str) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None) or str(uuid4())
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message,
                           "details": {}, "request_id": str(request_id)}},
        headers={"WWW-Authenticate": "Bearer"} if status == 401 else None,
    )


class ContractRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def wrapped(request: Request):
            try:
                return await handler(request)
            except RequestValidationError:
                return error_response(request, 422, "VALIDATION_ERROR", "Invalid request")
        return wrapped
