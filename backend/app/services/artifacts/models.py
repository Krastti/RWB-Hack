"""V10: transactional artifact bytes and immutable execution receipts."""
from uuid import UUID
from sqlalchemy import ForeignKey, Uuid, LargeBinary, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from ...models.base import Base


class ArtifactObject(Base):
    __tablename__ = 'artifact_objects'
    artifact_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    run_id: Mapped[UUID] = mapped_column(ForeignKey('runs.id'))
    attempt_id: Mapped[UUID] = mapped_column(ForeignKey('stage_attempts.id'))
    metadata_json: Mapped[dict] = mapped_column(JSONB)
    content: Mapped[bytes] = mapped_column(LargeBinary)


class ExecutionReceipt(Base):
    __tablename__ = 'execution_receipts'
    attempt_id: Mapped[UUID] = mapped_column(ForeignKey('stage_attempts.id'), primary_key=True)
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSONB)


class ModelCall(Base):
    __tablename__ = 'model_call_ledger'
    call_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    run_id: Mapped[UUID] = mapped_column(ForeignKey('runs.id'))
    attempt_id: Mapped[UUID] = mapped_column(ForeignKey('stage_attempts.id'))
    payload: Mapped[dict] = mapped_column(JSONB)


class ArtifactSnapshot(Base):
    __tablename__ = 'artifact_snapshots'
    snapshot_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    run_id: Mapped[UUID] = mapped_column(ForeignKey('runs.id'))
    manifest: Mapped[dict] = mapped_column(JSONB)
