import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.merchant import Merchant
from app.repositories.base import BaseRepository


class MerchantRepository(BaseRepository[Merchant]):

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        business_name: str,
        api_key_hash: str,
        api_key_prefix: str,
        webhook_secret: str,
    ) -> Merchant:
        merchant = Merchant(
            user_id=user_id,
            business_name=business_name,
            api_key_hash=api_key_hash,
            api_key_prefix=api_key_prefix,
            webhook_secret=webhook_secret,
        )
        self.session.add(merchant)
        await self.session.flush()
        await self.session.refresh(merchant)
        return merchant

    async def get_by_id(self, merchant_id: uuid.UUID) -> Optional[Merchant]:
        result = await self.session.execute(
            select(Merchant).where(
                Merchant.id == merchant_id,
                Merchant.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def get_by_user_id(self, user_id: uuid.UUID) -> Optional[Merchant]:
        result = await self.session.execute(
            select(Merchant).where(
                Merchant.user_id == user_id,
                Merchant.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def get_active_by_prefix(self, prefix: str) -> list[Merchant]:
        """
        Return all active, non-deleted merchants with the given api_key_prefix.
        Used to pre-filter candidates before the expensive bcrypt comparison.
        """
        result = await self.session.execute(
            select(Merchant).where(
                Merchant.api_key_prefix == prefix,
                Merchant.is_active.is_(True),
                Merchant.deleted_at.is_(None),
            )
        )
        return list(result.scalars().all())

    async def update_api_key(
        self,
        merchant: Merchant,
        *,
        api_key_hash: str,
        api_key_prefix: str,
    ) -> Merchant:
        merchant.api_key_hash = api_key_hash
        merchant.api_key_prefix = api_key_prefix
        await self.session.flush()
        await self.session.refresh(merchant)
        return merchant

    async def update_webhook(
        self,
        merchant: Merchant,
        *,
        webhook_url: Optional[str],
        webhook_secret: Optional[str],
    ) -> Merchant:
        if webhook_url is not None:
            merchant.webhook_url = webhook_url
        if webhook_secret is not None:
            merchant.webhook_secret = webhook_secret
        await self.session.flush()
        await self.session.refresh(merchant)
        return merchant

    async def soft_delete(self, merchant: Merchant) -> None:
        merchant.soft_delete()
        await self.session.flush()
