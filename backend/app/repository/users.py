from typing import Protocol
from uuid import UUID

from ..models.dto.auth import UserCredentials


class UserRepository(Protocol):
    """Read credentials from storage; never return or persist plaintext passwords."""

    def get_by_login(self, login: str) -> UserCredentials | None: ...

    def get_by_id(self, user_id: UUID) -> UserCredentials | None: ...
