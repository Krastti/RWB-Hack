from datetime import datetime, timezone
from uuid import UUID, uuid4

import jwt

from ...config.auth import AuthConfig
from ...errors.auth import Unauthorized
from ...models.dto.auth import CurrentUser, Token
from ...repository.users import UserRepository
from .passwords import hash_password, verify_password


class AuthService:
    def __init__(self, repository: UserRepository, config: AuthConfig) -> None:
        self._repository = repository
        self._config = config
        # Verify a real hash for unknown logins as well.
        self._dummy_hash = hash_password(str(uuid4()))

    def issue_token(self, *, login: str, password: str) -> Token:
        if not isinstance(login, str) or not 1 <= len(login) <= 120:
            raise Unauthorized()
        user = self._repository.get_by_login(login)
        valid = verify_password(password, user.password_hash if user else self._dummy_hash)
        if not valid or user is None or user.status != "active":
            raise Unauthorized()
        now = int(datetime.now(timezone.utc).timestamp())
        token = jwt.encode(
            {"sub": str(user.id), "iat": now, "exp": now + self._config.ttl_seconds,
             "iss": self._config.issuer, "aud": self._config.audience},
            self._config.secret_key, algorithm="HS256",
        )
        return Token(access_token=token, expires_in=self._config.ttl_seconds)

    def current_user(self, token: str) -> CurrentUser:
        try:
            claims = jwt.decode(
                token, self._config.secret_key, algorithms=["HS256"],
                issuer=self._config.issuer, audience=self._config.audience,
                options={"require": ["sub", "iat", "exp", "iss", "aud"]},
            )
            user_id = UUID(claims["sub"])
        except (jwt.InvalidTokenError, ValueError, TypeError, AttributeError):
            raise Unauthorized() from None
        user = self._repository.get_by_id(user_id)
        if user is None or user.status != "active":
            raise Unauthorized()
        return CurrentUser(id=user.id, login=user.login)
