# Transactions Component

## Purpose
Owns the Transaction record. Represents every money movement event on the platform.
Transactions are the state-tracked artifacts that map to ledger entries and external provider events.

## Scope
### In Scope
- `Transaction` model definition
- Transaction creation (by other components)
- Status transitions (state machine)
- Transaction lookup by reference, by wallet, by user
- Idempotency key tracking

### Out of Scope
- Triggering money movement → Transfer, Paystack, Merchants, Withdrawals
- Ledger entry writing → Ledger component
- Business rule enforcement → respective flow components

## Responsibilities
- `TransactionRepository`: create, get_by_id, get_by_reference, get_by_provider_reference, get_by_wallet (paginated), update_status
- `TransactionService`: helper methods for creating transactions with correct defaults
- Enforce allowed status transitions (state machine logic)

## Dependencies
- Wallets component (source/destination wallet FK)
- Users component (initiated_by_user_id FK)

## Related Models
- `Transaction`

## Related Endpoints
- `GET /api/v1/transactions/{reference}` — get single transaction by reference
- `GET /api/v1/transactions` — list own transactions (paginated)

## Business Rules
- Transaction `reference` is unique, platform-generated UUID4 string
- `idempotency_key` is unique when provided — duplicate key returns existing transaction
- Status transitions (valid moves only):
  - `pending` → `processing` → `completed`
  - `pending` → `processing` → `failed`
  - `completed` → `reversed`
  - No other transitions allowed
- Transaction records are never deleted
- Users see only their own transactions (where `initiated_by_user_id` matches)

## Security Considerations
- Users can only query their own transactions
- `provider_reference` is internal; not exposed in user-facing response
- `metadata` field may contain sensitive data (bank details) — review what is serialized

## Performance Considerations
- Indexed: `reference`, `provider_reference`, `idempotency_key`, `source_wallet_id`, `destination_wallet_id`, `status`, `created_at`
- List endpoint: paginated by `created_at` DESC, filter by `type` and `status`
- Admin list: additional filter by date range

## Reliability Considerations
- `get_by_idempotency_key` must be checked before creating any new transaction
- Transaction creation should be inside DB transaction scope of the calling service
- Status updates must validate against allowed state transitions

## Testing Expectations
- Unit: state machine rejects invalid transitions
- Unit: idempotency key prevents duplicate transaction creation
- API: user cannot view other users' transactions
- API: pagination works correctly with filters

## Implementation Notes
- `TransactionRepository.create(...)` accepts a dict of fields, returns Transaction
- `TransactionRepository.update_status(id, new_status, session)` validates transition before writing
- Reference generation: `str(uuid.uuid4())` formatted as `txn_{uuid}` for readability
- `metadata` stored as JSONB — callers pass a dict

## Status
complete

## Pending Tasks
- None

## Completion Notes
- `app/models/transaction.py` — Transaction model (TimestampMixin only, no soft delete — immutable); TransactionType + TransactionStatus enums; VALID_TRANSITIONS dict exported from model for use by repo; `extra_data` attribute maps to `metadata` DB column (renamed to avoid shadowing SQLAlchemy's `Base.metadata`)
- `alembic/versions/d4e5f6a7b8c9_create_transactions_table.py` — creates transactiontype + transactionstatus PG enums, transactions table with all FKs (RESTRICT on delete), 9 indexes
- `app/repositories/transaction.py` — create, get_by_id, get_by_reference, get_by_provider_reference, get_by_idempotency_key, get_by_wallet (OR filter, paginated), get_by_user (AND filter with optional type/status, paginated), update_status (state machine enforced, raises ValidationError on illegal transition)
- `app/schemas/transaction.py` — TransactionOut: provider_reference excluded (internal), extra_data included, use_enum_values=True for clean serialization
- `app/services/transaction.py` — create_transaction (idempotency check before insert, reference = `txn_{uuid4()}`), get_transaction (ownership check), list_transactions (returns PaginatedData[TransactionOut])
- `app/api/v1/transactions.py` — GET /transactions (type+status filter query params), GET /transactions/{reference}
- `app/api/v1/router.py` — transactions router included
- `app/models/__init__.py` + `alembic/env.py` — Transaction registered
- `app/services/wallet.py` — get_statement stub replaced with real TransactionRepository.get_by_wallet query
- `app/api/v1/wallets.py` — /me/transactions response_model upgraded from PaginatedData[Any] to PaginatedData[TransactionOut]
- `tests/conftest.py` — test_transaction fixture (COMPLETED FUNDING txn, amount 1000 NGN)
- `tests/unit/test_transactions.py` — 19 unit tests covering create, idempotency, full state machine transitions, ownership, list filters, pagination
- `tests/api/test_transactions_api.py` — 18 API tests covering list endpoint (auth, filters, pagination, other-user isolation) and detail endpoint (200, 404, ownership 404, no provider_reference in response)
