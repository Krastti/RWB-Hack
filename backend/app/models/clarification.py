"""B-12 durable question revisions, accepted input and immutable HTTP replay."""
from uuid import UUID, uuid4

from sqlalchemy import ForeignKey, Integer, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class Clarification(Base):
    __tablename__ = "clarifications"
    run_id: Mapped[UUID] = mapped_column(ForeignKey("runs.id", ondelete="RESTRICT"), primary_key=True)
    questions_revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    question_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    question: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    related_fr_ids: Mapped[list] = mapped_column(JSONB)
    required: Mapped[bool]
    answer: Mapped[str | None] = mapped_column(Text)


class ClarificationInput(Base):
    __tablename__ = "clarification_inputs"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("runs.id", ondelete="RESTRICT"))
    payload: Mapped[dict] = mapped_column(JSONB)


class AnswerReplay(Base):
    __tablename__ = "answer_replays"
    run_id: Mapped[UUID] = mapped_column(ForeignKey("runs.id", ondelete="RESTRICT"), primary_key=True)
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    request_hash: Mapped[str] = mapped_column(Text)
    response: Mapped[dict] = mapped_column(JSONB)
