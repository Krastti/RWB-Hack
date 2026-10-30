"""PostgreSQL adapters. Each operation owns and closes its SQLAlchemy session."""
import base64
from datetime import datetime, timezone
from hashlib import sha256
import hmac
import json
from uuid import UUID

from sqlalchemy import func, select, tuple_
from sqlalchemy.orm import Session, sessionmaker

from ..errors.projects import InvalidProjectInput
from ..models import Project as ProjectRow, RequirementRevision as RevisionRow, User
from ..models.dto import Project, ProjectPage, RequirementRevision
from ..models.dto.auth import UserCredentials


class PostgreSQLUserRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def get_by_login(self, login: str) -> UserCredentials | None:
        with self._sessions() as session:
            return self._dto(session.scalar(select(User).where(User.login == login)))

    def get_by_id(self, user_id: UUID) -> UserCredentials | None:
        with self._sessions() as session:
            return self._dto(session.get(User, user_id))

    @staticmethod
    def _dto(user: User | None) -> UserCredentials | None:
        if user is None:
            return None
        return UserCredentials(user.id, user.login, user.password_hash, user.status)


class PostgreSQLProjectRepository:
    def __init__(self, sessions: sessionmaker[Session], *, cursor_secret: str) -> None:
        if len(cursor_secret.encode()) < 32:
            raise ValueError("Cursor secret must contain at least 32 bytes")
        self._sessions = sessions
        self._cursor_key = cursor_secret.encode()

    def create(self, project: Project) -> None:
        with self._sessions.begin() as session:
            session.add(ProjectRow(
                id=project.id, owner_id=project.owner_id, name=project.name,
                created_at=project.created_at, updated_at=project.updated_at,
            ))
            session.flush()
            revision = project.current_revision
            session.add(RevisionRow(
                id=revision.id, project_id=project.id, number=revision.number,
                requirements_text=revision.requirements_text, hash=revision.hash,
            ))
        # Context manager commits both inserts or rolls back both on any failure.

    @staticmethod
    def _query():
        latest_number = (
            select(func.max(RevisionRow.number))
            .where(RevisionRow.project_id == ProjectRow.id)
            .correlate(ProjectRow).scalar_subquery()
        )
        return select(ProjectRow, RevisionRow).join(
            RevisionRow,
            (RevisionRow.project_id == ProjectRow.id) & (RevisionRow.number == latest_number),
        )

    def get(self, *, owner_id: UUID, project_id: UUID) -> Project | None:
        with self._sessions() as session:
            row = session.execute(self._query().where(
                ProjectRow.owner_id == owner_id, ProjectRow.id == project_id,
            )).one_or_none()
            return self._dto(*row) if row is not None else None

    def list(self, *, owner_id: UUID, limit: int, cursor: str | None) -> ProjectPage:
        query = self._query().where(ProjectRow.owner_id == owner_id)
        if cursor is not None:
            created_at, project_id = self._decode_cursor(cursor, owner_id)
            query = query.where(tuple_(ProjectRow.created_at, ProjectRow.id) < tuple_(created_at, project_id))
        query = query.order_by(ProjectRow.created_at.desc(), ProjectRow.id.desc()).limit(limit + 1)
        with self._sessions() as session:
            rows = session.execute(query).all()
            items = tuple(self._dto(*row) for row in rows[:limit])
        next_cursor = self._encode_cursor(items[-1]) if len(rows) > limit else None
        return ProjectPage(items=items, next_cursor=next_cursor)

    @staticmethod
    def _dto(project: ProjectRow, revision: RevisionRow) -> Project:
        return Project(
            id=project.id, owner_id=project.owner_id, name=project.name,
            created_at=project.created_at.astimezone(timezone.utc),
            updated_at=project.updated_at.astimezone(timezone.utc),
            current_revision=RequirementRevision(
                id=revision.id, number=revision.number,
                requirements_text=revision.requirements_text, hash=revision.hash,
            ),
        )

    def _sign(self, payload: bytes) -> bytes:
        return hmac.digest(self._cursor_key, b"projects:v1:" + payload, sha256)

    def _encode_cursor(self, project: Project) -> str:
        payload = json.dumps([
            1, str(project.owner_id), project.created_at.isoformat(), str(project.id),
        ], separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(self._sign(payload) + payload).decode().rstrip("=")

    def _decode_cursor(self, cursor: str, owner_id: UUID) -> tuple[datetime, UUID]:
        try:
            if not cursor or len(cursor) > 2048:
                raise ValueError
            raw = base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
            signature, payload = raw[:32], raw[32:]
            if not hmac.compare_digest(signature, self._sign(payload)):
                raise ValueError
            version, owner, timestamp, project = json.loads(payload)
            if type(version) is not int or version != 1 or owner != str(owner_id):
                raise ValueError
            created_at = datetime.fromisoformat(timestamp)
            if created_at.tzinfo is None:
                raise ValueError
            return created_at, UUID(project)
        except (ValueError, TypeError, UnicodeError, OverflowError):
            raise InvalidProjectInput("Invalid project cursor") from None
