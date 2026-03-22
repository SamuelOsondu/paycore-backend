# Wallets Component

## Purpose
Owns the Wallet entity. Manages wallet creation, balance reads, and transaction history.
The wallet is the financial container for every user and merchant on the platform.

## Scope
### In Scope
- Wallet model definition
- Wallet creation (one per user, created on registration)
- Wallet balance read
- Transaction history (paginated ledger/transaction view)
- Wallet statement endpoint

### Out of Scope
- Balance mutation → handled by Ledger/Transfer/Paystack components only
- Wallet funding flow → Paystack component
- Transfer logic → Transfers component

## Responsibilities
- `WalletRepository`: create, get_by_id, get_by_user_id, lock_for_update (SELECT FOR UPDATE)
- `WalletService`: create_wallet, get_wallet, get_balance, get_statement
- Enforce: one wallet per user (unique constraint + service check)

## Dependencies
- Users component (user_id FK)
- Transactions component (for statement endpoint)

## Related Models
- `Wallet`

## Related Endpoints
- `GET /api/v1/wallets/me` — get own wallet info and balance
- `GET /api/v1/wallets/me/transactions` — paginated transaction history

## Business Rules
- One wallet per user (UNIQUE constraint on `user_id`)
- Wallet is created automatically on user registration — not by user request
- Balance is a maintained field; never computed from ledger in hot path
- `balance >= 0` constraint enforced at DB level (CHECK constraint)
- Wallet can be deactivated by admin (blocks all operations)
- Currency is NGN for MVP

## Security Considerations
- Users can only view their own wallet
- `WalletRepository.lock_for_update` must only be called within an active DB transaction
- Balance must never be updated outside a transaction that also writes ledger entries

## Performance Considerations
- Balance reads are O(1) — no aggregation query
- Transaction history: paginated (default 20, max 100), indexed by `created_at` DESC

## Reliability Considerations
- `lock_for_update` prevents race condition in concurrent transfer/payment requests
- Wallet creation inside user registration transaction — atomic

## Testing Expectations
- Unit: one-wallet-per-user enforcement
- API: authenticated user gets correct wallet data
- API: unauthenticated access returns 401
- API: user cannot access another user's wallet

## Implementation Notes
- `WalletRepository.lock_for_update(wallet_id, session)` — wraps `SELECT ... FOR UPDATE`
- Balance updates NEVER go through `WalletRepository.update_balance` directly from a router
- Statement endpoint: join transactions where `source_wallet_id` or `destination_wallet_id` matches

## Status
complete

## Pending Tasks
- None

## Completion Notes
- `app/models/wallet.py` — Wallet model with SoftDeleteMixin, TimestampMixin, CHECK(balance >= 0), UNIQUE(user_id)
- `alembic/versions/c3d4e5f6a7b8_create_wallets_table.py` — migration with check constraint, unique constraint, two indexes
- `app/repositories/wallet.py` — WalletRepository: create, get_by_id, get_by_user_id, lock_for_update (SELECT FOR UPDATE), update_balance, set_active, soft_delete
- `app/schemas/wallet.py` — WalletOut (id, user_id, currency, balance as Decimal, is_active, created_at)
- `app/services/wallet.py` — WalletService: create_wallet, get_wallet, get_balance, get_statement (stub, TODO wired for Transactions component), assert_wallet_active
- `app/api/v1/wallets.py` — GET /wallets/me, GET /wallets/me/transactions (paginated stub, limit/offset query params)
- `app/api/v1/router.py` — wallets router included
- `app/models/__init__.py` + `alembic/env.py` — Wallet model registered
- `app/services/auth.py` — wallet creation wired atomically into register() before commit; TODO comment removed
- `tests/conftest.py` — test_wallet fixture added (zero-balance NGN wallet for test_user)
- `tests/unit/test_wallets.py` — 11 unit tests covering create, get, balance, statement, assert_active
- `tests/api/test_wallets_api.py` — 18 API tests covering both endpoints, envelope shape, pagination, deactivated wallet, no-wallet 404, and end-to-end register→wallet flow
- `get_statement` returns empty paginated list until Transactions component is built; the TODO comment is in WalletService.get_statement
- `lock_for_update` docstring clearly documents the caller contract (must hold row lock, must write ledger entries in same tx)
