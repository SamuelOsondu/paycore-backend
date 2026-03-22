# Fraud Component

## Purpose
Enforces risk controls and business limits before money movements are executed.
Runs synchronous blocking checks within the transaction initiation flow.

## Scope
### In Scope
- KYC tier limit enforcement (single transaction amount limits)
- Daily transfer volume checks
- Rapid repeat transfer detection (same sender + recipient + amount within 60s)
- Merchant payment anomaly detection
- Async risk flagging for pattern-based anomalies

### Out of Scope
- KYC tier management → KYC component
- Balance check (owned by individual service layers)
- Advanced ML-based fraud → not in scope

## Responsibilities
- `FraudService`: check_transfer, check_merchant_payment, check_withdrawal
- Each check method returns `pass` or raises `FraudRuleViolationError` with reason
- Async risk flag: `flag_transaction_risk(transaction_id, reason)` — Celery task

## Dependencies
- Wallets component (balance, wallet_id)
- Transactions component (query daily volume, recent transfer history)
- Users component (KYC tier)

## Related Models
- `Transaction` (queried for daily volume and recent patterns)

## Related Endpoints
- No direct endpoints (internal service)
- Admin can view flagged transactions via `GET /api/v1/admin/transactions?flagged=true`

## Business Rules

### Synchronous checks (block transaction if violated):
1. **KYC tier single-transaction limit:**
   - Tier 0: no transfers allowed
   - Tier 1: max 50,000 NGN per transaction
   - Tier 2: max 500,000 NGN per transaction
2. **KYC tier daily volume limit:**
   - Tier 1: max 50,000 NGN total outgoing per day
   - Tier 2: max 500,000 NGN total outgoing per day
3. **Duplicate transfer detection:**
   - Same sender_wallet_id + recipient_wallet_id + amount within 60 seconds → reject
4. **Withdrawal limits:**
   - Same as transfer limits; Tier 2 required

### Async flags (do not block, flag for admin review):
5. Large merchant payment from Tier 1 user (> 100,000 NGN single payment)
6. Rapid succession: > 5 transfers to different recipients within 10 minutes

## Security Considerations
- Limits stored as configurable env variables — not hardcoded
- Fraud checks run before any ledger write — no partial state if rejected
- Admin view of flagged transactions must require `admin` role

## Performance Considerations
- Daily volume check: SUM query on `transactions` filtered by `initiated_by_user_id`, `type`, `created_at >= today`, `status=completed`
- Needs index on `(initiated_by_user_id, type, status, created_at)` for this query
- Recent duplicate check: simple query on last 60 seconds — very fast

## Reliability Considerations
- Synchronous checks are in-process — no external calls
- Async flagging via Celery is best-effort; does not affect transaction outcome
- If fraud check raises an exception (DB error), escalate to 500 — do not bypass check

## Testing Expectations
- Unit: each rule fires correctly at its boundary values
- Unit: daily volume correctly includes today's completed transactions only
- Unit: duplicate detection respects 60-second window
- Integration: transfer above KYC limit → rejected with correct error
- Integration: transfer that exceeds daily volume → rejected

## Implementation Notes
- `FraudService.check_transfer(sender_user, sender_wallet, recipient_wallet_id, amount)`
- Raises `KYCLimitError`, `DailyLimitError`, `DuplicateTransferError`
- Limits loaded from settings at service instantiation: `settings.KYC_TIER1_DAILY_LIMIT`
- Async flagging: `tasks.flag_transaction_risk.delay(transaction_id, reason)` called post-commit

## Status
complete

## Pending Tasks
- None

## Completion Notes
- `app/models/transaction.py` — Added `risk_flagged: bool` (default False) and `risk_flag_reason: Optional[str]` fields for admin review flag persistence
- `app/core/config.py` — Added `KYC_TIER1_SINGLE_LIMIT`, `KYC_TIER2_SINGLE_LIMIT`, `FRAUD_DUPLICATE_WINDOW_SECONDS`, `FRAUD_RAPID_TRANSFER_COUNT`, `FRAUD_RAPID_TRANSFER_WINDOW_SECONDS`, `FRAUD_MERCHANT_PAYMENT_FLAG_THRESHOLD` settings
- `alembic/versions/f6a7b8c9d0e1_add_fraud_fields_and_index.py` — Adds `risk_flagged`/`risk_flag_reason` columns to transactions; adds composite index `ix_transactions_fraud_check` on `(initiated_by_user_id, type, status, created_at)` for daily volume queries; chains from `e5f6a7b8c9d0`
- `app/workers/celery_app.py` — Celery app instance (Redis broker/backend, JSON serialization, UTC, late acks, single prefetch)
- `app/workers/fraud_tasks.py` — `flag_transaction_risk` task: idempotent, auto-retries x3 with backoff, uses `SyncSessionLocal`, marks `risk_flagged=True` on the transaction
- `app/repositories/fraud.py` — `FraudRepository` with `get_daily_outgoing_sum`, `count_recent_transfers`, `count_distinct_recipients_recently` — all analytical queries on the transactions table
- `app/services/fraud.py` — `FraudService`: sync check methods (`check_transfer`, `check_merchant_payment`, `check_withdrawal`) raise `KYCTierError`/`DailyLimitError`/`DuplicateTransferError`; post-commit flag methods (`maybe_flag_merchant_payment`, `maybe_flag_rapid_transfers`) enqueue Celery tasks without blocking
- Exceptions (`KYCTierError`, `DailyLimitError`, `DuplicateTransferError`) were already defined in `app/core/exceptions.py`
- `tests/unit/test_fraud.py` — 22 unit tests: tier 0/1/2 single limits, daily limit enforcement (boundary), daily limit filters completed-only + today-only, duplicate window (inside/outside), withdrawal tier requirement, merchant payment tier check, async flag boundary tests (threshold, tier 2 exempt, rapid transfer count vs. limit)
