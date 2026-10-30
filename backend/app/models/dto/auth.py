"""Authentication DTOs for C-01 v1.0.0."""
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


@dataclass(frozen=True)
class UserCredentials:
    id: UUID
    login: str
    password_hash: str = field(repr=False)
    status: str = "active"


@dataclass(frozen=True)
class CurrentUser:
    id: UUID
    login: str


class TokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    login: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=1024, repr=False, json_schema_extra={"writeOnly": True})


class Token(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    access_token: str = Field(repr=False)
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(ge=1)
