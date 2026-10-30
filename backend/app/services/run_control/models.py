"""C-01 v2.1.0 DTOs and private C-03 v1.0 control state."""
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


def now() -> datetime:
    return datetime.now(timezone.utc)


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Limits(Model):
    max_calls: int = Field(ge=1, strict=True)
    max_output_tokens: int = Field(ge=1, strict=True)


class Retry(Model):
    limits: Limits | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_null(cls, value):
        if isinstance(value, dict) and "limits" in value and value["limits"] is None:
            raise ValueError("limits must be an object when supplied")
        return value


class Metrics(Model):
    tokens_in: int = Field(default=0, ge=0)
    tokens_out: int = Field(default=0, ge=0)
    model_calls: int = Field(default=0, ge=0)
    attempts: int = Field(default=0, ge=0)
    manual_clarifications: int = Field(default=0, ge=0)
    duration_ms: int = Field(default=0, ge=0)


class Question(Model):
    question_id: UUID
    question: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    required: bool
    related_fr_ids: list[str]
    answer: str | None = None


class StageAttempt(Model):
    stage_id: str = Field(min_length=1)
    attempt_no: int = Field(ge=1)
    contract_version: str
    status: Literal["pending", "running", "waiting_for_input", "succeeded", "failed", "cancelled", "skipped"]
    started_at: datetime | None = None
    finished_at: datetime | None = None
    reason: str | None = None
    error: dict | None = None


class Run(Model):
    run_id: UUID
    project_id: UUID
    revision_id: UUID
    profile_id: Literal["web-fastapi-angular", "web-fastapi-react"] = "web-fastapi-angular"
    status: Literal["queued", "running", "waiting_for_input", "completed", "cancelling", "cancelled", "failed", "budget_exceeded"]
    limits: Limits
    created_at: datetime = Field(default_factory=now)
    updated_at: datetime = Field(default_factory=now)
    stages: list[StageAttempt] = Field(default_factory=list)
    current_stage: str | None = None
    questions_revision: int = Field(default=0, ge=0)
    questions: list[Question] = Field(default_factory=list)
    metrics: Metrics = Field(default_factory=Metrics)
    stop_reason: str | None = None


class AttemptState(Model):
    """Private immutable identity/input and accepted output, never serialized as Run."""
    attempt_id: UUID = Field(default_factory=uuid4)
    stage_id: str
    attempt_no: int
    input_hash: str
    input_state: dict
    accepted_result: dict | None = None


class Checkpoint(Model):
    run_id: UUID
    revision_id: UUID
    contract_version: str
    state_version: int
    input_hash: str
    state: dict


class Record(Model):
    owner_id: UUID
    run: Run
    attempts: list[AttemptState]
    # Authoritative graph snapshot, including accepted artifacts/answers/usage.
    saved_state: dict
    state_version: int = 0
    checkpoint: Checkpoint | None = None
    events: list[dict] = Field(default_factory=list)
    outbox: list[dict] = Field(default_factory=list)
    replays: dict[str, dict] = Field(default_factory=dict)
    # Required total limits for next work, set by budget service, includes reservations.
    required_limits: Limits | None = None
    tokens_complete: dict[str, bool] = Field(default_factory=lambda: {"tokens_in": True, "tokens_out": True})


class ControlError(Exception):
    def __init__(
        self, status: int, code: str, message: str, *, retry_after: int | None = None,
    ):
        # Политика допуска задаёт срок ожидания; HTTP-слой его не угадывает.
        if retry_after is not None and (type(retry_after) is not int or retry_after < 1):
            raise ValueError("retry_after must be a positive integer number of seconds")
        if status == 429 and retry_after is None:
            raise ValueError("Rate-limit errors require retry_after from the admission policy")
        super().__init__(message)
        self.status, self.code, self.message = status, code, message
        self.retry_after = retry_after


def conflict(message: str = "Run state conflicts with this command") -> ControlError:
    return ControlError(409, "RUN_STATE_CONFLICT", message)
