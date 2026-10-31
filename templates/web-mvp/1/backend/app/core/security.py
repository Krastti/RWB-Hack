from typing import Annotated
import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from app.core.config import settings

class Actor(BaseModel):
    subject: str
    roles: list[str]

bearer = HTTPBearer(auto_error=False)
def current_actor(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> Actor:
    if credentials is None:
        raise HTTPException(401, "Bearer token required", headers={"WWW-Authenticate": "Bearer"})
    if len(settings.jwt_secret) < 32:
        raise HTTPException(503, "Authentication is not configured")
    try:
        claims = jwt.decode(credentials.credentials, settings.jwt_secret, algorithms=["HS256"],
                            audience=settings.jwt_audience, issuer=settings.jwt_issuer,
                            options={"require": ["exp", "sub", "iss", "aud"]})
        if not isinstance(claims["sub"], str) or not isinstance(claims.get("roles"), list) or not all(isinstance(r, str) for r in claims["roles"]):
            raise ValueError()
        return Actor(subject=claims["sub"], roles=claims["roles"])
    except (jwt.PyJWTError, ValueError):
        raise HTTPException(401, "Invalid bearer token", headers={"WWW-Authenticate": "Bearer"}) from None
