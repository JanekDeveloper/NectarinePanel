"""Password hashing, token generation, and secret encryption."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import jwt
from cryptography.fernet import Fernet, InvalidToken
from pwdlib import PasswordHash

from app.core.config import Settings

password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    """Hash a password using Argon2id."""
    return password_hash.hash(password)


def verify_password(password: str, encoded: str) -> bool:
    """Verify a plaintext password against an Argon2id hash."""
    return password_hash.verify(password, encoded)


def create_access_token(user_id: str, settings: Settings) -> str:
    """Create a short-lived signed access token."""
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_minutes),
        "jti": secrets.token_urlsafe(16),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_refresh_token(user_id: str, settings: Settings) -> tuple[str, datetime]:
    """Create an opaque refresh token and its expiry timestamp."""
    expires_at = datetime.now(UTC) + timedelta(days=settings.refresh_token_days)
    return secrets.token_urlsafe(48), expires_at


def decode_access_token(token: str, settings: Settings) -> dict[str, Any]:
    """Decode and validate an access token."""
    payload = cast(
        dict[str, Any],
        jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]),
    )
    if payload.get("type") != "access":
        raise jwt.InvalidTokenError("Unexpected token type")
    return payload


def create_scoped_token(
    subject: str,
    scope: str,
    settings: Settings,
    *,
    lifetime_seconds: int = 600,
    claims: dict[str, Any] | None = None,
) -> str:
    """Create a short-lived signed token for one internal browser flow."""
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": subject,
        "type": "scoped",
        "scope": scope,
        "iat": now,
        "exp": now + timedelta(seconds=lifetime_seconds),
        "jti": secrets.token_urlsafe(16),
    }
    payload.update(claims or {})
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_scoped_token(token: str, scope: str, settings: Settings) -> dict[str, Any]:
    """Decode a signed token and enforce its exact scope."""
    payload = cast(
        dict[str, Any],
        jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]),
    )
    if payload.get("type") != "scoped" or payload.get("scope") != scope:
        raise jwt.InvalidTokenError("Unexpected token scope")
    return payload


def token_digest(token: str) -> str:
    """Return a stable SHA-256 digest for token storage."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class SecretCipher:
    """Encrypt sensitive database fields with Fernet."""

    def __init__(self, key: str | None) -> None:
        """Initialize a cipher from a configured key."""
        self._fernet = Fernet(key.encode("ascii")) if key else None

    def encrypt(self, value: str) -> str:
        """Encrypt a value or reject missing production encryption."""
        if not self._fernet:
            return value
        return self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, value: str) -> str:
        """Decrypt a value and reject invalid ciphertext."""
        if not self._fernet:
            return value
        try:
            return self._fernet.decrypt(value.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError("Unable to decrypt stored secret") from exc
