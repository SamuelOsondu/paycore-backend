"""
Unit tests for AuthService.

All tests run against a real (test) PostgreSQL database inside a transaction
that is rolled back after each test — see conftest.py for the isolation strategy.
"""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, ForbiddenError, UnauthorizedError
from app.core.security import hash_password
from app.models.user import User, UserRole
from app.repositories.auth import RefreshTokenRepository
from app.schemas.auth import RegisterResponse, TokenResponse
from app.services.auth import AuthService

pytestmark = pytest.mark.asyncio


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _register(db: AsyncSession, email: str = "alice@example.com") -> RegisterResponse:
    service = AuthService(db)
    return await service.register(
        email=email,
        password="Secret123",
        full_name="Alice Doe",
    )


# ── register ──────────────────────────────────────────────────────────────────

async def test_register_success(db_session: AsyncSession) -> None:
    result = await _register(db_session)

    assert isinstance(result, RegisterResponse)
    assert result.user.email == "alice@example.com"
    assert result.user.full_name == "Alice Doe"
    assert result.access_token
    assert result.refresh_token
    assert result.token_type == "bearer"
    assert result.expires_in > 0
    # Sensitive fields must not be exposed
    assert not hasattr(result.user, "hashed_password")


async def test_register_duplicate_email_raises_conflict(db_session: AsyncSession) -> None:
    await _register(db_session)

    with pytest.raises(ConflictError) as exc_info:
        await _register(db_session)

    assert exc_info.value.error_code == "EMAIL_CONFLICT"


async def test_register_email_is_lowercased(db_session: AsyncSession) -> None:
    service = AuthService(db_session)
    result = await service.register(
        email="Bob@EXAMPLE.COM",
        password="Secret123",
        full_name="Bob Smith",
    )
    assert result.user.email == "bob@example.com"


# ── login ─────────────────────────────────────────────────────────────────────

async def test_login_success(db_session: AsyncSession) -> None:
    await _register(db_session)

    service = AuthService(db_session)
    result = await service.login(email="alice@example.com", password="Secret123")

    assert isinstance(result, TokenResponse)
    assert result.access_token
    assert result.refresh_token
    assert result.token_type == "bearer"
    assert result.expires_in > 0


async def test_login_wrong_password_raises_unauthorized(db_session: AsyncSession) -> None:
    await _register(db_session)

    service = AuthService(db_session)
    with pytest.raises(UnauthorizedError):
        await service.login(email="alice@example.com", password="WrongPass1")


async def test_login_unknown_email_raises_unauthorized(db_session: AsyncSession) -> None:
    service = AuthService(db_session)
    with pytest.raises(UnauthorizedError):
        await service.login(email="nobody@example.com", password="Secret123")


async def test_login_inactive_user_raises_forbidden(db_session: AsyncSession) -> None:
    await _register(db_session)

    # Deactivate the user directly
    from app.repositories.user import UserRepository
    repo = UserRepository(db_session)
    user = await repo.get_by_email("alice@example.com")
    assert user is not None
    await repo.set_active(user, active=False)

    service = AuthService(db_session)
    with pytest.raises(ForbiddenError):
        await service.login(email="alice@example.com", password="Secret123")


# ── refresh ───────────────────────────────────────────────────────────────────

async def test_refresh_success(db_session: AsyncSession) -> None:
    registered = await _register(db_session)
    raw_refresh = registered.refresh_token

    service = AuthService(db_session)
    result = await service.refresh(raw_token=raw_refresh)

    assert isinstance(result, TokenResponse)
    assert result.access_token
    # New refresh token must differ from the old one
    assert result.refresh_token != raw_refresh


async def test_refresh_rotates_old_token(db_session: AsyncSession) -> None:
    """After a refresh, the old token must be revoked."""
    registered = await _register(db_session)
    raw_refresh = registered.refresh_token

    service = AuthService(db_session)
    await service.refresh(raw_token=raw_refresh)

    # Trying to use the old token again must fail
    with pytest.raises(UnauthorizedError):
        await service.refresh(raw_token=raw_refresh)


async def test_refresh_invalid_token_raises_unauthorized(db_session: AsyncSession) -> None:
    service = AuthService(db_session)
    with pytest.raises(UnauthorizedError):
        await service.refresh(raw_token="totallyfaketoken")


async def test_refresh_expired_token_raises_unauthorized(db_session: AsyncSession) -> None:
    registered = await _register(db_session)

    # Manually expire the token in the DB
    from app.core.security import hash_refresh_token
    token_hash = hash_refresh_token(registered.refresh_token)
    token_repo = RefreshTokenRepository(db_session)
    db_token = await token_repo.get_by_hash(token_hash)
    assert db_token is not None
    db_token.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await db_session.flush()

    service = AuthService(db_session)
    with pytest.raises(UnauthorizedError):
        await service.refresh(raw_token=registered.refresh_token)


# ── logout ────────────────────────────────────────────────────────────────────

async def test_logout_revokes_token(db_session: AsyncSession) -> None:
    registered = await _register(db_session)
    raw_refresh = registered.refresh_token

    service = AuthService(db_session)
    await service.logout(raw_token=raw_refresh)

    # Token must now be unusable
    with pytest.raises(UnauthorizedError):
        await service.refresh(raw_token=raw_refresh)


async def test_logout_is_idempotent(db_session: AsyncSession) -> None:
    """Calling logout twice with the same token must not raise."""
    registered = await _register(db_session)
    raw_refresh = registered.refresh_token

    service = AuthService(db_session)
    await service.logout(raw_token=raw_refresh)
    await service.logout(raw_token=raw_refresh)  # should not raise


async def test_logout_unknown_token_is_silently_ignored(db_session: AsyncSession) -> None:
    service = AuthService(db_session)
    # Should complete without raising
    await service.logout(raw_token="does-not-exist")
