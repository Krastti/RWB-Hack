from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from uuid import UUID, uuid4

from ...errors import InvalidProjectInput, ProjectNotFound
from ...models.dto import Project, ProjectPage, RequirementRevision
from ...repository import ProjectRepository

class ProjectService:
    def __init__(self, repository: ProjectRepository) -> None:
        self._repository = repository

    def create_project(
        self, *, owner_id: UUID, name: str, requirements_text: str,
    ) -> Project:
        """Create a project and its immutable initial requirement revision."""
        self._validate_owner(owner_id)
        self._validate_text("name", name, 120)
        self._validate_text("requirements_text", requirements_text, 100_000)
        now = datetime.now(timezone.utc)
        project = Project(
            id=uuid4(), owner_id=owner_id, name=name,
            created_at=now, updated_at=now,
            current_revision=RequirementRevision(
                id=uuid4(), number=1, requirements_text=requirements_text,
                hash=sha256(requirements_text.encode("utf-8")).hexdigest(),
            ),
        )
        self._repository.create(project)
        return project

    def get_project(self, *, owner_id: UUID, project_id: UUID) -> Project:
        self._validate_owner(owner_id)
        if not isinstance(project_id, UUID):
            raise InvalidProjectInput("project_id must be a UUID")
        project = self._repository.get(owner_id=owner_id, project_id=project_id)
        if project is None or project.owner_id != owner_id:
            raise ProjectNotFound("Project not found")
        return project

    def list_projects(
        self, *, owner_id: UUID, limit: int = 100, cursor: str | None = None,
    ) -> ProjectPage:
        self._validate_owner(owner_id)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise InvalidProjectInput("limit must be an integer between 1 and 100")
        if cursor is not None and (not isinstance(cursor, str) or not cursor):
            raise InvalidProjectInput("cursor must be a non-empty string")
        page = self._repository.list(owner_id=owner_id, limit=limit, cursor=cursor)
        # Fail closed if an adapter violates the owner-scoped repository contract.
        if any(project.owner_id != owner_id for project in page.items):
            raise ProjectNotFound("Project not found")
        return page

    @staticmethod
    def _validate_owner(owner_id: UUID) -> None:
        if not isinstance(owner_id, UUID):
            raise InvalidProjectInput("owner_id must be a UUID")

    @staticmethod
    def _validate_text(field: str, value: str, maximum: int) -> None:
        if not isinstance(value, str) or not 1 <= len(value) <= maximum:
            raise InvalidProjectInput(f"{field} must contain 1–{maximum} characters")
