"""Closed C-01 read DTOs; artifact content is returned as bytes separately."""
from uuid import UUID
from typing import Annotated
from pydantic import BaseModel, ConfigDict, Field


class Artifact(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    artifact_id: UUID
    attempt_id: UUID
    path: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    media_type: str = Field(min_length=1)
    version: int = Field(ge=1)
    schema_version: str
    author_agent: str
    checksum: str = Field(pattern=r'^[a-f0-9]{64}$')
    size_bytes: int = Field(ge=0)
    related_fr_ids: list[Annotated[str, Field(pattern=r'^FR-[0-9]{3,}$')]]


class ManifestPage(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    snapshot_id: UUID
    items: list[Artifact]
    next_cursor: str | None


class AcceptedArtifacts(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    items: list[Artifact]
