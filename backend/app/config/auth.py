from dataclasses import dataclass, field


@dataclass(frozen=True)
class AuthConfig:
    secret_key: str = field(repr=False)
    issuer: str = "rwb-platform"
    audience: str = "rwb-platform-api"
    ttl_seconds: int = 1800

    def __post_init__(self) -> None:
        if not isinstance(self.secret_key, str) or len(self.secret_key.encode()) < 32:
            raise ValueError("JWT secret must contain at least 32 bytes")
        if not isinstance(self.issuer, str) or not self.issuer.strip():
            raise ValueError("JWT issuer is required")
        if not isinstance(self.audience, str) or not self.audience.strip():
            raise ValueError("JWT audience is required")
        if type(self.ttl_seconds) is not int or not 1 <= self.ttl_seconds <= 86400:
            raise ValueError("JWT lifetime must be between 1 and 86400 seconds")
