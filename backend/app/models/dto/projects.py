"""Immutable project DTOs for platform contract C-01 v1.0.0."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class RequirementRevision:
    id: UUID
    number: int
    requirements_text: str
    hash: str


@dataclass(frozen=True)
class Project:
    id: UUID
    owner_id: UUID
    name: str
    created_at: datetime
    updated_at: datetime
    current_revision: RequirementRevision


@dataclass(frozen=True)
class ProjectPage:
    items: tuple[Project, ...]
    next_cursor: str | None
