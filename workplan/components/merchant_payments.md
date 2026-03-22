# Merchant Payments Component

## Purpose
Handles the flow where a user pays a merchant from their wallet.
Debits user wallet, credits merchant wallet, and queues a webhook delivery to the merchant.

## Scope
### In Scope
- User-to-merchant payment initiation
- Balance and KYC checks
- Atomic wallet debit/credit + ledger write
- Queuing merchant webhook delivery after payment

### Out of Scope
- Merchant profile management → Merchants component
- Webhook delivery mechanics → Outgoing Webhooks component
- Transfer between users → Transfers component

## Responsibilities
- `MerchantPaymentService`: initiate_payment — orchestrates full payment flow
- Resolve merchant by ID
- Run fraud and KYC checks
- Execute atomic DB transaction
- Create webhook delivery record and enqueue Celery task

## Dependencies
- Wallets component (WalletRepository, lock_for_update)
- Ledger component (LedgerService.post_double_entry)
- Transactions component (TransactionRepository)
- Merchants component (MerchantRepository)
- Fraud component (FraudService.check_merchant_payment)
- Outgoing Webhooks component (create delivery record + enqueue)
- Audit component

## Related Models
- `Transaction`
- `LedgerEntry`
- `Wallet`
- `WebhookDelivery`

## Related Endpoints
- `POST /api/v1/merchants/{merchant_id}/pay` — user pays merchant

## Business Rules
- User must have sufficient wallet balance
- User KYC must be Tier 1 or higher
- Merchant must be active
- Amount must be > 0
- After payment: merchant webhook queued (delivery is best-effort, does not block payment)
- Payment is atomic: wallet moves are all-or-nothing
- User cannot pay themselves (same underlying user_id)

## Security Considerations
- User identity from JWT (cannot spoof payer)
- Merchant ID from URL path — verify merchant exists before processing
- Lock both wallets in consistent UUID order to prevent deadlock
- Idempotency key on payment request to prevent double charge

## Performance Considerations
- Same as Transfers — pure DB, no external calls in hot path
- Webhook enqueue is async (Celery) — does not block payment response

## Reliability Considerations
- Payment is committed to DB before webhook is enqueued
- If webhook enqueue fails, a retry sweep Celery job picks up un-enqueued deliveries
- Webhook delivery failure does not affect payment status (payment is final once committed)

## Testing Expectations
- Integration: payment completes, user debited, merchant credited, ledger entries exist, webhook delivery record created
- Integration: insufficient balance → rejected, no DB writes
- Integration: inactive merchant → rejected
- API: merchant webhook is queued after successful payment
- Failure: webhook delivery fails → retried separately, payment remains complete

## Implementation Notes
- `MerchantPaymentService.initiate_payment(payer_user_id, merchant_id, amount, idempotency_key)`
- After commit: `WebhookDeliveryService.create_and_enqueue(merchant_id, transaction_id, event_type='payment.received', payload=...)`
- Payload structure: `{ event: 'payment.received', transaction_reference: ..., amount: ..., payer_reference: ..., timestamp: ... }`

## Status
complete

## Pending Tasks
- None

## Completion Notes
- `app/models/webhook_delivery.py` — `WebhookDeliveryStatus` enum (pending/delivered/failed); `WebhookDelivery` model with `TimestampMixin` (no soft-delete — audit record); `merchant_id` + `transaction_id` FKs (RESTRICT), `event_type`, `payload` (JSONB), `status`, `attempt_count`, `next_retry_at` (for retry sweep), `last_response_code`, `last_error`; created here as prerequisite for this component
- `alembic/versions/i9j0k1l2m3n4_create_webhook_deliveries_table.py` — creates `webhookdeliverystatus` PG enum; `webhook_deliveries` table with 4 indexes (merchant_id, transaction_id, status, composite retry sweep index); chains from `h8i9j0k1l2m3`
- `app/repositories/webhook_delivery.py` — `WebhookDeliveryRepository`: `create`, `get_by_id`, `get_pending_for_retry` (retry sweep query), `update_delivery_result`
- `app/services/webhook_delivery.py` — `WebhookDeliveryService.create_and_enqueue`: skips silently if no webhook URL; creates delivery record → commits → enqueues `deliver_merchant_webhook.delay`; Celery enqueue errors swallowed (retry sweep recovers)
- `app/workers/webhook_tasks.py` — `deliver_merchant_webhook` Celery task: idempotent guard (skip if already DELIVERED); fetches merchant for URL + secret; builds JSON payload; HMAC-SHA256 signs with `X-PayCore-Signature: sha256=...` header; httpx sync POST (10s timeout, no redirects); on success: marks DELIVERED; on failure: schedules next retry via `next_retry_at` using `_RETRY_DELAYS_MINUTES`; after MAX_ATTEMPTS (6): marks FAILED
- `app/workers/celery_app.py` — `webhook_tasks` added to `include`
- `app/schemas/merchant_payment.py` — `MerchantPaymentRequest` (amount gt=0 decimal_places=2, optional idempotency_key)
- `app/services/merchant_payment.py` — `MerchantPaymentService.initiate_payment`: merchant resolution + active guard → self-payment guard → payer/merchant wallet resolution → idempotency early-return → `FraudService.check_merchant_payment` → lock wallets (consistent UUID order) → balance check → write MERCHANT_PAYMENT transaction (COMPLETED) + double-entry ledger + update balances → commit → post-commit: `maybe_flag_merchant_payment` + `WebhookDeliveryService.create_and_enqueue` (wrapped in try/except)
- `app/api/v1/merchants.py` — `POST /merchants/{merchant_id}/pay` (201) added; `merchant_id` typed as `uuid.UUID` (FastAPI auto-validates); response uses `TransactionOut`
- `app/models/__init__.py` + `alembic/env.py` — `WebhookDelivery` registered
- `tests/api/test_merchant_payment_api.py` — 14 API tests: unauthenticated (401), zero amount (422), invalid UUID path (422), merchant not found (404), inactive merchant (403), self-payment (422 SELF_PAYMENT), tier-0 payer (403), insufficient balance (422 + no balance changes), success (201 + balance delta + correct type/status), two ledger entries (DEBIT payer / CREDIT merchant), webhook delivery record created with correct payload + task enqueued, no webhook URL → no delivery record + task not called, idempotency (same key → same txn, no double charge), inactive payer wallet (403)
