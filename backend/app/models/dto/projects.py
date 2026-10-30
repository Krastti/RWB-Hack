"""Immutable project DTOs for platform contract C-01 v2.1.0."""

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


@dataclass(frozen=True)
class RequirementRevision:
    __pydantic_config__ = ConfigDict(extra="forbid")

    id: UUID
    number: Annotated[int, Field(ge=1)]
    requirements_text: Annotated[str, Field(min_length=1, max_length=100000)]
    hash: Annotated[str, Field(pattern="^[a-f0-9]{64}$")]


@dataclass(frozen=True)
class Project:
    __pydantic_config__ = ConfigDict(extra="forbid")

    id: UUID
    owner_id: UUID
    name: Annotated[str, Field(min_length=1, max_length=120)]
    created_at: datetime
    updated_at: datetime
    current_revision: RequirementRevision
    latest_run_status: Literal["queued", "running", "waiting_for_input", "completed", "cancelling", "cancelled", "failed", "budget_exceeded"] | None = None


@dataclass(frozen=True)
class ProjectPage:
    __pydantic_config__ = ConfigDict(extra="forbid")

    items: tuple[Project, ...]
    next_cursor: str | None


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=120)
    requirements_text: str = Field(min_length=1, max_length=100000)

    @field_validator("requirements_text")
    @classmethod
    def meaningful_requirements(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("requirements_text must contain a non-whitespace character")
        return value
