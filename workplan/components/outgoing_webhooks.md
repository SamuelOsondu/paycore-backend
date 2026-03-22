# Outgoing Webhooks Component

## Purpose
Delivers webhook notifications to merchants when payment events occur on their account.
Handles delivery attempts, retries with exponential backoff, and failure tracking.

## Scope
### In Scope
- `WebhookDelivery` model
- Creating delivery records after merchant payments
- Celery task for HTTP delivery to merchant webhook URL
- Retry logic with exponential backoff
- Signature of outgoing payload (HMAC SHA256)
- Delivery status tracking

### Out of Scope
- Incoming webhooks from Paystack → Paystack component
- Merchant profile / webhook URL configuration → Merchants component
- Payment triggering → Merchant Payments component

## Responsibilities
- `WebhookDeliveryRepository`: create, get_pending_for_retry, update_delivery_result
- `WebhookDeliveryService`: create_and_enqueue, mark_delivered, mark_failed
- Celery task: `deliver_merchant_webhook(delivery_id)` — HTTP POST to merchant URL
- Retry sweep job: `retry_pending_webhooks` — picks up due retries every 5 minutes

## Dependencies
- Merchants component (MerchantRepository — get webhook URL and secret)
- Transactions component (transaction_id FK on delivery record)

## Related Models
- `WebhookDelivery`

## Related Endpoints
- No user-facing endpoints
- `GET /api/v1/admin/webhook-deliveries` — admin view of all delivery records (paginated)

## Business Rules
- Webhook delivery is best-effort — payment is final regardless of delivery outcome
- Retry schedule: attempt 1 immediately, then 2min, 4min, 8min, 16min, 32min (6 total attempts)
- After 6 failed attempts: mark `status=failed`, stop retrying, emit audit log
- If merchant has no webhook URL configured: skip delivery silently
- Each delivery signed with HMAC SHA256 of payload using merchant's `webhook_secret`
- Signature sent as `X-PayCore-Signature: sha256={hex_digest}` header

## Security Considerations
- HMAC signature on every outgoing payload (merchant can verify)
- Merchant webhook secret is stored plaintext (they need to verify; it is their credential)
- HTTP timeout: 10 seconds per delivery attempt
- Do not follow redirects

## Performance Considerations
- All delivery is async via Celery — never blocks user request
- Retry sweep runs every 5 minutes via Celery Beat
- Large number of undelivered webhooks: retry sweep uses `status=pending` + `next_retry_at <= now` index

## Reliability Considerations
- Delivery task is idempotent: if task runs twice for same delivery, only one HTTP POST goes out
- Guard: check delivery status before making HTTP call; skip if already `delivered`
- Failed delivery does not trigger payment reversal
- `last_response_code` and `last_error` stored per attempt for debugging

## Testing Expectations
- Unit: HMAC signature generation correctness
- Integration: delivery task posts to merchant URL with correct payload and signature
- Integration: merchant URL returns 500 → retry is scheduled
- Integration: after max retries → delivery marked failed, audit log emitted
- Integration: merchant with no webhook URL → delivery record skipped

## Implementation Notes
- `deliver_merchant_webhook(delivery_id)`: fetch delivery → check not already delivered → build payload → sign → POST → update record
- HTTP client: `httpx` sync client (Celery task is sync)
- Payload structure: `{ event: str, data: { transaction_reference: str, amount: Decimal, ... }, timestamp: str }`
- `retry_pending_webhooks` beat task: query `WHERE status='pending' AND next_retry_at <= now()`, enqueue each

## Status
complete

## Pending Tasks
- None

## Completion Notes
- `WebhookDelivery` model, `WebhookDeliveryRepository`, and `deliver_merchant_webhook` Celery task were implemented in the Merchant Payments component; fully wired here
- `WebhookDeliveryRepository` extended with `list_all` (ordered newest-first, paginated) and `count_all` for admin endpoint
- `WebhookDeliveryService` extended with `mark_delivered` and `mark_failed` helpers wrapping `update_delivery_result`
- `retry_pending_webhooks` beat task added to `webhook_tasks.py` — sync inline SQL query via `SyncSessionLocal`, picks up PENDING deliveries where `next_retry_at` is set and elapsed, enqueues `deliver_merchant_webhook.delay` for each (idempotent)
- Celery Beat schedule wired in `celery_app.py` — `timedelta(minutes=5)` interval
- `WebhookDeliveryOut` schema in `app/schemas/webhook_delivery.py` — excludes `payload` (can be large, not needed for status monitoring)
- `GET /api/v1/admin/webhook-deliveries` endpoint added to admin router — paginated, admin-only, newest-first
- No new Alembic migration needed — `webhook_deliveries` table created in Merchant Payments migration
- 20 tests in `tests/api/test_outgoing_webhooks.py`: 4 unit (HMAC), 7 task (deliver_merchant_webhook + retry_pending_webhooks), 4 service, 5 admin endpoint
