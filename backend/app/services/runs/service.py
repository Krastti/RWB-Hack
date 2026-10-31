"""Создание запуска через атомарный репозиторий без зависимости от HTTP и ORM."""
from copy import deepcopy
from hashlib import sha256
import json
import re
from typing import Any, Protocol
from uuid import UUID

from ..run_queries.dto import Limits
from .dto import CreationResponse, RunCreate, RunCreationError


class RunCreationRepository(Protocol):
    def create(
        self, *, owner_id: UUID, project_id: UUID, key: str, request_hash: str,
        body: RunCreate, profile: dict[str, Any], limits: Limits,
    ) -> CreationResponse:
        """Атомарно сохранить запуск, ответ и задание или вернуть исходный ответ."""
        ...


class RunCreationService:
    def __init__(
        self, repository: RunCreationRepository, *, limits: Limits, profile: dict[str, Any],
    ) -> None:
        self._repository = repository
        self._limits = limits
        self._profile = deepcopy(profile)
        if self._profile.get("profile_id") != "web-fastapi-angular":
            raise ValueError("Unsupported run profile")

    def create_run(
        self, *, owner_id: UUID, project_id: UUID, idempotency_key: str, body: RunCreate,
    ) -> CreationResponse:
        if not isinstance(idempotency_key, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", idempotency_key):
            raise RunCreationError(422, "VALIDATION_ERROR", "Invalid idempotency key")
        normalized = json.dumps(
            {"owner_id": str(owner_id), "operation": "createRun", "resource_id": str(project_id),
             "body": body.model_dump(mode="json")},
            sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        )
        profile = self._profile
        if body.profile_id != profile["profile_id"]:
            from ...agents.profiles import ProfileLoader
            profile = ProfileLoader().load(body.profile_id).data
        return self._repository.create(
            owner_id=owner_id, project_id=project_id, key=idempotency_key,
            request_hash=sha256(normalized.encode("utf-8")).hexdigest(),
            body=body, profile=deepcopy(profile), limits=self._limits,
        )
