"""Атомарное создание запуска и повтор исходного ответа в PostgreSQL."""
from hashlib import sha256
import json
from typing import Any
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import func, select, tuple_
from sqlalchemy.orm import Session, joinedload, sessionmaker

from ...models import IdempotencyRecord, Project, RequirementRevision, Run, RunEvent, User
from ...models.run_creation import RunCreationResponse, RunInputSnapshot
from ..run_queries import Event, InvalidReadSnapshot, OwnedRun, RunKey
from ..run_queries.dto import Limits, Metrics, Run as RunDTO, StageAttempt as StageAttemptDTO
from .dto import CreationResponse, RunCreate, RunCreationError
from .queue import RunQueue


ACTIVE_STATUSES = ("queued", "running", "waiting_for_input", "cancelling")


class PostgreSQLRunCreationRepository:
    def __init__(self, sessions: sessionmaker[Session], queue: RunQueue, *, execution_policies=None) -> None:
        self._sessions = sessions
        self._queue = queue
        self._execution_policies = execution_policies or {}

    def create(
        self, *, owner_id: UUID, project_id: UUID, key: str, request_hash: str,
        body: RunCreate, profile: dict[str, Any], limits: Limits,
    ) -> CreationResponse:
        with self._sessions.begin() as session:
            self._owned_project(session, owner_id, project_id)
            self._lock_key(session, owner_id, project_id, key)
            replay = self._replay(session, owner_id, project_id, key, request_hash)
            if replay is not None:
                return replay

            # Одна блокировка пользователя сериализует создание во всех его проектах.
            owner = session.scalar(select(User).where(User.id == owner_id).with_for_update())
            if owner is None or owner.status != "active":
                raise RunCreationError(401, "UNAUTHORIZED", "Authentication required")
            project = self._owned_project(session, owner_id, project_id, lock=True)
            self._check_active_run(session, owner_id)
            revision = self._create_revision(session, project_id, body.requirements_text)
            run = self._create_run(session, project_id, revision.id, body.profile_id, limits)

            from ...agents.profiles.loader import FrozenProfile
            from contracts.agents.validate import load_profile_rules
            frozen_profile = FrozenProfile(json.dumps(profile, ensure_ascii=False).encode(),
                load_profile_rules(profile).encode())
            session.add(RunInputSnapshot(run_id=run.id, payload={
                "profile_snapshot": frozen_profile.snapshot(), "execution_policies": self._execution_policies,
                "revision_id": str(revision.id), "requirements_text": revision.requirements_text,
                "requirements_hash": revision.hash, "profile": profile,
                "contracts": {"platform": "2.3.0", "lifecycle": "1.0",
                              "agents": profile["contracts"]["agent_schema_version"]},
                "limits": limits.model_dump(mode="json"),
            }))
            project.updated_at = run.created_at
            self._append_initial_events(session, run)
            self._queue.enqueue(session, run.id)
            response = self._save_response(session, owner_id, project_id, key, request_hash, run)
            # Ошибка любого INSERT откатывает также ревизию, ключ и задание.
            session.flush()
            return response

    @staticmethod
    def _owned_project(session: Session, owner_id: UUID, project_id: UUID, *, lock: bool = False) -> Project:
        query = select(Project).where(Project.id == project_id, Project.owner_id == owner_id)
        if lock:
            query = query.with_for_update()
        project = session.scalar(query)
        if project is None:
            raise RunCreationError(404, "NOT_FOUND", "Resource not found")
        return project

    @staticmethod
    def _lock_key(session: Session, owner_id: UUID, project_id: UUID, key: str) -> None:
        scope = json.dumps([str(owner_id), "createRun", str(project_id), key], separators=(",", ":"))
        lock_id = int.from_bytes(sha256(scope.encode()).digest()[:8], "big", signed=True)
        if not session.scalar(select(func.pg_try_advisory_xact_lock(lock_id))):
            raise RunCreationError(409, "IDEMPOTENCY_IN_PROGRESS", "Request is already in progress")

    @staticmethod
    def _replay(
        session: Session, owner_id: UUID, project_id: UUID, key: str, request_hash: str,
    ) -> CreationResponse | None:
        record = session.scalar(select(IdempotencyRecord).where(
            IdempotencyRecord.owner_id == owner_id, IdempotencyRecord.operation == "createRun",
            IdempotencyRecord.resource_id == project_id, IdempotencyRecord.key == key,
        ))
        if record is None:
            return None
        if record.request_hash != request_hash:
            raise RunCreationError(409, "IDEMPOTENCY_KEY_CONFLICT", "Key was used with a different request")
        try:
            response_id = UUID(record.response_ref)
        except ValueError:
            raise RunCreationError(500, "INTERNAL_ERROR", "Invalid stored response reference") from None
        response = session.scalar(select(RunCreationResponse).join(
            Run, Run.id == RunCreationResponse.run_id,
        ).where(RunCreationResponse.id == response_id, Run.project_id == project_id))
        if response is None:
            raise RunCreationError(500, "INTERNAL_ERROR", "Stored response is unavailable")
        return CreationResponse(response.status_code, response.body)

    @staticmethod
    def _check_active_run(session: Session, owner_id: UUID) -> None:
        active = session.scalar(select(Run.id).join(Project, Run.project_id == Project.id).where(
            Project.owner_id == owner_id, Run.status.in_(ACTIVE_STATUSES),
        ).limit(1))
        if active is not None:
            raise RunCreationError(429, "RATE_LIMIT_EXCEEDED", "An active run already exists")

    @staticmethod
    def _create_revision(session: Session, project_id: UUID, requirements_text: str) -> RequirementRevision:
        number = session.scalar(select(func.coalesce(func.max(RequirementRevision.number), 0)).where(
            RequirementRevision.project_id == project_id,
        )) + 1
        revision = RequirementRevision(
            id=uuid4(), project_id=project_id, number=number, requirements_text=requirements_text,
            hash=sha256(requirements_text.encode("utf-8")).hexdigest(),
        )
        session.add(revision)
        session.flush()
        return revision

    @staticmethod
    def _create_run(
        session: Session, project_id: UUID, revision_id: UUID, profile_id: str, limits: Limits,
    ) -> Run:
        metrics = Metrics(tokens_in=0, tokens_out=0, model_calls=0, attempts=0,
                          manual_clarifications=0, duration_ms=0)
        run = Run(
            id=uuid4(), project_id=project_id, revision_id=revision_id, profile_id=profile_id,
            status="queued", limits=limits.model_dump(mode="json"),
            metrics=metrics.model_dump(mode="json"), questions_revision=0, questions=[],
            # now() вернул бы время начала транзакции до ожидания блокировок.
            created_at=func.clock_timestamp(), updated_at=func.clock_timestamp(),
        )
        session.add(run)
        session.flush()
        return run

    @staticmethod
    def _append_initial_events(session: Session, run: Run) -> None:
        # События описывают создание Run; now() был бы раньше при ожидании блокировок.
        session.add_all([
            RunEvent(run_id=run.id, seq=1, timestamp=run.created_at, event_type="run.created", data={
                "schema_version": "1.0", "status": "queued", "project_id": str(run.project_id),
                "revision_id": str(run.revision_id), "profile_id": run.profile_id,
            }),
            RunEvent(run_id=run.id, seq=2, timestamp=run.created_at, event_type="metrics.updated", data={
                "schema_version": "1.0", "metrics": dict(run.metrics),
                "tokens_complete": {"tokens_in": True, "tokens_out": True},
            }),
        ])

    @staticmethod
    def _save_response(
        session: Session, owner_id: UUID, project_id: UUID, key: str, request_hash: str, run: Run,
    ) -> CreationResponse:
        body = RunDTO(
            run_id=run.id, project_id=project_id, revision_id=run.revision_id,
            profile_id=run.profile_id, status=run.status, limits=run.limits,
            created_at=run.created_at, updated_at=run.updated_at, stages=(),
            questions_revision=0, questions=(), metrics=run.metrics,
        ).model_dump(mode="json")
        response_id = uuid4()
        session.add(RunCreationResponse(id=response_id, run_id=run.id, status_code=202, body=body))
        session.add(IdempotencyRecord(
            owner_id=owner_id, operation="createRun", resource_id=project_id, key=key,
            request_hash=request_hash, response_ref=str(response_id),
        ))
        return CreationResponse(202, body)


class PostgreSQLRunReadRepository:
    """Чтение сохранённых запусков и событий с проверкой владельца в каждом SQL."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def project_exists(self, *, owner_id: UUID, project_id: UUID) -> bool:
        with self._sessions() as session:
            return session.scalar(select(Project.id).where(
                Project.owner_id == owner_id, Project.id == project_id,
            )) is not None

    @staticmethod
    def _query(owner_id: UUID):
        # Один SELECT для Run и всех его попыток даёт согласованный снимок.
        return select(Run, Project.owner_id).join(Project, Run.project_id == Project.id).where(
            Project.owner_id == owner_id,
        ).options(joinedload(Run.stage_attempts))

    def get_run(self, *, owner_id: UUID, run_id: UUID) -> OwnedRun | None:
        with self._sessions() as session:
            row = session.execute(self._query(owner_id).where(Run.id == run_id)).unique().one_or_none()
            return self._snapshot(*row) if row is not None else None

    def list_runs(
        self, *, owner_id: UUID, project_id: UUID, before: RunKey | None, limit: int,
    ) -> tuple[OwnedRun, ...]:
        query = self._query(owner_id).where(Run.project_id == project_id)
        if before is not None:
            query = query.where(tuple_(Run.created_at, Run.id) < tuple_(*before))
        query = query.order_by(Run.created_at.desc(), Run.id.desc()).limit(limit)
        with self._sessions() as session:
            return tuple(self._snapshot(*row) for row in session.execute(query).unique().all())

    def list_events(
        self, *, owner_id: UUID, run_id: UUID, after_seq: int, limit: int,
    ) -> tuple[Event, ...]:
        query = select(RunEvent).join(Run, RunEvent.run_id == Run.id).join(
            Project, Run.project_id == Project.id,
        ).where(
            Project.owner_id == owner_id, RunEvent.run_id == run_id, RunEvent.seq > after_seq,
        ).order_by(RunEvent.seq).limit(limit)
        with self._sessions() as session:
            try:
                return tuple(Event.model_validate(event, from_attributes=True)
                             for event in session.scalars(query))
            except ValidationError as exc:
                raise InvalidReadSnapshot("Invalid stored event snapshot") from exc

    @staticmethod
    def _snapshot(run: Run, owner_id: UUID) -> OwnedRun:
        try:
            return OwnedRun(owner_id, RunDTO(
                run_id=run.id, project_id=run.project_id, revision_id=run.revision_id,
                profile_id=run.profile_id, status=run.status, current_stage=run.current_stage,
                limits=run.limits, created_at=run.created_at, updated_at=run.updated_at,
                stages=tuple(StageAttemptDTO.model_validate(attempt, from_attributes=True)
                             for attempt in run.stage_attempts),
                questions_revision=run.questions_revision, questions=run.questions,
                metrics=run.metrics, stop_reason=run.stop_reason,
            ))
        except ValidationError as exc:
            raise InvalidReadSnapshot("Invalid stored run snapshot") from exc
