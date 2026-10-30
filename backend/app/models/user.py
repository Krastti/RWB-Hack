from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .project import Project


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("length(login) BETWEEN 1 AND 120", name="login_length"),
        CheckConstraint("length(password_hash) > 0", name="password_hash_not_empty"),
        CheckConstraint("status IN ('active', 'disabled')", name="status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    login: Mapped[str] = mapped_column(String(120), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), server_default="active")
    projects: Mapped[list[Project]] = relationship(back_populates="owner", passive_deletes="all")
