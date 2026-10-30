"""Надёжная команда и lease; номер предметной попытки здесь не увеличивается."""
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from ...models.base import Base


class WorkerCommand(Base):
    __tablename__ = "worker_commands"
    __table_args__ = (
        CheckConstraint("state IN ('pending', 'running', 'lost', 'recovering', 'completed', 'obsolete')", name="state"),
        CheckConstraint("generation >= 0", name="generation_nonnegative"),
        CheckConstraint(
            "recovery_generation IS NULL OR (recovery_generation > 0 AND recovery_generation <= generation)",
            name="recovery_generation",
        ),
        CheckConstraint(
            "(state IN ('running', 'recovering') AND owner_token IS NOT NULL AND expires_at IS NOT NULL) "
            "OR (state NOT IN ('running', 'recovering') AND owner_token IS NULL AND expires_at IS NULL)",
            name="lease_state",
        ),
        Index("ix_worker_commands_delivery_id", "next_delivery_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    attempt_id: Mapped[UUID] = mapped_column(ForeignKey("stage_attempts.id", ondelete="RESTRICT"), unique=True)
    state: Mapped[str] = mapped_column(String(16), server_default="pending")
    generation: Mapped[int] = mapped_column(BigInteger, server_default="0")
    recovery_generation: Mapped[int | None] = mapped_column(BigInteger)
    owner_token: Mapped[UUID | None] = mapped_column(Uuid)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str | None] = mapped_column(Text)
    next_delivery_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
