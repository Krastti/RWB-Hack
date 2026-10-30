"""Outbox и lease в общей PostgreSQL; долгие вызовы выполняются вне транзакции."""
from contextlib import contextmanager
from datetime import timedelta
from typing import Iterator
from uuid import UUID, uuid4, uuid5

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from ...models import Run, RunEvent, StageAttempt
from .contracts import Lease, LeaseBusy, LeaseLost, QueueConfig, StageCommand
from .models import WorkerCommand


class PostgreSQLQueueStore:
    def __init__(self, sessions: sessionmaker[Session], config: QueueConfig) -> None:
        self.sessions = sessions
        self.config = config

    def enqueue(self, transaction: Session, attempt_id: UUID) -> UUID:
        """Добавить команду в транзакцию регистрации pending-попытки без commit."""
        transaction.flush()
        run_id = transaction.scalar(select(StageAttempt.run_id).where(StageAttempt.id == attempt_id))
        if run_id is None:
            raise ValueError("Attempt not found")
        transaction.scalar(select(Run).where(Run.id == run_id).with_for_update())
        attempt = transaction.scalar(select(StageAttempt).where(
            StageAttempt.id == attempt_id,
        ).execution_options(populate_existing=True))
        command_id = uuid5(attempt_id, "rwb.stage.execute.v1")
        existing = transaction.get(WorkerCommand, command_id)
        if existing is not None:
            return existing.id
        # Нельзя привязать новую команду к устаревшей или уже исполняемой попытке.
        if attempt.status != "pending" or not self._latest(transaction, attempt):
            raise ValueError("Only the latest pending attempt can be enqueued")
        transaction.execute(insert(WorkerCommand).values(
            id=command_id, attempt_id=attempt_id,
        ).on_conflict_do_nothing(index_elements=[WorkerCommand.attempt_id]))
        return command_id

    def reserve_due(self, limit: int = 100) -> list[UUID]:
        """Зарезервировать окно отправки; сбой отправителя не теряет команду."""
        if not 1 <= limit <= 1000:
            raise ValueError("Invalid dispatch limit")
        with self.sessions.begin() as session:
            now = self._now(session)
            rows = session.scalars(select(WorkerCommand).where(
                WorkerCommand.state.in_(("pending", "running", "lost", "recovering")),
                WorkerCommand.next_delivery_at <= now,
            ).order_by(WorkerCommand.next_delivery_at, WorkerCommand.id).limit(limit)
                .with_for_update(skip_locked=True)).all()
            for row in rows:
                row.next_delivery_at = now + timedelta(seconds=self.config.redelivery_seconds)
            return [row.id for row in rows]

    def claim(self, command_id: UUID) -> Lease | None:
        with self.sessions.begin() as session:
            locked = self._locked(session, command_id)
            if locked is None:
                return None
            row, run, attempt = locked
            now = self._now(session)
            if row.state in {"completed", "obsolete"}:
                return None
            if row.state in {"running", "recovering"}:
                if row.expires_at > now:
                    raise LeaseBusy()
                if row.state == "running":
                    self._mark_lost(session, row, run, attempt, "WORKER_LOST")
                else:
                    self._clear_lease(row, "lost")
            recovery = row.state == "lost"
            if not recovery:
                if not self._latest(session, attempt) or run.status in {
                    "completed", "cancelled", "cancelling", "failed", "budget_exceeded",
                }:
                    self._clear_lease(row, "obsolete")
                    return None
                if run.status == "waiting_for_input":
                    raise LeaseBusy()
                if attempt.status != "pending":
                    if attempt.status == "running":
                        self._mark_lost(session, row, run, attempt, "WORKER_LOST")
                        recovery = True
                    else:
                        self._clear_lease(row, "obsolete")
                        return None
            if not recovery:
                # Новая попытка ждёт завершения/восстановления прежнего владельца этапа.
                predecessor = session.scalar(select(WorkerCommand.id).join(
                    StageAttempt, StageAttempt.id == WorkerCommand.attempt_id,
                ).where(
                    StageAttempt.run_id == attempt.run_id, StageAttempt.stage_id == attempt.stage_id,
                    WorkerCommand.id != row.id,
                    WorkerCommand.state.in_(("running", "lost", "recovering")),
                ).limit(1))
                if predecessor is not None:
                    raise LeaseBusy()
                self._start_attempt(session, run, attempt)
            row.state = "recovering" if recovery else "running"
            row.generation += 1
            row.owner_token = uuid4()
            row.expires_at = now + timedelta(seconds=self.config.lease_seconds)
            row.next_delivery_at = row.expires_at
            return Lease(self._command(row, attempt), row.owner_token, row.generation,
                         row.expires_at, recovery, row.reason)

    def heartbeat(self, lease: Lease) -> None:
        with self.sessions.begin() as session:
            row, _, attempt = self._require_lease(session, lease)
            if not lease.recovery and not self._latest(session, attempt):
                raise LeaseLost()
            row.expires_at = self._now(session) + timedelta(seconds=self.config.lease_seconds)
            row.next_delivery_at = row.expires_at

    @contextmanager
    def transaction(self, lease: Lease) -> Iterator[Session]:
        """Fence проверяется под общими блокировками до и после предметной записи."""
        with self.sessions.begin() as session:
            row, run, attempt = self._require_lease(session, lease)
            if not lease.recovery and (
                not self._latest(session, attempt) or run.status not in {"running", "cancelling"}
            ):
                raise LeaseLost()
            yield session
            session.flush()
            self._assert_lease(session, row, lease)

    def complete(self, lease: Lease) -> None:
        with self.sessions.begin() as session:
            row, _, attempt = self._require_lease(session, lease)
            if not lease.recovery and (
                not self._latest(session, attempt) or attempt.status in {"pending", "running"}
            ):
                raise RuntimeError("Orchestrator did not persist an attempt outcome")
            self._clear_lease(row, "completed")

    def abandon(self, lease: Lease) -> None:
        """Ошибка callable не разрешает повторный внешний вызов старой попытки."""
        with self.sessions.begin() as session:
            row, run, attempt = self._require_lease(session, lease)
            if lease.recovery:
                self._clear_lease(row, "lost")
            else:
                self._mark_lost(session, row, run, attempt, "ORCHESTRATOR_FAILED")
            row.next_delivery_at = self._now(session)

    @staticmethod
    def _now(session: Session):
        return session.scalar(select(func.clock_timestamp()))

    @staticmethod
    def _latest(session: Session, attempt: StageAttempt) -> bool:
        number = session.scalar(select(func.max(StageAttempt.attempt_no)).where(
            StageAttempt.run_id == attempt.run_id, StageAttempt.stage_id == attempt.stage_id,
        ))
        return attempt.attempt_no == number

    @staticmethod
    def _command(row: WorkerCommand, attempt: StageAttempt) -> StageCommand:
        return StageCommand(row.id, attempt.run_id, attempt.id, attempt.stage_id,
                            attempt.attempt_no, attempt.input_hash, attempt.contract_version)

    @staticmethod
    def _clear_lease(row: WorkerCommand, state: str) -> None:
        row.state = state
        row.owner_token = None
        row.expires_at = None
        row.generation += 1

    def _locked(self, session: Session, command_id: UUID):
        run_id = session.scalar(select(StageAttempt.run_id).join(
            WorkerCommand, WorkerCommand.attempt_id == StageAttempt.id,
        ).where(WorkerCommand.id == command_id))
        if run_id is None:
            return None
        # Все изменяющие операции соблюдают порядок Run → команда → попытка.
        run = session.scalar(select(Run).where(Run.id == run_id).with_for_update())
        row = session.scalar(select(WorkerCommand).where(
            WorkerCommand.id == command_id,
        ).with_for_update())
        attempt = session.scalar(select(StageAttempt).where(
            StageAttempt.id == row.attempt_id,
        ).with_for_update())
        return row, run, attempt

    def _require_lease(self, session: Session, lease: Lease):
        locked = self._locked(session, lease.command.command_id)
        if locked is None:
            raise LeaseLost()
        self._assert_lease(session, locked[0], lease)
        return locked

    def _assert_lease(self, session: Session, row: WorkerCommand, lease: Lease) -> None:
        expected = "recovering" if lease.recovery else "running"
        if (row.state != expected or row.owner_token != lease.owner_token
                or row.generation != lease.generation or row.expires_at <= self._now(session)):
            raise LeaseLost()

    @staticmethod
    def _event(session: Session, run: Run, event_type: str, data: dict, stage_id: str | None = None):
        seq = session.scalar(select(func.coalesce(func.max(RunEvent.seq), 0)).where(
            RunEvent.run_id == run.id,
        )) + 1
        session.add(RunEvent(run_id=run.id, seq=seq, event_type=event_type,
                             stage_id=stage_id, data={"schema_version": "1.0", **data}))
        session.flush()

    def _start_attempt(self, session: Session, run: Run, attempt: StageAttempt) -> None:
        if run.status == "queued":
            run.status = "running"
            self._event(session, run, "run.status_changed", {
                "from": "queued", "to": "running", "reason_code": "WORKER_STARTED",
            })
        attempt.status = "running"
        attempt.started_at = self._now(session)
        run.current_stage = attempt.stage_id
        self._event(session, run, "stage.status_changed", {
            "from": "pending", "to": "running", "attempt_no": attempt.attempt_no,
            "contract_version": attempt.contract_version, "reason_code": "WORKER_STARTED",
        }, attempt.stage_id)

    def _mark_lost(self, session: Session, row: WorkerCommand, run: Run,
                   attempt: StageAttempt, reason: str) -> None:
        row.reason = reason
        self._clear_lease(row, "lost")
        if attempt.status == "running" and run.status in {"running", "cancelling"}:
            attempt.status = "failed"
            attempt.finished_at = self._now(session)
            attempt.reason = reason
            self._event(session, run, "stage.status_changed", {
                "from": "running", "to": "failed", "attempt_no": attempt.attempt_no,
                "contract_version": attempt.contract_version, "reason_code": reason,
            }, attempt.stage_id)
