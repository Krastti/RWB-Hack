"""Argon2id password hashing; plaintext is never retained."""
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    if not isinstance(password, str) or not 1 <= len(password) <= 1024:
        raise ValueError("Password must contain 1–1024 characters")
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    if not isinstance(password, str) or not 1 <= len(password) <= 1024:
        return False
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError, TypeError):
        return False
