"""Постановка задания в той же транзакции, что и создание запуска."""
from typing import Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from ...models.run_creation import RunQueueJob


class RunQueue(Protocol):
    def enqueue(self, transaction: Session, run_id: UUID) -> None:
        """Записать задание без самостоятельного commit и внешних сетевых вызовов."""
        ...


class PostgreSQLRunQueue:
    def enqueue(self, transaction: Session, run_id: UUID) -> None:
        transaction.add(RunQueueJob(run_id=run_id))
