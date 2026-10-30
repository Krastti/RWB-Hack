"""Run persistence for C-01 v1.0.0 and lifecycle C-03 v1.0."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint,
    Index, Integer, String, Text, UniqueConstraint, Uuid, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base
from .project import Project
from .requirement_revision import RequirementRevision
from .user import User


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["revision_id", "project_id"],
            ["requirement_revisions.id", "requirement_revisions.project_id"],
            name="fk_runs_revision_project", ondelete="RESTRICT",
        ),
        CheckConstraint("status IN ('queued', 'running', 'waiting_for_input', 'completed', 'cancelling', 'cancelled', 'failed', 'budget_exceeded')", name="status"),
        CheckConstraint("length(profile_id) > 0", name="profile_id_not_empty"),
        CheckConstraint("jsonb_typeof(limits) = 'object'", name="limits_object"),
        CheckConstraint("jsonb_typeof(questions) = 'array'", name="questions_array"),
        CheckConstraint("jsonb_typeof(metrics) = 'object'", name="metrics_object"),
        CheckConstraint("questions_revision >= 0", name="questions_revision_nonnegative"),
        Index("ix_runs_project_created_id", "project_id", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="RESTRICT"))
    revision_id: Mapped[UUID] = mapped_column(Uuid)
    profile_id: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), server_default="queued")
    current_stage: Mapped[str | None] = mapped_column(Text)
    limits: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stop_reason: Mapped[str | None] = mapped_column(Text)
    questions_revision: Mapped[int] = mapped_column(Integer, server_default="0")
    questions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{\"tokens_in\": 0,\"tokens_out\": 0,\"model_calls\": 0,\"attempts\": 0,\"manual_clarifications\": 0,\"duration_ms\": 0}'::jsonb"))
    project: Mapped[Project] = relationship(foreign_keys=[project_id])
    revision: Mapped[RequirementRevision] = relationship(viewonly=True)
    stage_attempts: Mapped[list[StageAttempt]] = relationship(
        back_populates="run", order_by="(StageAttempt.stage_id, StageAttempt.attempt_no)", passive_deletes="all",
    )
    events: Mapped[list[RunEvent]] = relationship(back_populates="run", order_by="RunEvent.seq", passive_deletes="all")


class StageAttempt(Base):
    __tablename__ = "stage_attempts"
    __table_args__ = (
        UniqueConstraint("run_id", "stage_id", "attempt_no", name="uq_stage_attempts_run_stage_attempt"),
        CheckConstraint("attempt_no >= 1", name="attempt_no_positive"),
        CheckConstraint("length(stage_id) > 0", name="stage_id_not_empty"),
        CheckConstraint("length(contract_version) > 0", name="contract_version_not_empty"),
        CheckConstraint("input_hash ~ '^[a-f0-9]{64}$'", name="input_hash_sha256"),
        CheckConstraint("status IN ('pending', 'running', 'waiting_for_input', 'succeeded', 'failed', 'cancelled', 'skipped')", name="status"),
        CheckConstraint("status <> 'skipped' OR (reason IS NOT NULL AND length(reason) > 0)", name="skipped_reason"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("runs.id", ondelete="RESTRICT"))
    stage_id: Mapped[str] = mapped_column(Text)
    attempt_no: Mapped[int] = mapped_column(Integer)
    contract_version: Mapped[str] = mapped_column(Text)
    input_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), server_default="pending")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str | None] = mapped_column(Text)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    run: Mapped[Run] = relationship(back_populates="stage_attempts")


class RunEvent(Base):
    __tablename__ = "run_events"
    __table_args__ = (
        CheckConstraint("seq >= 1", name="seq_positive"),
        CheckConstraint("length(event_type) > 0", name="event_type_not_empty"),
        CheckConstraint("jsonb_typeof(data) = 'object'", name="data_object"),
    )

    run_id: Mapped[UUID] = mapped_column(ForeignKey("runs.id", ondelete="RESTRICT"), primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    event_type: Mapped[str] = mapped_column(Text)
    stage_id: Mapped[str | None] = mapped_column(Text)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    run: Mapped[Run] = relationship(back_populates="events")


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint("owner_id", "operation", "resource_id", "key", name="uq_idempotency_records_scope_key"),
        CheckConstraint("length(operation) > 0", name="operation_not_empty"),
        CheckConstraint("length(key) BETWEEN 1 AND 128 AND key ~ '^[A-Za-z0-9._:-]+$'", name="key_format"),
        CheckConstraint("request_hash ~ '^[a-f0-9]{64}$'", name="request_hash_sha256"),
        CheckConstraint("length(response_ref) > 0", name="response_ref_not_empty"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    operation: Mapped[str] = mapped_column(Text)
    resource_id: Mapped[UUID] = mapped_column(Uuid)
    key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    response_ref: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    owner: Mapped[User] = relationship()
