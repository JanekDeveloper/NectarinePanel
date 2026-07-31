"""Owner authentication endpoints."""

import secrets
from datetime import UTC, datetime, timedelta

import httpx
from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import select, update

from app.api.dependencies import (
    AppSettings,
    CurrentUser,
    DbSession,
    LoginRateLimiter,
)
from app.core.security import (
    create_access_token,
    create_refresh_token,
    token_digest,
    verify_password,
)
from app.models.entities import LoginChallenge, RefreshToken, User
from app.schemas.auth import (
    LoginChallengeResponse,
    LoginRequest,
    RefreshRequest,
    TokenPair,
    TwoFactorCompleteRequest,
    UserResponse,
)
from app.schemas.common import MessageResponse
from app.services.audit import write_audit_log
from app.services.system_settings import (
    telegram_runtime_settings,
    telegram_two_factor_enabled,
)

router = APIRouter(prefix="/auth", tags=["auth"])
TWO_FACTOR_LIFETIME_SECONDS = 300


def _client_ip(request: Request) -> str | None:
    """Extract the direct client IP without trusting proxy headers."""
    return request.client.host if request.client else None


async def _telegram_two_factor_config(
    session: DbSession,
    settings: AppSettings,
) -> tuple[bool, str, int | None]:
    """Load two-factor state and Telegram delivery credentials."""
    runtime = await telegram_runtime_settings(session, settings)
    enabled = await telegram_two_factor_enabled(session)
    return enabled, runtime.bot_token, runtime.owner_id


async def _send_telegram_approval(
    bot_token: str,
    owner_id: int,
    challenge_token: str,
    request_ip: str,
) -> None:
    """Send a bounded two-factor approval request to the Telegram owner."""
    payload = {
        "chat_id": owner_id,
        "text": f"Вход в NectarinePanel\nIP: {request_ip}\nРазрешить вход?",
        "reply_markup": {
            "inline_keyboard": [
                [
                    {
                        "text": "Разрешить",
                        "callback_data": f"2fa:{challenge_token}",
                    },
                    {"text": "Отклонить", "callback_data": "2fa-cancel"},
                ]
            ]
        },
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json=payload,
            )
            response.raise_for_status()
    except httpx.HTTPError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to deliver Telegram approval",
        ) from None


async def _issue_token_pair(
    user: User,
    session: DbSession,
    settings: AppSettings,
) -> TokenPair:
    """Persist and return a fresh owner token pair."""
    refresh, expires_at = create_refresh_token(user.id, settings)
    session.add(
        RefreshToken(user_id=user.id, digest=token_digest(refresh), expires_at=expires_at)
    )
    user.last_login_at = datetime.now(UTC)
    return TokenPair(
        access_token=create_access_token(user.id, settings),
        refresh_token=refresh,
        expires_in=settings.access_token_minutes * 60,
    )


@router.post("/login", response_model=TokenPair | LoginChallengeResponse)
async def login(
    data: LoginRequest,
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
    limiter: LoginRateLimiter,
) -> TokenPair | LoginChallengeResponse:
    """Authenticate the owner and rotate credentials."""
    client_ip = _client_ip(request) or "unknown"
    allowed = await limiter.check(
        f"login:{client_ip}:{data.username.lower()}",
        settings.login_attempt_limit,
        settings.login_attempt_window_seconds,
    )
    if not allowed:
        await write_audit_log(
            session,
            action="auth.rate_limited",
            actor_id=None,
            ip_address=client_ip,
            user_agent=request.headers.get("user-agent"),
            details={"username": data.username},
        )
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts",
        )
    user = await session.scalar(select(User).where(User.username == data.username))
    if user is None or not verify_password(data.password, user.password_hash):
        await write_audit_log(
            session,
            action="auth.login_failed",
            actor_id=user.id if user else None,
            ip_address=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
            details={"username": data.username},
        )
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
        )
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is disabled")
    two_factor_enabled, bot_token, owner_id = await _telegram_two_factor_config(
        session,
        settings,
    )
    if two_factor_enabled:
        if not bot_token or owner_id is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Telegram two-factor authentication is misconfigured",
            )
        challenge_token = secrets.token_urlsafe(24)
        session.add(
            LoginChallenge(
                user_id=user.id,
                token_digest=token_digest(challenge_token),
                request_ip=client_ip,
                expires_at=datetime.now(UTC) + timedelta(seconds=TWO_FACTOR_LIFETIME_SECONDS),
            )
        )
        await _send_telegram_approval(
            bot_token,
            owner_id,
            challenge_token,
            client_ip,
        )
        await write_audit_log(
            session,
            action="auth.2fa_requested",
            actor_id=user.id,
            ip_address=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        await session.commit()
        response.status_code = status.HTTP_202_ACCEPTED
        return LoginChallengeResponse(
            challenge_token=challenge_token,
            expires_in=TWO_FACTOR_LIFETIME_SECONDS,
        )
    tokens = await _issue_token_pair(user, session, settings)
    await write_audit_log(
        session,
        action="auth.login",
        actor_id=user.id,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    await session.commit()
    return tokens


@router.post("/2fa/complete", response_model=TokenPair)
async def complete_two_factor(
    data: TwoFactorCompleteRequest,
    request: Request,
    session: DbSession,
    settings: AppSettings,
) -> TokenPair:
    """Consume an approved Telegram challenge and finish login."""
    challenge = await session.scalar(
        select(LoginChallenge).where(
            LoginChallenge.token_digest == token_digest(data.challenge_token)
        )
    )
    if challenge is None:
        raise HTTPException(status_code=404, detail="Two-factor challenge not found")
    now = datetime.now(UTC)
    expires_at = (
        challenge.expires_at
        if challenge.expires_at.tzinfo is not None
        else challenge.expires_at.replace(tzinfo=UTC)
    )
    if challenge.consumed_at is not None or expires_at <= now:
        raise HTTPException(status_code=410, detail="Two-factor challenge expired")
    if challenge.approved_at is None:
        raise HTTPException(status_code=409, detail="Two-factor approval is pending")
    user = await session.get(User, challenge.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=403, detail="Account is unavailable")
    challenge.consumed_at = now
    tokens = await _issue_token_pair(user, session, settings)
    await write_audit_log(
        session,
        action="auth.login",
        actor_id=user.id,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        details={"two_factor": "telegram"},
    )
    await session.commit()
    return tokens


@router.post("/refresh", response_model=TokenPair)
async def refresh_tokens(
    data: RefreshRequest, session: DbSession, settings: AppSettings
) -> TokenPair:
    """Rotate a valid refresh token and issue a new token pair."""
    now = datetime.now(UTC)
    stored = await session.scalar(
        select(RefreshToken).where(RefreshToken.digest == token_digest(data.refresh_token))
    )
    if stored is not None and stored.revoked_at is not None:
        await session.execute(
            update(RefreshToken)
            .where(
                RefreshToken.user_id == stored.user_id,
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token reuse detected",
        )
    if stored is None or stored.expires_at.replace(tzinfo=UTC) <= now:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token"
        )
    stored.revoked_at = now
    refresh, expires_at = create_refresh_token(stored.user_id, settings)
    session.add(
        RefreshToken(
            user_id=stored.user_id,
            digest=token_digest(refresh),
            expires_at=expires_at,
        )
    )
    await session.commit()
    return TokenPair(
        access_token=create_access_token(stored.user_id, settings),
        refresh_token=refresh,
        expires_in=settings.access_token_minutes * 60,
    )


@router.post("/logout", response_model=MessageResponse)
async def logout(
    data: RefreshRequest, user: CurrentUser, session: DbSession
) -> MessageResponse:
    """Revoke the supplied refresh token."""
    await session.execute(
        update(RefreshToken)
        .where(
            RefreshToken.user_id == user.id,
            RefreshToken.digest == token_digest(data.refresh_token),
            RefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(UTC))
    )
    await write_audit_log(session, action="auth.logout", actor_id=user.id)
    await session.commit()
    return MessageResponse(message="Session revoked")


@router.get("/me", response_model=UserResponse)
async def current_user(user: CurrentUser) -> User:
    """Return the authenticated owner."""
    return user
