# Discovery Answers — PayCore

## Stack and Framework
- Language: Python 3.12+
- Framework: FastAPI
- ORM: SQLAlchemy (async) with Alembic migrations
- Database: PostgreSQL
- Queue: Celery + Redis
- Auth: JWT (access + refresh tokens)
- Infra: Docker + Docker Compose

**Decided:** No changes permitted to the stack. Brief explicitly defines it.

---

## Balance Tracking Strategy
**Answer:** Maintained balance field on Wallet model, updated atomically inside the same DB transaction as every ledger write.

**Rationale:** Fast balance reads without ledger aggregation. Safe because all writes go through the service layer under a single transaction. Ledger remains authoritative for reconciliation.

---

## KYC Document Storage
**Answer:** AWS S3 (via boto3). `S3_BUCKET_NAME`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION` in environment variables.

**Rationale:** User selected cloud storage for production readiness.

**Implementation:** Abstract behind a `StorageService` interface. Upload returns a presigned URL or object key stored in the KYC record.

---

## Payout / Withdrawal Rail
**Answer:** Paystack Transfer API in sandbox mode.

**Implementation:**
- Paystack Transfer requires a transfer recipient (bank account registered with Paystack)
- Create transfer recipient → initiate transfer → handle webhook callback
- Sandbox mode simulates success/failure callbacks
- Fallback: if Paystack transfer unavailable in test, support a `MOCK_PAYOUT=true` env flag that simulates deterministic success after 5 seconds

---

## Authentication
**Answer:** Access + refresh token pair.

- Access token: 15-minute expiry, used on all protected endpoints
- Refresh token: 7-day expiry, stored in DB (hashed), used only on `/auth/refresh`
- On logout: invalidate refresh token in DB
- Role field on User model: `user`, `merchant`, `admin`

---

## KYC Tiers
| Tier | Requirements | Limits |
|---|---|---|
| Tier 0 | Email verified | No transfers, no withdrawal, funding only (small cap) |
| Tier 1 | Phone + basic profile | Daily transfer limit: 50,000 NGN, no withdrawal |
| Tier 2 | ID document, admin-approved | Daily transfer limit: 500,000 NGN, withdrawal enabled |

**Note:** These limits are configurable via env variables so they can be adjusted without code changes.

---

## Fraud and Risk Controls
- Synchronous checks block transactions (KYC tier limit, daily volume, balance check)
- Rapid repeat transfer detection (same recipient, same amount within 60 seconds)
- Merchant payment anomaly check (unusually large single payment)
- These checks run inside the service layer before any ledger writes

---

## Real-Time / WebSockets
**Decision:** Not needed for MVP. REST polling is sufficient. No WebSockets.

---

## Pagination
- All list endpoints: cursor-based pagination (by `created_at` + UUID) or offset pagination
- Decision: **offset pagination** for MVP (simpler, sufficient at portfolio scale)
- Default page size: 20, max: 100

---

## Testing
- Unit tests for service logic (fraud checks, KYC validation, balance logic)
- Integration tests for critical flows (funding, transfer, merchant payment, withdrawal)
- API tests for auth, permissions, and key endpoints
- Failure path tests for webhook deduplication, payout failure handling

---

## Operational
- Structured logging (Python `logging` with JSON formatter)
- Request IDs via middleware (UUID per request, passed to logs)
- Environment config via `.env` file loaded by `pydantic-settings`
- Docker Compose brings up: FastAPI app, PostgreSQL, Redis, Celery worker, Flower (optional)
- Health check endpoint: `GET /health`

---

## Email Notifications
**Decision:** Out of MVP scope. Stub `NotificationService` in place for future use.

---

## Rate Limiting
**Decision:** Include `slowapi` middleware. Rate limit auth endpoints aggressively. Apply lighter limits to transaction endpoints.
