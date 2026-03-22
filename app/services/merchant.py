import uuid
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError, UnauthorizedError
from app.core.security import generate_api_key, verify_api_key
from app.models.audit_log import ActorType
from app.models.merchant import Merchant
from app.models.user import User, UserRole
from app.repositories.merchant import MerchantRepository
from app.repositories.wallet import WalletRepository


class MerchantService:

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._repo = MerchantRepository(session)

    async def create_merchant(
        self, user: User, *, business_name: str
    ) -> tuple[Merchant, str]:
        """
        Promote a user to merchant: create the merchant profile, ensure the
        user has a wallet, and promote their role to MERCHANT.

        Returns
        -------
        (merchant, raw_api_key)
        raw_api_key is the only opportunity for the caller to see the key —
        only the bcrypt hash is persisted.

        Raises
        ------
        ConflictError – the user already has a merchant profile
        """
        existing = await self._repo.get_by_user_id(user.id)
        if existing is not None:
            raise ConflictError(
                "A merchant profile already exists for this account.",
                error_code="MERCHANT_ALREADY_EXISTS",
            )

        raw_key, prefix, hashed_key = generate_api_key()
        webhook_secret = str(uuid.uuid4())

        merchant = await self._repo.create(
            user_id=user.id,
            business_name=business_name,
            api_key_hash=hashed_key,
            api_key_prefix=prefix,
            webhook_secret=webhook_secret,
        )

        # Ensure the merchant has a wallet.  All registered users already have
        # one created atomically on registration — this is a safety net for any
        # edge case where a wallet was not created during registration.
        wallet_repo = WalletRepository(self.session)
        if await wallet_repo.get_by_user_id(user.id) is None:
            await wallet_repo.create(user_id=user.id)

        # Promote the user role to MERCHANT within the same transaction
        user.role = UserRole.MERCHANT
        await self.session.flush()

        await self.session.commit()

        # Audit log — fire-and-forget after commit
        from app.services.audit import AuditService
        audit = AuditService(self.session)
        await audit.log(
            actor_id=user.id,
            actor_type=ActorType.USER,
            action="merchant.created",
            target_type="merchant",
            target_id=merchant.id,
            metadata={"business_name": business_name},
        )
        await audit.log(
            actor_id=user.id,
            actor_type=ActorType.USER,
            action="api_key.generated",
            target_type="merchant",
            target_id=merchant.id,
        )

        return merchant, raw_key

    async def get_merchant_profile(self, user_id: uuid.UUID) -> Merchant:
        """Return the merchant profile for the given user, or raise NotFoundError."""
        merchant = await self._repo.get_by_user_id(user_id)
        if merchant is None:
            raise NotFoundError("Merchant profile")
        return merchant

    async def rotate_api_key(self, user_id: uuid.UUID) -> tuple[Merchant, str]:
        """
        Invalidate the current API key and issue a new one atomically.
        The old key is no longer valid the moment this commit succeeds.

        Returns
        -------
        (merchant, new_raw_api_key)
        """
        merchant = await self.get_merchant_profile(user_id)
        raw_key, prefix, hashed_key = generate_api_key()
        merchant = await self._repo.update_api_key(
            merchant, api_key_hash=hashed_key, api_key_prefix=prefix
        )
        await self.session.commit()

        # Audit log — fire-and-forget after commit
        from app.services.audit import AuditService
        await AuditService(self.session).log(
            actor_id=merchant.user_id,
            actor_type=ActorType.USER,
            action="api_key.generated",
            target_type="merchant",
            target_id=merchant.id,
        )

        return merchant, raw_key

    async def update_webhook_config(
        self,
        user_id: uuid.UUID,
        *,
        webhook_url: Optional[str],
        regenerate_secret: bool,
    ) -> Merchant:
        """
        Update webhook URL and/or regenerate the signing secret.
        Fields set to None are left unchanged.
        """
        merchant = await self.get_merchant_profile(user_id)
        new_secret = str(uuid.uuid4()) if regenerate_secret else None
        merchant = await self._repo.update_webhook(
            merchant,
            webhook_url=webhook_url,
            webhook_secret=new_secret,
        )
        await self.session.commit()
        return merchant


class MerchantAuthService:
    """
    Authenticates merchant API keys for merchant-facing endpoints.
    Uses prefix pre-filtering to reduce the bcrypt comparison pool.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._repo = MerchantRepository(session)

    async def authenticate(self, raw_key: str) -> Merchant:
        """
        Verify a raw API key against stored bcrypt hashes.

        Flow
        ----
        1. Extract the 8-char prefix from the raw key.
        2. Load all active merchants sharing that prefix.
        3. bcrypt-verify the raw key against each candidate.
        4. Return the matching merchant, or raise UnauthorizedError.

        Raises
        ------
        UnauthorizedError – no active merchant matched the key
        """
        prefix = raw_key[:8]
        candidates = await self._repo.get_active_by_prefix(prefix)
        for candidate in candidates:
            if verify_api_key(raw_key, candidate.api_key_hash):
                return candidate
        raise UnauthorizedError("Invalid API key.")
