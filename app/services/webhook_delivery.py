import logging
import uuid
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.merchant import Merchant
from app.models.webhook_delivery import WebhookDelivery, WebhookDeliveryStatus
from app.repositories.webhook_delivery import WebhookDeliveryRepository

logger = logging.getLogger(__name__)


class WebhookDeliveryService:
    """
    Creates webhook delivery records and enqueues async delivery tasks.

    Delivery mechanics (HTTP POST, retries, HMAC signing) are implemented
    in the Outgoing Webhooks component via the deliver_merchant_webhook
    Celery task.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._repo = WebhookDeliveryRepository(session)

    async def create_and_enqueue(
        self,
        *,
        merchant: Merchant,
        transaction_id: uuid.UUID,
        event_type: str,
        payload: dict,
    ) -> Optional[WebhookDelivery]:
        """
        Persist a delivery record and enqueue the delivery Celery task.

        Skips silently if the merchant has no webhook URL configured —
        no record is created and no task is enqueued.

        The payment is already committed before this is called; any failure
        here is logged and swallowed so it never affects the payment outcome.
        The retry sweep task picks up any un-enqueued or failed deliveries.

        Returns the created WebhookDelivery, or None if skipped.
        """
        if not merchant.webhook_url:
            return None

        delivery = await self._repo.create(
            merchant_id=merchant.id,
            transaction_id=transaction_id,
            event_type=event_type,
            payload=payload,
        )
        await self.session.commit()

        try:
            from app.workers.webhook_tasks import deliver_merchant_webhook

            deliver_merchant_webhook.delay(str(delivery.id))
        except Exception:
            logger.exception(
                "Failed to enqueue webhook delivery %s for transaction %s — "
                "retry sweep will pick it up",
                delivery.id,
                transaction_id,
            )

        return delivery

    async def mark_delivered(
        self,
        delivery: WebhookDelivery,
        *,
        response_code: int,
    ) -> WebhookDelivery:
        """Mark a delivery as successfully delivered."""
        return await self._repo.update_delivery_result(
            delivery,
            status=WebhookDeliveryStatus.DELIVERED,
            attempt_count=delivery.attempt_count + 1,
            next_retry_at=None,
            last_response_code=response_code,
            last_error=None,
        )

    async def mark_failed(
        self,
        delivery: WebhookDelivery,
        *,
        error: str,
        response_code: Optional[int] = None,
    ) -> WebhookDelivery:
        """Mark a delivery as permanently failed (max retries exhausted)."""
        return await self._repo.update_delivery_result(
            delivery,
            status=WebhookDeliveryStatus.FAILED,
            attempt_count=delivery.attempt_count + 1,
            next_retry_at=None,
            last_response_code=response_code,
            last_error=error,
        )
