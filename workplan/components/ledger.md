# Ledger Component

## Purpose
The double-entry accounting engine. Every completed money movement produces exactly two
ledger entries (debit + credit). The ledger is the audit source of truth for all balance history.

## Scope
### In Scope
- `LedgerEntry` model
- Writing debit/credit entry pairs within a DB transaction
- `balance_after` snapshot on each entry
- Ledger read for reconciliation and audit

### Out of Scope
- Initiating money movement → Transfer, Paystack, Merchant Payments, Withdrawals
- Wallet balance field updates → done by callers inside same transaction
- Transaction record management → Transactions component

## Responsibilities
- `LedgerRepository`: create_entry, get_by_transaction, get_by_wallet (paginated)
- `LedgerService`: post_double_entry(transaction_id, debit_wallet_id, credit_wallet_id, amount, debit_balance_after, credit_balance_after)
- Enforce: exactly 2 entries per transaction (debit + credit)
- Enforce: no updates or deletes on ledger entries ever

## Dependencies
- Wallets component (wallet_id FK)
- Transactions component (transaction_id FK)

## Related Models
- `LedgerEntry`

## Related Endpoints
- No direct user-facing endpoints (internal component)
- `GET /api/v1/admin/ledger` — admin only (paginated ledger inspection)

## Business Rules
- Every money movement must produce exactly one debit entry and one credit entry
- `balance_after` = wallet balance after the entry is applied (snapshot, not computed)
- Ledger entries are immutable once written
- Reversals create new entries (debit what was credited, credit what was debited)
- All entries written inside the same DB transaction as the wallet balance update

## Security Considerations
- Ledger entries are internal; not exposed to regular users in raw form
- Accessible to admin via read-only endpoint only
- No update or delete endpoints exist or should be created

## Performance Considerations
- Indexed by `transaction_id` and `wallet_id`
- `created_at` index for time-range admin queries

## Reliability Considerations
- `post_double_entry` must be called inside an active DB transaction (not standalone)
- If either entry write fails, entire transaction rolls back
- Caller is responsible for ensuring wallet balance updates are in the same transaction

## Testing Expectations
- Unit: `post_double_entry` creates exactly 2 entries with correct types and amounts
- Unit: balance_after is correctly populated
- Integration: after a transfer, verify both debit and credit entries exist in DB
- Failure: partial write (simulated) rolls back both entries

## Implementation Notes
- `LedgerService.post_double_entry` is not a transaction manager — it does not call `session.begin()`
- It must be called from within an existing transaction scope (from Transfer/PaystackService etc.)
- Signature: `post_double_entry(session, transaction_id, debit_wallet_id, credit_wallet_id, amount, debit_balance_after, credit_balance_after)`
- `balance_after` values must be computed by the caller (who already holds the locked wallet rows)

## Status
complete

## Pending Tasks
- None

## Completion Notes
- `app/models/ledger_entry.py` — `EntryType` enum (DEBIT/CREDIT); `LedgerEntry` model with only `created_at` (no TimestampMixin, no SoftDeleteMixin — immutable); `entry_type` mapped via `PgEnum(create_type=False)` (type created in migration); `balance_after` NUMERIC(20,2) snapshot
- `alembic/versions/e5f6a7b8c9d0_create_ledger_entries_table.py` — creates `entrytype` PG enum; `ledger_entries` table with FKs to `transactions.id` and `wallets.id` (both RESTRICT); 3 indexes: transaction_id, wallet_id, created_at
- `app/repositories/ledger.py` — `create_entry`, `get_by_transaction` (ordered ASC), `get_by_wallet` (paginated, ordered DESC); no update or delete methods — entries are immutable
- `app/schemas/ledger.py` — `LedgerEntryOut` with `use_enum_values=True`; admin-facing only
- `app/services/ledger.py` — `LedgerService.post_double_entry(*, transaction_id, debit_wallet_id, credit_wallet_id, amount, currency, debit_balance_after, credit_balance_after)` writes exactly one DEBIT + one CREDIT entry; service is NOT a transaction manager — caller owns the session scope
- `app/models/__init__.py` + `alembic/env.py` — `LedgerEntry` and `EntryType` registered
- `tests/unit/test_ledger.py` — 15 unit tests: double entry creates 2 entries, correct types, correct wallet IDs, correct transaction ID, amount propagation, balance_after snapshots, currency propagation, get_by_transaction (both entries, empty, ASC order), get_by_wallet (own entries, excludes others, empty, pagination), immutability contract (no update/delete methods)
