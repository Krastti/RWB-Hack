"""Owner-scoped project storage interface."""

from typing import Protocol
from uuid import UUID

from ..models.dto import Project, ProjectPage


class ProjectRepository(Protocol):
    """Storage boundary; all operations are scoped to an authenticated owner."""

    def create(self, project: Project) -> None:
        """Atomically insert project and revision #1, or roll back both.

        Adapters map current_revision to a revision row with project_id=project.id.
        Revisions are append-only; this interface never updates existing text.
        """
        ...

    def get(self, *, owner_id: UUID, project_id: UUID) -> Project | None:
        """Filter by BOTH IDs in storage; return None for missing/foreign rows.

        Include the revision with the highest number as current_revision.
        """
        ...

    def list(
        self, *, owner_id: UUID, limit: int, cursor: str | None,
    ) -> ProjectPage:
        """Return at most limit owned projects with their latest revisions.

        Use stable descending (created_at, id) keyset pagination. Return None
        as next_cursor only when exhausted. Cursors are opaque and scoped to
        owner_id: malformed or foreign-context cursors raise InvalidProjectInput.
        Cursor encoding and validation belong to the storage adapter; a cursor
        never replaces the owner filter. Revisions must not be modified on reads.
        """
        ...
