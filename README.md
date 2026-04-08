# PayCore — Fintech Wallet Backend

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.12-blue.svg)

A production-grade async REST API for a digital wallet platform. Built with FastAPI, PostgreSQL, Celery, and Redis. Handles wallet funding via Paystack, peer-to-peer transfers, merchant payments, KYC verification, fraud detection, and automated reconciliation — all backed by a double-entry ledger.

---

## Table of Contents

- [Features](#features)
- [Tech Stack](#tech-stack)
- [Architecture Overview](#architecture-overview)
- [Project Structure](#project-structure)
- [Getting Started](#getting-started)
- [Environment Variables](#environment-variables)
- [Running Migrations](#running-migrations)
- [API Reference](#api-reference)
- [Background Workers](#background-workers)
- [Database Schema](#database-schema)
- [Security](#security)
- [Design Decisions](#design-decisions)

---

## Features

- **Wallet management** — One NGN wallet per user; balance maintained with a double-entry ledger for full audit trail
- **Wallet funding** — Paystack-powered card/bank funding with HMAC-verified webhook confirmation
- **Peer-to-peer transfers** — Deadlock-safe wallet locking, idempotency keys, fraud-checked before execution
- **Merchant payments** — Users pay merchants by reference; merchants receive signed outbound webhooks with retry logic
- **KYC (3 tiers)** — Document upload to S3, admin approve/reject workflow with presigned download URLs; tier gates transaction limits
- **Fraud detection** — Velocity checks, duplicate detection, and large-transaction flags running as Celery tasks
- **Withdrawals** — Bank account registration with Paystack recipient verification; async payout dispatch
- **Audit log** — Append-only log of every significant action (actor, type, target, IP, metadata)
- **Admin panel** — Transaction monitoring, KYC review, reconciliation trigger, user and webhook inspection
- **Automated reconciliation** — Celery Beat job every 30 minutes checks stale transactions against Paystack and resolves them
- **Outbound webhook retries** — Beat job every 5 minutes retries failed merchant webhook deliveries with exponential back-off

---

## Tech Stack

| Layer | Technology |
|---|---|
| API framework | FastAPI 0.115 + Uvicorn |
| Database | PostgreSQL 16 (async via asyncpg) |
| ORM / migrations | SQLAlchemy 2.0 async + Alembic |
| Task queue | Celery 5.4 + Redis 7 |
| Auth | JWT (HS256) + bcrypt |
| Payments | Paystack API |
| File storage | AWS S3 (KYC documents) |
| Rate limiting | SlowAPI |
| Validation | Pydantic v2 |
| Containerisation | Docker + Docker Compose |

---

## Architecture Overview

```
                        ┌──────────────────────┐
                        │     FastAPI (async)   │
                        │   /api/v1/* routes    │
                        └────────┬─────────────┘
                                 │
            ┌────────────────────┼────────────────────┐
            │                    │                    │
     ┌──────▼──────┐    ┌────────▼───────┐   ┌───────▼──────┐
     │  Services   │    │  Repositories  │   │   Schemas    │
     │ (business   │    │  (data access  │   │  (Pydantic   │
     │   logic)    │    │   SQLAlchemy)  │   │  request/    │
     └──────┬──────┘    └────────┬───────┘   │  response)   │
            │                    │            └──────────────┘
            │            ┌───────▼───────┐
            │            │  PostgreSQL   │
            │            │  (11 tables)  │
            │            └───────────────┘
            │
     ┌──────▼──────────────────────────────────┐
     │              Celery Workers              │
     │  fraud_tasks · withdrawal_tasks          │
     │  webhook_tasks · paystack_tasks          │
     │  reconciliation_tasks                    │
     └──────────────────┬───────────────────────┘
                        │
                 ┌──────▼──────┐
                 │    Redis     │
                 │  (broker +   │
                 │   results)   │
                 └─────────────┘
```

**Money movement flows:**

1. **Inflow** — User initiates funding → Paystack payment link created → user pays → Paystack fires signed webhook → `PaystackWebhookService` verifies signature → wallet credited + ledger entry written
2. **Internal** — Transfer or merchant payment → fraud checks run → pessimistic wallet lock → debit source + credit destination atomically → double-entry ledger → outbound merchant webhook queued
3. **Outflow** — User requests withdrawal → bank account verified via Paystack → payout dispatched async via Celery → transaction resolved on callback or by reconciliation beat

---

## Project Structure

```
paycore-backend/
├── app/
│   ├── api/v1/           # Route handlers (one file per domain)
│   │   ├── auth.py
│   │   ├── users.py
│   │   ├── wallets.py
│   │   ├── transactions.py
│   │   ├── transfers.py
│   │   ├── merchants.py
│   │   ├── kyc.py
│   │   ├── withdrawals.py
│   │   ├── webhooks.py   # Inbound Paystack webhook
│   │   └── admin.py
│   ├── core/             # Config, DB session, deps, security, exceptions
│   ├── models/           # SQLAlchemy ORM models (11 tables)
│   ├── repositories/     # Data access layer (one repo per model)
│   ├── schemas/          # Pydantic request/response schemas
│   ├── services/         # Business logic layer
│   ├── integrations/     # External clients (Paystack, S3)
│   └── workers/          # Celery tasks + beat schedule
├── alembic/              # Database migrations (11 versions)
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
└── .env.example
```

---

## Getting Started

### Prerequisites

- Docker and Docker Compose
- (For local dev without Docker) Python 3.12+, PostgreSQL 16, Redis 7

### 1. Clone and configure

```bash
git clone https://github.com/SamuelOsondu/paycore-backend.git
cd paycore-backend
cp .env.example .env
# Edit .env — at minimum set SECRET_KEY, PAYSTACK_SECRET_KEY, and AWS credentials
```

### 2. Start all services

```bash
docker compose up --build
```

This starts:

| Service | Port | Description |
|---|---|---|
| `api` | 8000 | FastAPI application |
| `worker` | — | Celery worker (concurrency: 4) |
| `beat` | — | Celery Beat scheduler |
| `postgres` | 5432 | PostgreSQL database |
| `redis` | 6379 | Redis broker |
| `flower` | 5555 | Celery task monitor (optional profile) |

### 3. Run migrations

```bash
docker compose exec api alembic upgrade head
```

### 4. Verify

```
GET http://localhost:8000/health
→ {"status": "ok"}

GET http://localhost:8000/docs
→ Interactive Swagger UI
```

---

## Environment Variables

Copy `.env.example` to `.env` and fill in the values.

| Variable | Description |
|---|---|
| `SECRET_KEY` | JWT signing secret (use a long random string in production) |
| `DATABASE_URL` | Async PostgreSQL URL (`postgresql+asyncpg://...`) |
| `SYNC_DATABASE_URL` | Sync PostgreSQL URL for Alembic (`postgresql+psycopg2://...`) |
| `REDIS_URL` | Redis connection string |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | JWT access token TTL (default: 30) |
| `REFRESH_TOKEN_EXPIRE_DAYS` | Refresh token TTL (default: 1) |
| `PAYSTACK_SECRET_KEY` | Paystack secret key (`sk_live_...` or `sk_test_...`) |
| `PAYSTACK_PUBLIC_KEY` | Paystack public key |
| `PAYSTACK_WEBHOOK_SECRET` | Used to verify HMAC-SHA512 webhook signatures |
| `AWS_ACCESS_KEY_ID` | AWS credentials for S3 |
| `AWS_SECRET_ACCESS_KEY` | AWS credentials for S3 |
| `AWS_REGION` | S3 bucket region |
| `S3_BUCKET_NAME` | Bucket for KYC document uploads |
| `KYC_TIER0_DAILY_FUNDING_LIMIT` | Max daily funding for unverified users (default: 10,000 NGN) |
| `KYC_TIER1_DAILY_LIMIT` | Daily limit for Tier 1 users (default: 50,000 NGN) |
| `KYC_TIER2_DAILY_LIMIT` | Daily limit for Tier 2 users (default: 500,000 NGN) |
| `MOCK_PAYOUT` | Set `true` to skip real Paystack payout calls in development |
| `CELERY_WORKER_CONCURRENCY` | Celery worker process count (default: 4) |

---

## Running Migrations

```bash
# Apply all migrations
alembic upgrade head

# Create a new migration after model changes
alembic revision --autogenerate -m "describe the change"

# Roll back one version
alembic downgrade -1
```

---

## API Reference

All endpoints are prefixed with `/api/v1`. Interactive docs at `/docs` (Swagger) and `/redoc`.

Every response follows the envelope format:
```json
{
  "success": true,
  "message": "Human-readable message.",
  "data": { ... },
  "error_code": null
}
```

Paginated list responses wrap items in:
```json
{
  "items": [...],
  "total": 42,
  "limit": 20,
  "offset": 0
}
```

---

### Auth — `/auth`

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/auth/register` | — | Create account. Returns access + refresh tokens. Rate-limited: 10/min |
| `POST` | `/auth/login` | — | Email + password. Returns access + refresh tokens. Rate-limited: 20/min |
| `POST` | `/auth/refresh` | Refresh token | Rotate refresh token, issue new access token |
| `POST` | `/auth/logout` | Bearer | Revoke refresh token |

---

### Users — `/users`

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/users/me` | Bearer | Current user profile |
| `PATCH` | `/users/me` | Bearer | Update `full_name` and/or `phone` |

---

### Wallets — `/wallets`

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/wallets/fund` | Bearer | Initiate Paystack funding. Returns payment URL |
| `GET` | `/wallets/me` | Bearer | Wallet balance and details |
| `GET` | `/wallets/me/transactions` | Bearer | Paginated transaction history |

---

### Transactions — `/transactions`

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/transactions` | Bearer | User's transactions. Filter: `type`, `status`, `limit`, `offset` |
| `GET` | `/transactions/{reference}` | Bearer | Single transaction detail |

---

### Transfers — `/transfers`

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/transfers` | Bearer | Send funds to another user by `recipient_user_id` or `email`. Supports `idempotency_key` |

---

### Merchants — `/merchants`

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/merchants` | Bearer | Promote account to merchant, set `business_name` and optional `webhook_url` |
| `GET` | `/merchants/me` | Merchant Bearer | Merchant profile |
| `POST` | `/merchants/me/api-key` | Merchant Bearer | Rotate API key. Returns new plaintext key once |
| `PATCH` | `/merchants/me/webhook` | Merchant Bearer | Update webhook URL |
| `GET` | `/merchants/me/payments` | Merchant Bearer | Paginated list of received payments |

---

### KYC — `/kyc`

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/kyc/submit` | Bearer | Upload identity document (`multipart/form-data`) and specify `target_tier` |
| `GET` | `/kyc/me` | Bearer | Most recent KYC submission and status |

---

### Withdrawals

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/bank-accounts` | Bearer | Register a bank account (verified via Paystack) |
| `GET` | `/bank-accounts` | Bearer | List user's bank accounts |
| `DELETE` | `/bank-accounts/{id}` | Bearer | Soft-delete a bank account |
| `POST` | `/withdrawals` | Bearer | Initiate withdrawal to a registered bank account |
| `GET` | `/withdrawals/{reference}` | Bearer | Check withdrawal status |

---

### Webhooks — `/webhooks`

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/webhooks/paystack` | HMAC-SHA512 | Inbound Paystack event handler (charge success, transfer callbacks) |

---

### Admin — `/admin` *(requires `role=admin`)*

| Method | Path | Description |
|---|---|---|
| `GET` | `/admin/audit-logs` | Paginated audit log. Filter: `actor_id`, `action`, `from_date`, `to_date` |
| `GET` | `/admin/webhook-deliveries` | Paginated outbound webhook delivery records |
| `GET` | `/admin/kyc` | KYC submissions. Filter: `status` (default: pending) |
| `GET` | `/admin/kyc/{id}` | KYC submission detail + presigned S3 document URL |
| `POST` | `/admin/kyc/{id}/approve` | Approve KYC; promotes user tier automatically |
| `POST` | `/admin/kyc/{id}/reject` | Reject KYC with a reason |
| `GET` | `/admin/transactions` | All transactions. Filter: `status`, `type`, `risk_flagged`, `from_date`, `to_date` |
| `GET` | `/admin/transactions/{reference}` | Transaction detail with embedded ledger entries |
| `POST` | `/admin/reconciliation/run` | Manually enqueue the stale-transaction reconciliation Celery task |
| `GET` | `/admin/users` | All users. Filter: `role`, `kyc_tier` |
| `GET` | `/admin/users/{id}` | User detail |

---

## Background Workers

Workers are Celery tasks defined under `app/workers/`. Redis is the broker.

### Beat Schedule

| Task | Interval | Description |
|---|---|---|
| `webhooks.retry_pending_webhooks` | Every 5 minutes | Retry outbound merchant webhook deliveries that are pending or failed (up to `max_retries`) |
| `reconciliation.check_stale_transactions` | Every 30 minutes | Fetch stale PENDING transactions and resolve them against Paystack or re-enqueue for retry |

### Task Files

| File | Tasks |
|---|---|
| `fraud_tasks.py` | Async fraud evaluation after transfer/payment |
| `withdrawal_tasks.py` | `process_withdrawal` — dispatch payout to Paystack, update transaction status |
| `paystack_tasks.py` | Process charge success events received via webhook |
| `webhook_tasks.py` | `retry_pending_webhooks` — periodic outbound webhook retry |
| `reconciliation_tasks.py` | `check_stale_transactions` — FUNDING stale txns verified via Paystack; WITHDRAWAL stale txns re-enqueued |

### Running Workers Manually

```bash
# Start worker
celery -A app.workers.celery_app worker --loglevel=info

# Start beat scheduler
celery -A app.workers.celery_app beat --loglevel=info

# Monitor via Flower
celery -A app.workers.celery_app flower
```

---

## Database Schema

11 tables across 11 Alembic migrations.

```
users
  id · email · phone · hashed_password · full_name
  role (user | merchant | admin) · kyc_tier (0–2)
  is_active · is_email_verified · deleted_at

wallets
  id · user_id (unique FK) · currency · balance (≥ 0 CHECK)
  is_active · deleted_at

transactions
  id · reference (unique) · type · status
  amount · currency · source_wallet_id · destination_wallet_id
  initiated_by_user_id · idempotency_key (unique)
  provider_reference · metadata (JSONB)
  risk_flagged · risk_flag_reason
  failure_reason · created_at · updated_at

ledger_entries                           ← double-entry record per transaction leg
  id · transaction_id · wallet_id
  entry_type (debit | credit) · amount · currency
  balance_after · created_at

merchants
  id · user_id (unique FK) · business_name
  api_key_hash · webhook_url · deleted_at

kyc_submissions
  id · user_id · target_tier · status
  document_s3_path · rejection_reason

bank_accounts
  id · user_id · account_number · bank_code
  account_name · is_default · paystack_recipient_code · deleted_at

refresh_tokens
  id · user_id · token_hash · expires_at · revoked_at

webhook_deliveries
  id · merchant_id · event_type · payload (JSONB)
  attempt_count · max_retries · status · last_error

audit_logs                               ← append-only, never updated
  id · actor_id · actor_type (user | system | admin)
  action · target_type · target_id
  metadata (JSONB) · ip_address · created_at

```

**Transaction state machine:**

```
PENDING → PROCESSING → COMPLETED
                    ↘ FAILED
```

Direct `PENDING → FAILED` is not allowed — reconciliation always transitions through `PROCESSING` first to ensure ledger consistency.

---

## Security

| Concern | Approach |
|---|---|
| Password storage | bcrypt (no passlib — avoids Python 3.12 deprecation warnings) |
| Auth tokens | Short-lived JWT access tokens (30 min) + rotating refresh tokens (1 day) |
| Role enforcement | `require_role("admin")` FastAPI dependency on all admin routes |
| Paystack webhooks | HMAC-SHA512 signature verification on every inbound event |
| Merchant webhooks | HMAC-SHA256 payload signing; merchants verify on their end |
| Rate limiting | SlowAPI: 10/min on register, 20/min on login |
| Soft deletes | Users, wallets, merchants, bank accounts — never hard-deleted |
| Idempotency | Unique `idempotency_key` on funding and transfers prevents double-spend |
| Wallet locking | `SELECT FOR UPDATE NOWAIT` to prevent concurrent balance corruption |
| Admin isolation | Admin schemas expose internal fields (`provider_reference`, `risk_flagged`) never surfaced to users |

---

## Design Decisions

| Decision | Rationale |
|---|---|
| One wallet per user | Simplifies balance management and KYC limit enforcement |
| Double-entry ledger | Every balance change is auditable and reconcilable independently of wallet balance |
| `risk_flagged` boolean column (not JSONB) | Allows a clean index for admin fraud filter queries |
| `metadata_` attribute alias for audit log | Avoids shadowing SQLAlchemy's `Base.metadata` class attribute |
| Sync engine for Alembic only | Migrations run as CLI commands; sync is simpler and fully compatible with asyncpg at runtime |
| Deferred imports in Celery tasks | Prevents circular imports at worker startup; tasks import services and clients inside function bodies |
| `MOCK_PAYOUT=true` flag | Allows full end-to-end withdrawal testing in development without real Paystack payout calls |
