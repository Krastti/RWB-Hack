"""Owner checks, scoped keyset cursors and event lookahead pagination."""
import base64
from datetime import datetime, timezone
import hashlib
import hmac
import json
from uuid import UUID

from .dto import EventPage, Run, RunPage
from .repository import OwnedRun, RunKey, RunReadRepository


class ReadNotFound(Exception):
    pass


class InvalidReadInput(Exception):
    pass


class InvalidReadSnapshot(Exception):
    """An adapter violated its contract. Never return its data to the caller."""


def _key(run: Run) -> RunKey:
    return run.created_at, run.run_id


class RunQueryService:
    def __init__(self, repository: RunReadRepository, *, cursor_secret: str):
        if not isinstance(cursor_secret, str) or len(cursor_secret.encode()) < 32:
            raise ValueError("cursor_secret must contain at least 32 bytes")
        self._repository = repository
        self._secret = cursor_secret.encode()

    @staticmethod
    def _ids(*values: UUID) -> None:
        if any(not isinstance(value, UUID) for value in values):
            raise InvalidReadInput("Expected UUID")

    @staticmethod
    def _limit(limit: int) -> None:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise InvalidReadInput("limit must be between 1 and 100")

    @staticmethod
    def _owned(record: OwnedRun | None, owner_id: UUID) -> Run:
        if record is None or record.owner_id != owner_id:
            raise ReadNotFound()
        run = record.run.model_copy(deep=True)
        attempt_keys = {(item.stage_id, item.attempt_no) for item in run.stages}
        if len(attempt_keys) != len(run.stages):
            raise InvalidReadSnapshot("Duplicate stage attempt")
        # Lifecycle C-03 groups attempts by stage, then attempt number.
        return run.model_copy(update={"stages": tuple(sorted(
            run.stages, key=lambda item: (item.stage_id, item.attempt_no)))})

    def get_run(self, *, owner_id: UUID, run_id: UUID) -> Run:
        self._ids(owner_id, run_id)
        run = self._owned(self._repository.get_run(owner_id=owner_id, run_id=run_id), owner_id)
        if run.run_id != run_id:
            raise InvalidReadSnapshot()
        if not self._repository.project_exists(owner_id=owner_id, project_id=run.project_id):
            raise ReadNotFound()
        return run

    def list_runs(self, *, owner_id: UUID, project_id: UUID,
                  limit: int = 100, cursor: str | None = None) -> RunPage:
        self._ids(owner_id, project_id)
        self._limit(limit)
        if not self._repository.project_exists(owner_id=owner_id, project_id=project_id):
            raise ReadNotFound()
        before = self._decode(cursor, owner_id, project_id) if cursor is not None else None
        records = self._repository.list_runs(owner_id=owner_id, project_id=project_id,
                                              before=before, limit=limit + 1)
        runs = tuple(self._owned(record, owner_id) for record in records)
        if (len(runs) > limit + 1 or any(run.project_id != project_id for run in runs)
                or any(_key(a) <= _key(b) for a, b in zip(runs, runs[1:]))
                or (before is not None and any(_key(run) >= before for run in runs))):
            raise InvalidReadSnapshot()
        items = runs[:limit]
        next_cursor = self._encode(_key(items[-1]), owner_id, project_id) if len(runs) > limit else None
        return RunPage(items=items, next_cursor=next_cursor)

    def list_events(self, *, owner_id: UUID, run_id: UUID,
                    after_seq: int = 0, limit: int = 100) -> EventPage:
        self._ids(owner_id, run_id)
        self._limit(limit)
        if type(after_seq) is not int or after_seq < 0:
            raise InvalidReadInput("after_seq must be nonnegative")
        self.get_run(owner_id=owner_id, run_id=run_id)
        events = self._repository.list_events(owner_id=owner_id, run_id=run_id,
                                               after_seq=after_seq, limit=limit + 1)
        if (len(events) > limit + 1
                or any(event.run_id != run_id or event.seq <= after_seq for event in events)
                or any(a.seq >= b.seq for a, b in zip(events, events[1:]))):
            raise InvalidReadSnapshot()
        items = tuple(event.model_copy(deep=True) for event in events[:limit])
        return EventPage(items=items, next_after_seq=items[-1].seq if items else after_seq,
                         has_more=len(events) > limit)

    def _encode(self, key: RunKey, owner_id: UUID, project_id: UUID) -> str:
        body = json.dumps([1, str(owner_id), str(project_id), key[0].isoformat(), str(key[1])],
                          separators=(",", ":")).encode()
        signature = hmac.digest(self._secret, b"run-history:" + body, hashlib.sha256)
        return base64.urlsafe_b64encode(body + signature).decode().rstrip("=")

    def _decode(self, cursor: str, owner_id: UUID, project_id: UUID) -> RunKey:
        try:
            if not isinstance(cursor, str) or not 1 <= len(cursor) <= 2048:
                raise ValueError()
            raw = base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
            body, signature = raw[:-32], raw[-32:]
            expected = hmac.digest(self._secret, b"run-history:" + body, hashlib.sha256)
            if not hmac.compare_digest(signature, expected):
                raise ValueError()
            version, owner, project, created, run_id = json.loads(body)
            if type(version) is not int or version != 1 or owner != str(owner_id) or project != str(project_id):
                raise ValueError()
            timestamp = datetime.fromisoformat(created)
            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                raise ValueError()
            return timestamp.astimezone(timezone.utc), UUID(run_id)
        except (ValueError, TypeError, UnicodeError) as exc:
            raise InvalidReadInput("Invalid run cursor") from exc
