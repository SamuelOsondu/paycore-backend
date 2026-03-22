"""
Inbound webhook receiver endpoints.

POST /webhooks/paystack is public — no JWT required.
Paystack authenticates via HMAC-SHA512 of the raw request body.

Design
------
1. Read raw body bytes before any JSON parsing (signature needs raw bytes).
2. Verify X-Paystack-Signature.  Reject with 401 on failure.
3. Parse JSON payload.
4. Enqueue Celery task for async processing.
5. Return 200 immediately — Paystack expects a fast acknowledgement and will
   retry if it does not receive one within ~30 seconds.
"""

import json
import logging

from fastapi import APIRouter, Request

from app.core.exceptions import UnauthorizedError, ValidationError
from app.core.response import success_response
from app.services.paystack_webhook import PaystackWebhookService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


@router.post(
    "/paystack",
    status_code=200,
    summary="Paystack inbound webhook receiver",
)
async def receive_paystack_webhook(request: Request) -> dict:
    """
    Receive, verify, and enqueue a Paystack webhook event.

    - Reads raw body bytes (required for correct HMAC verification).
    - Verifies ``X-Paystack-Signature`` against HMAC-SHA512 of the raw body
      using ``PAYSTACK_WEBHOOK_SECRET``.  Any missing or invalid signature
      returns 401 and no processing occurs.
    - Enqueues the event to Celery; if enqueuing fails the 200 is still
      returned (prevents infinite Paystack retries for a Redis hiccup).
    """
    raw_body = await request.body()
    signature = request.headers.get("X-Paystack-Signature", "")

    if not PaystackWebhookService.verify_signature(raw_body, signature):
        raise UnauthorizedError("Invalid or missing webhook signature.")

    try:
        event_data = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ValidationError("Malformed JSON body.", error_code="INVALID_JSON")

    event_type: str = event_data.get("event", "")
    data: dict = event_data.get("data", {})

    from app.workers.paystack_tasks import process_paystack_webhook

    try:
        process_paystack_webhook.delay(event_type, data)
    except Exception:
        logger.exception(
            "Failed to enqueue paystack webhook task for event '%s'", event_type
        )

    return success_response(message="Webhook accepted.")
