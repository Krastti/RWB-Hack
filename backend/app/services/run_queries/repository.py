"""Replaceable, synchronous storage boundary for owner-scoped read snapshots."""
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from .dto import Event, Run

RunKey = tuple[datetime, UUID]


@dataclass(frozen=True)
class OwnedRun:
    owner_id: UUID
    run: Run


class RunReadRepository(Protocol):
    """Adapters filter ownership in storage, including event queries.

    Return detached snapshots, never lazy ORM objects. Read each operation in a
    consistent transaction; no writes or locks intended to change run state.
    Foreign and missing resources are indistinguishable. SQL adapters join
    Run.project_id to Project.id and filter Project.owner_id on EVERY query.
    """

    def project_exists(self, *, owner_id: UUID, project_id: UUID) -> bool:
        """True only for an existing project owned by owner_id."""
        ...

    def get_run(self, *, owner_id: UUID, run_id: UUID) -> OwnedRun | None:
        ...

    def list_runs(self, *, owner_id: UUID, project_id: UUID,
                  before: RunKey | None, limit: int) -> tuple[OwnedRun, ...]:
        """At most limit rows, descending (created_at, run_id), key < before.

        Include all stage attempts and confirmed question/metric snapshots.
        limit can be 101: service requests one extra row for lookahead.
        """
        ...

    def list_events(self, *, owner_id: UUID, run_id: UUID,
                    after_seq: int, limit: int) -> tuple[Event, ...]:
        """Committed public events with seq > after_seq, ascending seq.

        Gaps are permitted; duplicate or reordered seq is not. limit can be 101.
        Event.data is the already-sanitized C-03 public payload, never raw model
        output, prompts, secrets or operational logs; unknown types are retained.
        """
        ...
