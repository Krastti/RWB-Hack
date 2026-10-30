"""Read projections for C-01 v2.1.0; ORM entities are deliberately separate."""
from datetime import datetime, timezone
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, AfterValidator, model_serializer

from ...models.dto.errors import Error


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return value.astimezone(timezone.utc)


Timestamp = Annotated[datetime, AfterValidator(_utc)]
Count = Annotated[int, Field(ge=0, strict=True)]
Positive = Annotated[int, Field(ge=1, strict=True)]
RunStatus = Literal["queued", "running", "waiting_for_input", "completed", "cancelling", "cancelled", "failed", "budget_exceeded"]
AttemptStatus = Literal["pending", "running", "waiting_for_input", "succeeded", "failed", "cancelled", "skipped"]


class ReadDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_serializer(mode="wrap")
    def omit_absent_optional_fields(self, handler):
        values = handler(self)
        return {key: value for key, value in values.items()
                if value is not None or type(self).model_fields[key].is_required()}


class Limits(ReadDTO):
    max_calls: Positive
    max_output_tokens: Positive


class Metrics(ReadDTO):
    tokens_in: Count
    tokens_out: Count
    model_calls: Count
    attempts: Count
    manual_clarifications: Count
    duration_ms: Count


class Question(ReadDTO):
    question_id: UUID
    reason: str = Field(min_length=1)
    related_fr_ids: tuple[Annotated[str, Field(pattern=r"^FR-[0-9]{3,}$")], ...]
    required: bool
    question: str = Field(min_length=1)
    answer: str | None = Field(default=None, min_length=1, max_length=10000)


class StageAttempt(ReadDTO):
    stage_id: str = Field(min_length=1)
    attempt_no: Positive
    contract_version: str
    status: AttemptStatus
    started_at: Timestamp | None = None
    finished_at: Timestamp | None = None
    reason: str | None = None
    error: Error | None = None


class Run(ReadDTO):
    run_id: UUID
    project_id: UUID
    revision_id: UUID
    profile_id: Literal["web-fastapi-angular"]
    status: RunStatus
    current_stage: str | None = None
    limits: Limits
    created_at: Timestamp
    updated_at: Timestamp
    stages: tuple[StageAttempt, ...]
    questions_revision: Count
    questions: tuple[Question, ...]
    metrics: Metrics
    stop_reason: str | None = None


class RunPage(ReadDTO):
    items: tuple[Run, ...]
    next_cursor: str | None


class Event(ReadDTO):
    run_id: UUID
    seq: Positive
    event_type: str = Field(min_length=1)
    stage_id: str | None = None
    timestamp: Timestamp
    data: dict[str, Any]


class EventPage(ReadDTO):
    items: tuple[Event, ...]
    next_after_seq: Count
    has_more: bool
