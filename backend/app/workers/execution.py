"""Вызов оркестратора с heartbeat и проверяемым контекстом записи результата."""
from contextlib import contextmanager
import logging
from threading import Event, Thread
from typing import Iterator
from uuid import UUID

from sqlalchemy.orm import Session

from ..adapters.queue import Lease, LeaseLost, Orchestrator, RecoveryHandler
from ..adapters.queue.postgresql import PostgreSQLQueueStore


logger = logging.getLogger(__name__)


class WorkerExecutionFailed(RuntimeError):
    """Предметная ошибка скрыта; восстановление запланировано в PostgreSQL."""


class LeasedExecution:
    def __init__(self, store: PostgreSQLQueueStore, lease: Lease) -> None:
        self.store = store
        self.lease = lease
        self._stop = Event()
        self._lost = Event()
        self._thread = Thread(target=self._renew, name="stage-lease-heartbeat", daemon=True)
        self.activity_clock = None

    def __enter__(self) -> "LeasedExecution":
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self._stop.set()
        self._thread.join(timeout=self.store.config.heartbeat_seconds)

    def ensure_current(self) -> None:
        if self._lost.is_set():
            raise LeaseLost()

    def heartbeat(self) -> None:
        self.ensure_current()
        try:
            self.store.heartbeat(self.lease)
            clock = self.activity_clock
            if clock is not None:
                clock.heartbeat()
        except LeaseLost:
            self._lost.set()
            raise
        except Exception:
            logger.exception("Lease heartbeat failed: command_id=%s run_id=%s attempt_id=%s generation=%s",
                             self.lease.command.command_id, self.lease.command.run_id,
                             self.lease.command.attempt_id, self.lease.generation)
            self._lost.set()
            raise LeaseLost() from None

    @contextmanager
    def transaction(self, *, recovery_complete: bool = False) -> Iterator[Session]:
        self.ensure_current()
        with self.store.transaction(self.lease, recovery_complete=recovery_complete) as session:
            yield session
            self.ensure_current()

    def _renew(self) -> None:
        while not self._stop.wait(self.store.config.heartbeat_seconds):
            try:
                self.heartbeat()
            except LeaseLost:
                return


class StageWorker:
    def __init__(self, store: PostgreSQLQueueStore, orchestrator: Orchestrator,
                 recovery: RecoveryHandler) -> None:
        if not callable(orchestrator) or not callable(recovery):
            raise TypeError("Orchestrator and recovery handler must be callable")
        self.store = store
        self.orchestrator = orchestrator
        self.recovery = recovery

    def execute(self, command_id: UUID) -> str:
        lease = self.store.claim(command_id)
        if lease is None:
            return "already_handled"
        with LeasedExecution(self.store, lease) as execution:
            try:
                execution.heartbeat()
                handler = self.recovery if lease.recovery else self.orchestrator
                handler(lease.command, execution)
                execution.ensure_current()
                self.store.complete(lease)
                return "recovered" if lease.recovery else "completed"
            except LeaseLost:
                # Истёкшее поколение не вправе даже помечать новую попытку ошибочной.
                return "fenced"
            except Exception:
                logger.exception(
                    "Worker execution failed: command_id=%s run_id=%s attempt_id=%s generation=%s recovery=%s",
                    command_id, lease.command.run_id, lease.command.attempt_id, lease.generation, lease.recovery,
                )
                try:
                    self.store.abandon(lease)
                except LeaseLost:
                    return "fenced"
                except Exception:
                    logger.exception("Could not schedule recovery: command_id=%s generation=%s",
                                     command_id, lease.generation)
                    raise
                raise WorkerExecutionFailed("Execution needs recovery") from None
