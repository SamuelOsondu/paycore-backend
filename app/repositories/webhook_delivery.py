import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.webhook_delivery import WebhookDelivery, WebhookDeliveryStatus
from app.repositories.base import BaseRepository


class WebhookDeliveryRepository(BaseRepository[WebhookDelivery]):

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def create(
        self,
        *,
        merchant_id: uuid.UUID,
        transaction_id: uuid.UUID,
        event_type: str,
        payload: dict,
    ) -> WebhookDelivery:
        delivery = WebhookDelivery(
            merchant_id=merchant_id,
            transaction_id=transaction_id,
            event_type=event_type,
            payload=payload,
        )
        self.session.add(delivery)
        await self.session.flush()
        await self.session.refresh(delivery)
        return delivery

    async def get_by_id(self, delivery_id: uuid.UUID) -> Optional[WebhookDelivery]:
        result = await self.session.execute(
            select(WebhookDelivery).where(WebhookDelivery.id == delivery_id)
        )
        return result.scalar_one_or_none()

    async def get_pending_for_retry(
        self, *, now: datetime, limit: int = 100
    ) -> list[WebhookDelivery]:
        """
        Return pending deliveries whose next_retry_at is at or before `now`.
        Used by the retry sweep beat task.
        """
        result = await self.session.execute(
            select(WebhookDelivery)
            .where(
                WebhookDelivery.status == WebhookDeliveryStatus.PENDING,
                WebhookDelivery.next_retry_at <= now,
            )
            .order_by(WebhookDelivery.next_retry_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def update_delivery_result(
        self,
        delivery: WebhookDelivery,
        *,
        status: WebhookDeliveryStatus,
        attempt_count: int,
        next_retry_at: Optional[datetime],
        last_response_code: Optional[int],
        last_error: Optional[str],
    ) -> WebhookDelivery:
        delivery.status = status
        delivery.attempt_count = attempt_count
        delivery.next_retry_at = next_retry_at
        delivery.last_response_code = last_response_code
        delivery.last_error = last_error
        await self.session.flush()
        await self.session.refresh(delivery)
        return delivery

    async def list_all(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> list[WebhookDelivery]:
        """Return all delivery records ordered by creation time (newest first)."""
        result = await self.session.execute(
            select(WebhookDelivery)
            .order_by(WebhookDelivery.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def count_all(self) -> int:
        """Return total count of all delivery records."""
        result = await self.session.execute(
            select(func.count()).select_from(WebhookDelivery)
        )
        return result.scalar_one()
