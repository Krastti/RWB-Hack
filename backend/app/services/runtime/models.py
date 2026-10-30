"""Private persistence shared by HTTP control and leased execution (V6)."""
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ...models.base import Base


class RunRuntimeState(Base):
    __tablename__ = "run_runtime_states"
    __table_args__ = (
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("jsonb_typeof(payload) = 'object'", name="payload_object"),
        CheckConstraint("jsonb_typeof(generations) = 'object'", name="generations_object"),
    )

    run_id: Mapped[UUID] = mapped_column(ForeignKey("runs.id", ondelete="RESTRICT"), primary_key=True)
    version: Mapped[int] = mapped_column(BigInteger, default=1)
    payload: Mapped[dict] = mapped_column(JSONB)
    generations: Mapped[dict] = mapped_column(JSONB, default=dict)
