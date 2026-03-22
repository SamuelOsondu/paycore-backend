import hashlib
import hmac
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select

from app.core.database import SyncSessionLocal
from app.models.webhook_delivery import WebhookDelivery, WebhookDeliveryStatus
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

# Retry schedule: 6 attempts total with exponential backoff (minutes)
_RETRY_DELAYS_MINUTES = [0, 2, 4, 8, 16, 32]
MAX_ATTEMPTS = len(_RETRY_DELAYS_MINUTES)
HTTP_TIMEOUT_SECONDS = 10


def _sign_payload(payload_bytes: bytes, secret: str) -> str:
    """Return HMAC-SHA256 hex digest for the outgoing webhook signature header."""
    return hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()


@celery_app.task(
    name="webhooks.deliver_merchant_webhook",
    bind=True,
    max_retries=0,  # Retry scheduling handled manually via next_retry_at
)
def deliver_merchant_webhook(self, delivery_id: str) -> None:
    """
    Deliver a webhook payload to the merchant's configured URL.

    Idempotent — if the delivery record is already DELIVERED, this is a no-op.
    On success: marks delivery DELIVERED.
    On failure: increments attempt_count, schedules next retry via next_retry_at,
    or marks FAILED if MAX_ATTEMPTS is exhausted.
    """
    with SyncSessionLocal() as session:
        delivery: WebhookDelivery | None = session.get(
            WebhookDelivery, uuid.UUID(delivery_id)
        )
        if delivery is None:
            logger.warning(
                "deliver_merchant_webhook: delivery %s not found", delivery_id
            )
            return
        if delivery.status == WebhookDeliveryStatus.DELIVERED:
            return  # already delivered — idempotent

        # Re-fetch merchant for webhook URL and secret
        from app.models.merchant import Merchant

        merchant: Merchant | None = session.get(Merchant, delivery.merchant_id)
        if merchant is None or not merchant.webhook_url:
            delivery.status = WebhookDeliveryStatus.FAILED
            delivery.last_error = "Merchant has no webhook URL configured."
            session.commit()
            return

        payload_bytes = json.dumps(delivery.payload, default=str).encode()
        secret = merchant.webhook_secret or ""
        signature = _sign_payload(payload_bytes, secret)

        attempt = delivery.attempt_count + 1
        response_code: int | None = None
        error_msg: str | None = None

        try:
            with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=False) as client:
                resp = client.post(
                    merchant.webhook_url,
                    content=payload_bytes,
                    headers={
                        "Content-Type": "application/json",
                        "X-PayCore-Signature": f"sha256={signature}",
                    },
                )
            response_code = resp.status_code
            if resp.is_success:
                delivery.status = WebhookDeliveryStatus.DELIVERED
                delivery.attempt_count = attempt
                delivery.last_response_code = response_code
                delivery.next_retry_at = None
                session.commit()
                logger.info(
                    "Webhook delivery %s succeeded (HTTP %s)", delivery_id, response_code
                )
                return
            error_msg = f"HTTP {response_code}"
        except Exception as exc:
            error_msg = str(exc)
            logger.warning(
                "Webhook delivery %s attempt %s failed: %s",
                delivery_id,
                attempt,
                error_msg,
            )

        # Delivery failed — schedule retry or mark as exhausted
        if attempt >= MAX_ATTEMPTS:
            delivery.status = WebhookDeliveryStatus.FAILED
            delivery.attempt_count = attempt
            delivery.last_response_code = response_code
            delivery.last_error = error_msg
            delivery.next_retry_at = None
            session.commit()
            logger.error(
                "Webhook delivery %s failed after %s attempts: %s",
                delivery_id,
                attempt,
                error_msg,
            )

            # Audit log — fire-and-forget, never raises
            from app.models.audit_log import ActorType
            from app.services.audit import log_sync
            log_sync(
                session,
                actor_id=None,
                actor_type=ActorType.SYSTEM,
                action="webhook_delivery.failed",
                target_type="webhook_delivery",
                target_id=delivery.id,
                metadata={
                    "merchant_id": str(delivery.merchant_id),
                    "attempts": attempt,
                    "last_error": error_msg,
                },
            )
        else:
            delay_minutes = _RETRY_DELAYS_MINUTES[attempt]
            next_retry = datetime.now(timezone.utc) + timedelta(minutes=delay_minutes)
            delivery.attempt_count = attempt
            delivery.last_response_code = response_code
            delivery.last_error = error_msg
            delivery.next_retry_at = next_retry
            session.commit()


@celery_app.task(name="webhooks.retry_pending_webhooks")
def retry_pending_webhooks() -> None:
    """
    Celery Beat task — runs every 5 minutes.

    Queries for PENDING webhook deliveries whose next_retry_at window has
    elapsed and enqueues a fresh deliver_merchant_webhook task for each.

    Idempotency: deliver_merchant_webhook checks the delivery status before
    making any HTTP call, so double-enqueuing a delivery is safe.
    """
    now = datetime.now(timezone.utc)
    with SyncSessionLocal() as session:
        result = session.execute(
            select(WebhookDelivery.id).where(
                WebhookDelivery.status == WebhookDeliveryStatus.PENDING,
                WebhookDelivery.next_retry_at.isnot(None),
                WebhookDelivery.next_retry_at <= now,
            ).limit(100)
        )
        delivery_ids: list[uuid.UUID] = [row[0] for row in result.all()]

    for delivery_id in delivery_ids:
        deliver_merchant_webhook.delay(str(delivery_id))

    if delivery_ids:
        logger.info(
            "retry_pending_webhooks: enqueued %d deliveries for retry",
            len(delivery_ids),
        )
