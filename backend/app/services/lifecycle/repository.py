"""Atomic repository interface and a standalone local file adapter."""
from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3
from typing import ContextManager, Iterator, Protocol
from uuid import UUID

from .models import LifecycleError, RunRecord, conflict


class LifecycleRepository(Protocol):
    def insert(self, record: RunRecord) -> None:
        """Insert the whole new aggregate/events atomically; duplicates conflict."""
        ...

    def transaction(self, *, owner_id: UUID, run_id: UUID) -> ContextManager[RunRecord]:
        """Owner-scope and serialize ALL writers to this Run until commit/rollback.

        Commit state, generation, attempts, counters, domain data, events and seq
        together. Discard all changes on any exception, including commit failure.
        Missing and foreign rows raise the same 404 NOT_FOUND. A persistent
        production adapter shares this transaction with B-05/B-09/B-10/B-12.
        """
        ...


class SQLiteLifecycleRepository:
    """Local integration adapter; not automatically connected to the application.

    The future PostgreSQL adapter implements this interface over the common
    Run/StageAttempt/RunEvent repository, not a second production state store.
    """
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path == ":memory:":
            raise ValueError("Use a file to retain accepted lifecycle operations")
        with closing(self._connect()) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS lifecycle_runs (run_id TEXT PRIMARY KEY, payload TEXT NOT NULL)")

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.execute("PRAGMA synchronous=FULL")
        return db

    def insert(self, record: RunRecord) -> None:
        try:
            with closing(self._connect()) as db, db:
                db.execute("INSERT INTO lifecycle_runs VALUES (?, ?)",
                           (str(record.run_id), record.model_dump_json()))
        except sqlite3.IntegrityError as exc:
            raise conflict("Run already exists") from exc
        except sqlite3.Error as exc:
            raise LifecycleError(500, "INTERNAL_ERROR", "Lifecycle storage operation failed") from exc

    @contextmanager
    def transaction(self, *, owner_id: UUID, run_id: UUID) -> Iterator[RunRecord]:
        db = None
        try:
            db = self._connect()
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM lifecycle_runs WHERE run_id = ?", (str(run_id),)).fetchone()
            record = RunRecord.model_validate_json(row[0]) if row else None
            if record is None or record.owner_id != owner_id:
                raise LifecycleError(404, "NOT_FOUND", "Run not found")
            original = record.model_dump_json()
            yield record
            payload = record.model_dump_json()
            if payload != original:
                db.execute("UPDATE lifecycle_runs SET payload = ? WHERE run_id = ?",
                           (payload, str(run_id)))
            db.commit()
        except sqlite3.Error as exc:
            if db is not None:
                db.rollback()
            raise LifecycleError(500, "INTERNAL_ERROR", "Lifecycle storage operation failed") from exc
        except BaseException:
            if db is not None:
                db.rollback()
            raise
        finally:
            if db is not None:
                db.close()
