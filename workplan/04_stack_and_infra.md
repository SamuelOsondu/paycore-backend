# Stack and Infrastructure — PayCore

## Language and Runtime

- Python 3.12+
- Reason: async support maturity, type hints, pydantic v2 compatibility

---

## Framework

- **FastAPI** — async-first, automatic OpenAPI docs, Pydantic integration, dependency injection
- ASGI server: **Uvicorn** (dev: single worker; prod: gunicorn + uvicorn workers)

---

## Database

- **PostgreSQL 15+** — relational, ACID-compliant, row locking support
- Driver: `asyncpg` (async) for FastAPI app
- Sync driver: `psycopg2` for Celery workers
- ORM: **SQLAlchemy 2.x** (async session for app, sync session for workers)
- Migrations: **Alembic**

---

## Queue and Background Jobs

- Broker: **Redis 7+**
- Worker framework: **Celery 5+**
- Task monitoring: **Flower** (optional, accessible at port 5555 in Docker Compose)
- Beat scheduler: Celery Beat for periodic reconciliation jobs

---

## Caching

- Redis (same instance as Celery broker) for:
  - Idempotency key deduplication (short TTL)
  - Rate limit counters (via slowapi)
- No application-level query caching in MVP

---

## Authentication

- **JWT** — PyJWT library
- Access token: 30 min, HS256
- Refresh token: 1 days, stored hashed in DB
- Password hashing: **bcrypt** via `passlib`

---

## External Integrations

- **Paystack** — HTTP via `httpx` (async client)
  - Wallet funding initialization
  - Payment verification
  - Transfer/payout to bank accounts
  - Webhook ingestion
- **AWS S3** — `boto3` for KYC document uploads (sync, called from async via `asyncio.run_in_executor`)

---

## Rate Limiting

- **slowapi** (Starlette-compatible limiter using Redis as backend)
- Login: 5 requests/minute per IP
- Transaction endpoints: 30 requests/minute per user

---

## Validation

- **Pydantic v2** — request/response schemas, settings management
- **pydantic-settings** — environment variable loading

---

## Logging

- Python standard `logging` with a JSON formatter
- Request ID middleware: UUID injected per request, added to all log lines
- Log levels: DEBUG (dev), INFO (prod), ERROR always

---

## Testing

- **pytest** + **pytest-asyncio**
- **httpx** async test client for API tests
- **pytest-cov** for coverage reporting
- Factory pattern for test data (no FactoryBoy dependency — manual fixtures)
- Test database: separate PostgreSQL DB or SQLite in-memory for unit tests

---

## Docker Compose Services

```
services:
  api          → FastAPI app (port 8000)
  worker       → Celery worker
  beat         → Celery Beat scheduler
  postgres     → PostgreSQL (port 5432)
  redis        → Redis (port 6379)
  flower       → Celery Flower UI (port 5555, optional)
```

---

## Environment Variables

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL async connection string |
| `SYNC_DATABASE_URL` | PostgreSQL sync connection string (Celery) |
| `REDIS_URL` | Redis connection string |
| `SECRET_KEY` | JWT signing secret |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Default: 15 |
| `REFRESH_TOKEN_EXPIRE_DAYS` | Default: 7 |
| `PAYSTACK_SECRET_KEY` | Paystack API secret |
| `PAYSTACK_PUBLIC_KEY` | Paystack public key |
| `PAYSTACK_WEBHOOK_SECRET` | Paystack webhook signature secret |
| `AWS_ACCESS_KEY_ID` | S3 credential |
| `AWS_SECRET_ACCESS_KEY` | S3 credential |
| `AWS_REGION` | S3 region |
| `S3_BUCKET_NAME` | KYC document bucket name |
| `MOCK_PAYOUT` | Set to `true` to bypass Paystack Transfer API |
| `KYC_TIER1_DAILY_LIMIT` | Daily transfer limit for Tier 1 (default: 50000) |
| `KYC_TIER2_DAILY_LIMIT` | Daily transfer limit for Tier 2 (default: 500000) |
| `KYC_TIER1_SINGLE_LIMIT` | Per-transaction limit for Tier 1 (default: 50000) |
| `KYC_TIER2_SINGLE_LIMIT` | Per-transaction limit for Tier 2 (default: 500000) |
| `FRAUD_DUPLICATE_WINDOW_SECONDS` | Window for duplicate transfer detection (default: 60) |
| `FRAUD_RAPID_TRANSFER_COUNT` | Distinct recipient threshold for rapid-transfer flag (default: 5) |
| `FRAUD_RAPID_TRANSFER_WINDOW_SECONDS` | Window for rapid-transfer detection (default: 600) |
| `FRAUD_MERCHANT_PAYMENT_FLAG_THRESHOLD` | NGN threshold to flag large Tier 1 merchant payments (default: 100000) |
| `ENVIRONMENT` | `development` or `production` |

---

## Key Python Dependencies

```
fastapi
uvicorn[standard]
gunicorn
sqlalchemy[asyncio]
asyncpg
psycopg2-binary
alembic
celery[redis]
redis
httpx
pyjwt
passlib[bcrypt]
pydantic[email]
pydantic-settings
boto3
slowapi
pytest
pytest-asyncio
httpx  # test client
pytest-cov
python-multipart  # file uploads
```
