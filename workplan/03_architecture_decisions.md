# Architecture Decisions — PayCore

## Architecture Style

**Decision: Modular Monolith**

Rationale:
- Single FastAPI application with clearly bounded modules
- Each module has its own router, service, repository, and schemas
- No inter-service HTTP calls — all communication via function calls within the process
- Celery workers run as separate processes but share the same codebase
- Clean module boundaries make a future service split straightforward if needed

---

## Directory Structure

```
app/
  api/               # FastAPI routers (thin handlers only)
    v1/
      auth.py
      users.py
      kyc.py
      wallets.py
      transactions.py
      transfers.py
      merchants.py
      withdrawals.py
      admin.py
      webhooks.py    # Paystack webhook receiver
  models/            # SQLAlchemy ORM models
  schemas/           # Pydantic request/response schemas
  services/          # Business logic layer
  repositories/      # Database access layer
  integrations/      # External API clients (Paystack, S3)
  workers/           # Celery tasks
  core/              # Config, security, database session, middleware
  utils/             # Shared utilities (pagination, audit helper, etc.)
```

---

## Module Interaction Pattern

```
Router (api/) → Service (services/) → Repository (repositories/)
                      ↓
               Integrations (integrations/)   ← external APIs
                      ↓
               Workers (workers/)             ← async jobs via Celery
```

- Routers call services, never repositories directly
- Services own business logic, transaction management, and coordination
- Repositories own all database queries
- Workers call services for their logic (no raw DB access in worker tasks)

---

## Async Strategy

**FastAPI app:** Fully async (async SQLAlchemy, async endpoints)

**Celery workers:** Standard synchronous Celery tasks using a separate sync DB session
- Reason: Celery does not natively support async task execution cleanly; sync workers with SQLAlchemy sync engine is the pragmatic choice

**Async boundaries:**
- Webhook delivery → Celery task (async via queue)
- Withdrawal payout → Celery task (async via queue)
- Fraud anomaly flagging → Celery task (post-transaction)
- Reconciliation jobs → Celery periodic task (beat)

---

## Data Transaction Strategy

**Rule:** All money-moving operations must execute inside a single SQLAlchemy database transaction covering:
1. Ledger entry writes (debit + credit)
2. Wallet balance updates
3. Transaction record creation/update

If any step fails, the entire transaction rolls back. No partial state.

Pattern used: SQLAlchemy `async with session.begin()` wrapping service calls from repository layer.

---

## API Versioning

All endpoints prefixed with `/api/v1/`.
No versioning complexity beyond this for MVP.

---

## Authentication Architecture

- FastAPI dependency injection: `get_current_user` dependency on protected routes
- Role-based: `require_role("admin")`, `require_role("merchant")` dependencies
- Merchant API key auth: separate `get_merchant_from_api_key` dependency for merchant-facing endpoints
- Refresh token stored hashed in `refresh_tokens` table with user_id, expiry, revoked flag

---

## Webhook Architecture

**Incoming (Paystack → Platform):**
- Single endpoint: `POST /api/v1/webhooks/paystack`
- Verify `x-paystack-signature` HMAC header before processing
- Idempotency: check `paystack_reference` against existing transactions before acting
- Queue processing: accept → verify → enqueue Celery task → return 200 immediately

**Outgoing (Platform → Merchant):**
- Stored in `webhook_deliveries` table with status, attempts, next_retry_at
- Celery task: POST to merchant URL with HMAC signature header
- Retry up to 5 times with exponential backoff
- Dead-letter: mark as `failed` after max retries

---

## Ledger Architecture

Double-entry: every completed money movement creates exactly two ledger entries.

| Transaction Type | Debit Account | Credit Account |
|---|---|---|
| Wallet Funding | System Float Account | User Wallet |
| Transfer | Sender Wallet | Recipient Wallet |
| Merchant Payment | User Wallet | Merchant Wallet |
| Withdrawal | User Wallet | System Payout Account |
| Reversal | Original credit account | Original debit account |

`LedgerEntry` model fields: `id`, `transaction_id`, `wallet_id`, `entry_type` (debit/credit), `amount`, `balance_after`, `created_at`

---

## Security Architecture

- JWT signed with HS256, secret from environment
- Paystack webhook HMAC: SHA512 with Paystack secret key
- Merchant API keys: generated as UUID4, stored as bcrypt hash, shown once on creation
- KYC documents: stored in S3 with private ACL; access via presigned URLs only
- Rate limiting: `slowapi` on auth endpoints (5/min login), transaction endpoints (30/min)
- Input validation: Pydantic schemas enforce all constraints at boundary

---

## Key Design Invariants

1. Wallet balance field is the single source of truth for available balance during transactions
2. Ledger is the audit source of truth for all balance history
3. A transaction record is created before any money moves; it transitions through states
4. Celery tasks are idempotent — safe to retry without double-processing
5. No raw SQL; all DB access through SQLAlchemy ORM in repositories
