"""Common error envelope for platform contract C-01 v1.0.0."""
from typing import Any

from pydantic import BaseModel, ConfigDict


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict[str, Any]
    request_id: str


class Error(BaseModel):
    model_config = ConfigDict(extra="forbid")
    error: ErrorDetail
