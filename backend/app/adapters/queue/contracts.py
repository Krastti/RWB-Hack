"""Внутренний контракт доставки попытки этапа; C-03 1.0."""
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, ContextManager, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class QueueConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    queue_name: str = Field(default="rwb.stages", min_length=1)
    control_queue: str = Field(default="rwb.control", min_length=1)
    lease_seconds: int = Field(default=90, ge=15, strict=True)
    heartbeat_seconds: int = Field(default=20, ge=1, strict=True)
    redelivery_seconds: int = Field(default=30, ge=1, strict=True)
    dispatch_seconds: int = Field(default=5, ge=1, strict=True)

    def model_post_init(self, context) -> None:
        if self.heartbeat_seconds * 3 >= self.lease_seconds:
            raise ValueError("Heartbeat interval must be less than a third of lease duration")
        if self.queue_name == self.control_queue:
            raise ValueError("Stage and control queues must be different")


@dataclass(frozen=True)
class StageCommand:
    command_id: UUID
    run_id: UUID
    attempt_id: UUID
    stage_id: str
    attempt_no: int
    input_hash: str
    contract_version: str


@dataclass(frozen=True)
class Lease:
    command: StageCommand
    owner_token: UUID
    generation: int
    expires_at: datetime
    recovery: bool = False
    reason_code: str | None = None


class LeaseLost(RuntimeError):
    """Исполнение потеряло право изменять принятые результаты."""


class LeaseBusy(RuntimeError):
    """У команды уже есть действующий владелец."""


class ExecutionContext(Protocol):
    lease: Lease

    def heartbeat(self) -> None:
        """Продлить только текущее, ещё не истёкшее поколение."""
        ...

    def transaction(self) -> ContextManager["Session"]:
        """Общий commit результатов с проверкой lease до и после записи."""
        ...


class Orchestrator(Protocol):
    def __call__(self, command: StageCommand, execution: ExecutionContext) -> None:
        """Выполнить принятую попытку; сохранять результат через execution.transaction."""
        ...


class RecoveryHandler(Protocol):
    def __call__(self, command: StageCommand, execution: ExecutionContext) -> None:
        """Идемпотентно обработать потерю исполнения и политику новой попытки."""
        ...
