# Withdrawals Component

## Purpose
Handles bank account management and the outflow money movement from user wallets to external bank accounts.
Withdrawal processing is asynchronous — initiated in request, completed by a Celery worker.

## Scope
### In Scope
- Bank account addition (and optional Paystack verify)
- Setting default bank account
- Withdrawal request validation and initiation
- Withdrawal processing via Celery (Paystack Transfer API in sandbox)
- Success callback handling: finalize debit, complete transaction
- Failure callback handling: reverse balance hold, mark failed

### Out of Scope
- Paystack HTTP client → Paystack component
- Ledger writes → LedgerService
- Wallet balance locking → WalletRepository

## Responsibilities
- `BankAccountRepository`: create, get_by_user_id, get_default
- `WithdrawalService`: add_bank_account, initiate_withdrawal, process_payout_success, process_payout_failure
- `BankAccountVerificationService`: call Paystack `bank/resolve` to verify account name
- Celery task: `process_withdrawal(withdrawal_transaction_id)` — calls Paystack Transfer

## Dependencies
- Wallets component (balance check, lock_for_update)
- Ledger component (write withdrawal debit entry on success)
- Transactions component (create withdrawal transaction, update status)
- Paystack component (PaystackClient for transfer recipient + initiate transfer)
- Fraud component (check withdrawal limits)
- Audit component

## Related Models
- `BankAccount`
- `Transaction` (type=withdrawal)
- `LedgerEntry`

## Related Endpoints
- `POST /api/v1/bank-accounts` — add bank account
- `GET /api/v1/bank-accounts` — list own bank accounts
- `DELETE /api/v1/bank-accounts/{id}` — remove bank account
- `POST /api/v1/withdrawals` — initiate withdrawal request
- `GET /api/v1/withdrawals/{reference}` — check withdrawal status

## Business Rules
- User must have KYC Tier 2 to initiate a withdrawal
- Withdrawal amount must not exceed wallet balance
- Withdrawal amount must not exceed daily withdrawal limit for KYC tier
- Balance is "held" (deducted) when withdrawal is initiated; returned if payout fails
- Only one active pending withdrawal at a time per user (prevent double withdrawal)
- Bank account must exist and belong to the requesting user
- Paystack transfer recipient must be created before initiating transfer (first withdrawal with account creates it)

## Security Considerations
- User identity from JWT — bank account must belong to the authenticated user
- Withdrawal only allowed from user's own wallet (never from request body)
- `paystack_recipient_code` stored on bank_account — not exposed in user API response
- Balance hold and release must be atomic

## Performance Considerations
- Withdrawal initiation is synchronous (fast DB writes only, no external calls)
- Paystack Transfer API call is in Celery worker (async, non-blocking for user)
- `process_withdrawal` Celery task: autoretry on network errors (max 3 retries, 60s delay)

## Reliability Considerations
- Balance hold on initiation prevents double-spend while withdrawal is processing
- If Celery worker crashes mid-task: task re-runs; idempotency prevents double transfer
- `transfer.success` webhook from Paystack confirms final state
- `transfer.failed` webhook triggers reversal: credit balance back, mark transaction failed
- Reconciliation job flags withdrawal transactions stuck in `processing` > 1 hour

## Testing Expectations
- Integration: initiation → balance held, transaction pending, Celery task enqueued
- Integration (mocked Paystack): worker processes → transfer.success → balance finalized, ledger debit written
- Integration: worker failure path → balance returned, transaction failed
- Integration: KYC Tier < 2 → rejected
- Integration: insufficient balance → rejected
- Edge: duplicate withdrawal initiation → rejected (already pending)

## Implementation Notes
- `initiate_withdrawal`: lock wallet → check balance → create pending transaction → deduct from balance (hold) → enqueue Celery task
- Celery `process_withdrawal(transaction_id)`: get transaction → call PaystackClient.initiate_transfer → update transaction to `processing`
- Paystack `transfer.success`/`transfer.failed` webhooks handled in PaystackWebhookService, which calls `WithdrawalService.process_payout_success/failure`
- Balance hold: immediate deduction from `wallet.balance` on initiation; if payout fails, credit back

## Status
complete

## Pending Tasks
- None

## Completion Notes
- `BankAccount` model with `SoftDeleteMixin`; `paystack_recipient_code` nullable, excluded from API response
- Alembic migration chained from Paystack migration; indexes on user_id, deleted_at, composite (user_id, is_default)
- `BankAccountRepository`: create, get_by_id, get_by_user_id, get_default, count_by_user, unset_all_defaults, set_default, set_recipient_code, soft_delete (promotes next if was default)
- `BankAccountVerificationService.verify_account`: graceful — returns None on any error; verified Paystack name overrides client-supplied name
- `WithdrawalService`: add_bank_account (first account auto-default), list_bank_accounts, remove_bank_account (ownership + active withdrawal guard), initiate_withdrawal (KYC Tier 2, single active guard, balance hold, PENDING txn, Celery dispatch), execute_payout (recipient_code lazy-cache, PENDING→PROCESSING, Paystack transfer), process_payout_success (DEBIT ledger, COMPLETED), process_payout_failure (balance restored, FAILED)
- Transfer idempotency via `txn.reference` as Paystack transfer reference
- `process_withdrawal` Celery task: `asyncio.run(_dispatch(...))`, lazy imports, autoretry max 3 × 60s backoff
- `PaystackWebhookService.process_transfer_result` delegates to `WithdrawalService`
- 5 endpoints in `app/api/v1/withdrawals.py`: POST /bank-accounts, GET /bank-accounts, DELETE /bank-accounts/{id}, POST /withdrawals, GET /withdrawals/{reference}
- 21 tests in `tests/api/test_withdrawal_api.py` covering all paths, idempotency, balance invariants
