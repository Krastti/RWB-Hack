"""Explicit local example adapter. Never installed by the production factory."""
from collections.abc import Iterable, Mapping
from uuid import UUID

from .dto import Event
from .repository import OwnedRun, RunKey


class InMemoryRunReadRepository:
    """Detached fixed snapshots supplied by the caller; no generated fake data."""

    def __init__(self, *, projects: Mapping[UUID, UUID],
                 runs: Iterable[OwnedRun] = (), events: Iterable[Event] = ()):
        self._projects = dict(projects)  # project_id -> owner_id
        self._runs = {record.run.run_id: OwnedRun(record.owner_id, record.run.model_copy(deep=True))
                      for record in runs}
        self._events = tuple(event.model_copy(deep=True) for event in events)
        if len({(event.run_id, event.seq) for event in self._events}) != len(self._events):
            raise ValueError("Duplicate event sequence")
        if any(self._projects.get(record.run.project_id) != record.owner_id
               for record in self._runs.values()):
            raise ValueError("Run owner must match project owner")
        if any(event.run_id not in self._runs for event in self._events):
            raise ValueError("Event must belong to an existing run")

    def project_exists(self, *, owner_id: UUID, project_id: UUID) -> bool:
        return self._projects.get(project_id) == owner_id

    def get_run(self, *, owner_id: UUID, run_id: UUID) -> OwnedRun | None:
        record = self._runs.get(run_id)
        if record is None or record.owner_id != owner_id:
            return None
        return OwnedRun(record.owner_id, record.run.model_copy(deep=True))

    def list_runs(self, *, owner_id: UUID, project_id: UUID,
                  before: RunKey | None, limit: int) -> tuple[OwnedRun, ...]:
        records = [record for record in self._runs.values()
                   if record.owner_id == owner_id and record.run.project_id == project_id
                   and (before is None or (record.run.created_at, record.run.run_id) < before)]
        records.sort(key=lambda record: (record.run.created_at, record.run.run_id), reverse=True)
        return tuple(OwnedRun(record.owner_id, record.run.model_copy(deep=True))
                     for record in records[:limit])

    def list_events(self, *, owner_id: UUID, run_id: UUID,
                    after_seq: int, limit: int) -> tuple[Event, ...]:
        if self.get_run(owner_id=owner_id, run_id=run_id) is None:
            return ()
        events = sorted((event for event in self._events
                         if event.run_id == run_id and event.seq > after_seq), key=lambda event: event.seq)
        return tuple(event.model_copy(deep=True) for event in events[:limit])
