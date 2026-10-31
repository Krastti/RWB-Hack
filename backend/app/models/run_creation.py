"""Снимки принятого входа, исходные ответы и надёжные задания очереди."""
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class RunInputSnapshot(Base):
    __tablename__ = "run_input_snapshots"
    __table_args__ = (CheckConstraint("jsonb_typeof(payload) = 'object'", name="payload_object"),)

    run_id: Mapped[UUID] = mapped_column(ForeignKey("runs.id", ondelete="RESTRICT"), primary_key=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)


class RunCreationResponse(Base):
    __tablename__ = "run_creation_responses"
    __table_args__ = (
        CheckConstraint("status_code = 202", name="accepted"),
        CheckConstraint("jsonb_typeof(body) = 'object'", name="body_object"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("runs.id", ondelete="RESTRICT"), unique=True)
    status_code: Mapped[int] = mapped_column(Integer)
    body: Mapped[dict[str, Any]] = mapped_column(JSONB)


class RunQueueJob(Base):
    __tablename__ = "run_queue_jobs"
    __table_args__ = (
        CheckConstraint("kind = 'createRun'", name="kind"),
        CheckConstraint("state IN ('pending', 'rejected')", name="delivery_state"),
        CheckConstraint("failures BETWEEN 0 AND 3", name="failures_bounded"),
        Index("ix_run_queue_jobs_created_id", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("runs.id", ondelete="RESTRICT"), unique=True)
    kind: Mapped[str] = mapped_column(Text, server_default="createRun")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    state: Mapped[str] = mapped_column(Text, server_default="pending")
    failures: Mapped[int] = mapped_column(Integer, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    failure_code: Mapped[str | None] = mapped_column(Text)
