"""Transactional storage port and standalone durable reference adapter."""
from contextlib import contextmanager
from contextlib import closing
from pathlib import Path
import sqlite3
from typing import ContextManager, Iterator, Protocol
from uuid import UUID

from .models import ControlError, Record


class ControlStore(Protocol):
    def transaction(self, owner_id: UUID, run_id: UUID) -> ContextManager[Record]:
        """Owner-scope + lock aggregate; commit all effects, rollback on exception.

        Includes Run, attempts, checkpoints, events, replay and outbox. Adapters
        must serialize against run creation, answers, budget and worker writes.
        """
        ...


class SQLiteControlStore:
    """Local integration adapter; a file, never ':memory:', survives API restart.

    PostgreSQL integration must implement ControlStore against the common run
    repository rather than introduce a second source of truth in production.
    """
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path == ":memory:":
            raise ValueError("Use a file for durable run control")
        with closing(self._connect()) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS run_control (run_id TEXT PRIMARY KEY, payload TEXT NOT NULL)")

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.execute("PRAGMA synchronous=FULL")
        return db

    def create(self, record: Record) -> None:
        """Integration/bootstrap seam, not a replacement for POST /runs."""
        with closing(self._connect()) as db, db:
            db.execute("INSERT INTO run_control VALUES (?, ?)",
                       (str(record.run.run_id), record.model_dump_json()))

    @contextmanager
    def transaction(self, owner_id: UUID, run_id: UUID) -> Iterator[Record]:
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM run_control WHERE run_id = ?", (str(run_id),)).fetchone()
            record = Record.model_validate_json(row[0]) if row else None
            if record is None or record.owner_id != owner_id:
                raise ControlError(404, "NOT_FOUND", "Run not found")
            yield record
            db.execute("UPDATE run_control SET payload = ? WHERE run_id = ?",
                       (record.model_dump_json(), str(run_id)))
            db.commit()
        except sqlite3.Error as exc:
            db.rollback()
            raise ControlError(500, "INTERNAL_ERROR", "Run storage operation failed") from exc
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()
