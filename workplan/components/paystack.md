# Paystack Component

## Purpose
External payment rail integration. Handles wallet funding initialization, payment verification,
incoming webhook processing, and payout transfer initiation.

## Scope
### In Scope
- `PaystackClient`: async HTTP wrapper for Paystack API
- Wallet funding: initialize transaction, return payment URL
- Payment verification (on-demand and via webhook)
- Incoming webhook: receive, verify signature, enqueue processing task
- Payout: create transfer recipient, initiate transfer

### Out of Scope
- Wallet balance updates → done by LedgerService/WalletService within same transaction
- Transaction record state management → Transactions component
- Withdrawal request flow → Withdrawals component orchestrates payout

## Responsibilities
- `PaystackClient`: initialize_transaction, verify_transaction, create_transfer_recipient, initiate_transfer, verify_transfer
- `PaystackWebhookService`: verify_signature, process_charge_success, process_transfer_result
- `WalletFundingService`: initiate_funding — creates pending transaction, calls Paystack initialize
- Webhook endpoint: verify → enqueue Celery task → return 200 immediately

## Dependencies
- Wallets component (credit on successful funding)
- Ledger component (write funding entries)
- Transactions component (create/update transaction record)
- Workers component (enqueue webhook processing tasks)
- Audit component

## Related Models
- `Transaction`
- `LedgerEntry`
- `Wallet`

## Related Endpoints
- `POST /api/v1/wallets/fund` — initiate wallet funding (user-facing)
- `POST /api/v1/webhooks/paystack` — Paystack webhook receiver (no auth, signature-verified)

## Business Rules
- Funding only allowed if user has active wallet
- Minimum funding amount: 100 NGN
- Webhook MUST be signature-verified before any processing
- Idempotency: check `provider_reference` in transactions before crediting — never double-credit
- Webhook endpoint must return 200 immediately after accepting (processing is async)
- Reconciliation job handles webhooks that were never received

## Security Considerations
- `POST /api/v1/webhooks/paystack` is public (no JWT) but signature-verified
- Signature: HMAC SHA512 of raw request body using `PAYSTACK_WEBHOOK_SECRET`
- Any request with invalid or missing signature → 401 with no processing
- `provider_reference` uniqueness check before wallet credit
- `PAYSTACK_SECRET_KEY` loaded from environment — never hardcoded

## Performance Considerations
- Webhook endpoint: accept immediately, enqueue Celery task, return 200
- Never process webhook synchronously in the HTTP request cycle
- `PaystackClient` uses httpx async client with 10-second timeout

## Reliability Considerations
- Paystack API down: funding init fails with 503 — no transaction record created
- Webhook not received: reconciliation job (every 30 min) queries pending transactions > 30min old, calls Paystack verify endpoint
- Transfer failure webhook: triggers reversal handler in Withdrawals component

## Testing Expectations
- Unit: signature verification logic
- Unit: idempotency check prevents double credit
- Integration: funding init → mock Paystack → pending transaction created
- Integration: charge.success webhook → wallet credited, transaction completed, ledger entries written
- Integration: duplicate webhook event → idempotency prevents double credit
- Failure: invalid signature on webhook → 401

## Implementation Notes
- `PaystackClient` in `app/integrations/paystack.py` — all HTTP calls here
- `PaystackWebhookService.process_charge_success(reference, amount)`: verify idempotency, then call WalletService.credit_wallet and LedgerService.post_double_entry inside one transaction
- Celery task: `tasks.process_paystack_webhook(event_type, data_dict)`
- Webhook endpoint raw body must be read before parsing JSON (signature verification needs raw bytes)

## Status
complete

## Pending Tasks
- None

## Completion Notes
- `app/integrations/__init__.py` — integrations package created
- `app/integrations/paystack.py` — `PaystackClient`: async httpx wrapper; `initialize_transaction`, `verify_transaction`, `create_transfer_recipient`, `initiate_transfer`, `verify_transfer`; all amounts in kobo; raises `ExternalServiceError` on non-2xx or network error
- `app/schemas/wallet_funding.py` — `WalletFundingRequest` (amount gt=0 decimal_places=2, optional idempotency_key); `WalletFundingOut` (transaction_id, reference, payment_url, amount, currency)
- `app/services/wallet_funding.py` — `WalletFundingService.initiate_funding`: wallet active guard → minimum 100 NGN guard → idempotency early-return → call Paystack FIRST (no DB record if Paystack down) → create PENDING FUNDING txn with provider_reference + payment_url in extra_data → commit
- `app/services/paystack_webhook.py` — `PaystackWebhookService`: static `verify_signature` (HMAC-SHA512, returns False if secret unconfigured); async `process_charge_success` (idempotency via provider_reference, PENDING→PROCESSING→COMPLETED, wallet credit, single CREDIT ledger entry, commit); async `process_transfer_result` (stub log — Withdrawals component)
- `app/workers/paystack_tasks.py` — `process_paystack_webhook` Celery task (name=`paystack.process_webhook`, autoretry 3× with backoff); uses `asyncio.run()` + `_dispatch()` to call `PaystackWebhookService` async methods with a fresh `AsyncSession`; keeps processing logic testable under async test infrastructure
- `app/api/v1/webhooks.py` — new router: `POST /webhooks/paystack` (public, no JWT); reads raw body bytes first; verifies `X-Paystack-Signature`; enqueues Celery task; returns 200 immediately; 401 on bad/missing signature
- `app/api/v1/wallets.py` — `POST /wallets/fund` (201) added; returns `WalletFundingOut`
- `app/api/v1/router.py` — `webhooks.router` included
- `app/workers/celery_app.py` — `app.workers.paystack_tasks` added to `include`
- `tests/api/test_paystack_api.py` — 14 tests: unauthenticated (401), zero amount (422), below minimum (422 BELOW_MINIMUM_AMOUNT), inactive wallet (403), no wallet (403), Paystack down (503 + no txn row), success (201 + PENDING txn + payment_url), idempotency (same txn, original URL); webhook: missing sig (401), invalid sig (401), charge.success accepted (200 + task enqueued), unknown event (200), malformed JSON (422); integration: charge.success credits wallet + COMPLETED + ledger entry, idempotent duplicate skip, orphan reference no-op; unit: verify_signature valid/invalid/no-secret
