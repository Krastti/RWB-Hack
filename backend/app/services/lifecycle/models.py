"""Private lifecycle state and C-01 Event DTOs; C-03 payload version 1.0."""
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_FOR_INPUT = "waiting_for_input"
    CANCELLING = "cancelling"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    BUDGET_EXCEEDED = "budget_exceeded"


class StageStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_FOR_INPUT = "waiting_for_input"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Event(Model):
    run_id: UUID
    seq: int = Field(ge=1, strict=True)
    event_type: str = Field(min_length=1)
    timestamp: datetime
    stage_id: str | None = None
    data: dict[str, Any]


class EventPage(Model):
    items: list[Event]
    next_after_seq: int = Field(ge=0, strict=True)
    has_more: bool


class Attempt(Model):
    """Internal record. attempt_id/input/fencing fields do not extend C-01 DTOs."""
    attempt_id: UUID = Field(default_factory=uuid4)
    stage_id: str = Field(min_length=1)
    attempt_no: int = Field(ge=1, strict=True)
    contract_version: str = Field(min_length=1)
    input_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: StageStatus = StageStatus.PENDING
    generation: int = Field(default=0, ge=0, strict=True)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    reason_code: str | None = None


class RunRecord(Model):
    """Repository aggregate. context is immutable; data belongs to domain modules.

    data holds artifact references, counters, restoration/outbox/idempotency
    records. It must not contain provider calls performed inside a transaction.
    Event history is append-only, without retention/renumbering in C-03 v1.0.
    """
    run_id: UUID
    owner_id: UUID
    status: RunStatus = RunStatus.QUEUED
    version: int = Field(default=1, ge=1, strict=True)
    last_seq: int = Field(default=0, ge=0, strict=True)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    stopped_at: datetime | None = None
    context: dict[str, Any]
    data: dict[str, Any] = Field(default_factory=dict)
    attempts: list[Attempt] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)


class LifecycleError(Exception):
    """Caller maps status/code/message into the existing C-01 Error envelope."""
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def conflict(message: str = "Run state conflicts with this operation") -> LifecycleError:
    return LifecycleError(409, "RUN_STATE_CONFLICT", message)
