from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, ForeignKey, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .project import Project


class RequirementRevision(Base):
    __tablename__ = "requirement_revisions"
    __table_args__ = (
        UniqueConstraint("project_id", "number", name="uq_requirement_revisions_project_number"),
        CheckConstraint("number >= 1", name="number_positive"),
        CheckConstraint("length(requirements_text) BETWEEN 1 AND 100000", name="text_length"),
        CheckConstraint("hash ~ '^[a-f0-9]{64}$'", name="hash_sha256"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="RESTRICT"))
    number: Mapped[int]
    requirements_text: Mapped[str] = mapped_column(Text)
    hash: Mapped[str] = mapped_column(String(64))
    project: Mapped[Project] = relationship(back_populates="revisions")
