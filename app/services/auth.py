import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import ConflictError, ForbiddenError, UnauthorizedError
from app.core.security import (
    TIMING_DUMMY_HASH,
    create_access_token,
    create_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from app.models.audit_log import ActorType
from app.models.user import User
from app.repositories.auth import RefreshTokenRepository
from app.repositories.user import UserRepository
from app.schemas.auth import RegisterResponse, TokenResponse
from app.schemas.user import UserOut
from app.services.wallet import WalletService

logger = logging.getLogger(__name__)


class AuthService:

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.user_repo = UserRepository(session)
        self.token_repo = RefreshTokenRepository(session)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _issue_token_pair(self, user: User) -> tuple[str, str, str, int]:
        """
        Create a signed access token + a random refresh token.

        Returns
        -------
        (access_token, raw_refresh, refresh_hash, expires_in_seconds)
        Store only refresh_hash in the database; hand raw_refresh to the client.
        """
        access_token = create_access_token(user.id, user.role.value)
        raw_refresh, refresh_hash = create_refresh_token()
        expires_in = settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
        return access_token, raw_refresh, refresh_hash, expires_in

    async def _store_refresh_token(
        self, user_id: uuid.UUID, refresh_hash: str
    ) -> None:
        expires_at = datetime.now(timezone.utc) + timedelta(
            days=settings.REFRESH_TOKEN_EXPIRE_DAYS
        )
        await self.token_repo.create(
            user_id=user_id,
            token_hash=refresh_hash,
            expires_at=expires_at,
        )

    # ── Public operations ─────────────────────────────────────────────────────

    async def register(
        self,
        *,
        email: str,
        password: str,
        full_name: str,
        phone: str | None = None,
    ) -> RegisterResponse:
        """
        Create a new user account and immediately issue a token pair.

        Raises
        ------
        ConflictError   – email already registered
        """
        existing = await self.user_repo.get_by_email(email)
        if existing is not None:
            raise ConflictError(
                "An account with this email already exists.",
                error_code="EMAIL_CONFLICT",
            )

        hashed = await asyncio.to_thread(hash_password, password)
        user = await self.user_repo.create(
            email=email,
            hashed_password=hashed,
            full_name=full_name,
            phone=phone,
        )

        # Create the user's default wallet in the same transaction — atomic.
        await WalletService(self.session).create_wallet(user.id)

        access_token, raw_refresh, refresh_hash, expires_in = self._issue_token_pair(user)
        await self._store_refresh_token(user.id, refresh_hash)

        await self.session.commit()

        # Audit log — fire-and-forget after commit
        from app.services.audit import AuditService
        await AuditService(self.session).log(
            actor_id=user.id,
            actor_type=ActorType.USER,
            action="user.registered",
            target_type="user",
            target_id=user.id,
        )

        return RegisterResponse(
            user=UserOut.model_validate(user),
            access_token=access_token,
            refresh_token=raw_refresh,
            token_type="bearer",
            expires_in=expires_in,
        )

    async def login(self, *, email: str, password: str) -> TokenResponse:
        """
        Authenticate a user and return a fresh token pair.

        Timing-safe: when the email is not found we still run a full bcrypt
        verify (against a dummy hash) so the response time is indistinguishable
        from the "wrong password" path, preventing email-enumeration attacks.

        Raises
        ------
        UnauthorizedError – email not found or wrong password
        ForbiddenError    – account is deactivated
        """
        user = await self.user_repo.get_by_email(email)

        if user is None:
            # Run dummy bcrypt to normalise timing.
            await asyncio.to_thread(verify_password, password, TIMING_DUMMY_HASH)
            raise UnauthorizedError("Invalid credentials.")

        password_ok = await asyncio.to_thread(
            verify_password, password, user.hashed_password
        )
        if not password_ok:
            raise UnauthorizedError("Invalid credentials.")

        if not user.is_active:
            raise ForbiddenError("Account is deactivated. Please contact support.")

        access_token, raw_refresh, refresh_hash, expires_in = self._issue_token_pair(user)
        await self._store_refresh_token(user.id, refresh_hash)
        await self.session.commit()

        # Audit log — fire-and-forget after commit
        from app.services.audit import AuditService
        await AuditService(self.session).log(
            actor_id=user.id,
            actor_type=ActorType.USER,
            action="user.login",
            target_type="user",
            target_id=user.id,
        )

        return TokenResponse(
            access_token=access_token,
            refresh_token=raw_refresh,
            token_type="bearer",
            expires_in=expires_in,
        )

    async def refresh(self, *, raw_token: str) -> TokenResponse:
        """
        Rotate a refresh token: revoke the old one and issue a fresh pair.

        Raises
        ------
        UnauthorizedError – token not found, revoked, or expired
        """
        token_hash = hash_refresh_token(raw_token)
        db_token = await self.token_repo.get_by_hash(token_hash)

        if db_token is None or db_token.is_revoked:
            raise UnauthorizedError("Invalid or expired refresh token.")

        if db_token.expires_at < datetime.now(timezone.utc):
            raise UnauthorizedError("Refresh token has expired.")

        user = await self.user_repo.get_by_id(db_token.user_id)
        if user is None or not user.is_active:
            raise UnauthorizedError("Invalid or expired refresh token.")

        # Token rotation: revoke the consumed token before issuing a new one.
        await self.token_repo.revoke(db_token)
        access_token, raw_refresh, refresh_hash, expires_in = self._issue_token_pair(user)
        await self._store_refresh_token(user.id, refresh_hash)
        await self.session.commit()

        return TokenResponse(
            access_token=access_token,
            refresh_token=raw_refresh,
            token_type="bearer",
            expires_in=expires_in,
        )

    async def logout(self, *, raw_token: str) -> None:
        """
        Revoke the supplied refresh token.
        Idempotent — calling logout with an already-revoked or unknown token
        is silently ignored (no error raised).
        """
        token_hash = hash_refresh_token(raw_token)
        db_token = await self.token_repo.get_by_hash(token_hash)

        if db_token is not None and not db_token.is_revoked:
            await self.token_repo.revoke(db_token)
            await self.session.commit()
