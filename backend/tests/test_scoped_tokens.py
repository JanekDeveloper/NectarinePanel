"""Scoped security token tests."""

import jwt
import pytest
from app.core.config import Settings
from app.core.security import create_scoped_token, decode_scoped_token


def test_scoped_tokens_reject_cross_scope() -> None:
    """Reject reuse of a token outside its exact browser flow."""
    settings = Settings(environment="test")
    token = create_scoped_token("owner", "adminer", settings)
    assert decode_scoped_token(token, "adminer", settings)["sub"] == "owner"
    with pytest.raises(jwt.InvalidTokenError):
        decode_scoped_token(token, "backup", settings)
