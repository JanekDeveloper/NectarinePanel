"""Authentication API schemas."""

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    """Owner login credentials."""

    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=12, max_length=256)


class RefreshRequest(BaseModel):
    """Opaque refresh token request."""

    refresh_token: str = Field(min_length=32, max_length=512)


class TokenPair(BaseModel):
    """Access and refresh token response."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105 - OAuth token scheme, not a credential
    expires_in: int


class LoginChallengeResponse(BaseModel):
    """Pending Telegram two-factor challenge."""

    two_factor_required: bool = True
    challenge_token: str
    expires_in: int


class TwoFactorCompleteRequest(BaseModel):
    """Telegram two-factor challenge completion request."""

    challenge_token: str = Field(min_length=32, max_length=128)


class UserResponse(BaseModel):
    """Safe owner account representation."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str
    display_name: str | None
    role: str
    is_active: bool
