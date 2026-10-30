"""Вход создания запуска и сохранённый HTTP-ответ по C-01 2.1.0."""
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class RunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    requirements_text: str = Field(min_length=1, max_length=100000)
    profile_id: Literal["web-fastapi-angular"] = "web-fastapi-angular"


@dataclass(frozen=True)
class CreationResponse:
    status_code: int
    body: dict[str, Any]


class RunCreationError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
