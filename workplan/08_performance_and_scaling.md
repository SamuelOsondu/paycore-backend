# Performance and Scaling — PayCore

## Scale Expectations

Portfolio/demo scale. Not optimized for millions of users.
However, design must demonstrate production-minded patterns:
- No N+1 queries
- Paginated lists
- Async where appropriate
- No blocking external calls in request path

---

## Hot Paths

| Path | Notes |
|---|---|
| Wallet balance read | Use maintained balance field — O(1) lookup, no aggregation |
| Transaction list | Paginated, indexed by `created_at` + `wallet_id` |
| Paystack webhook processing | Accept fast, process in Celery — never block HTTP response |
| Merchant webhook delivery | Always async via Celery |

---

## N+1 Prevention

- Transaction list: join load wallet info in one query
- Ledger entries: always queried by `transaction_id` or `wallet_id` with LIMIT
- KYC submissions (admin list): paginated, no nested per-row queries
- Webhook deliveries: batch query with status filter

---

## Async Boundaries

Synchronous in request/response:
- Auth operations
- Wallet balance read
- Transfer (fast, DB-only)
- Merchant payment (fast, DB-only)
- KYC submission (includes S3 upload — acceptable latency)

Asynchronous via Celery:
- Withdrawal payout (Paystack transfer API call)
- Merchant webhook delivery
- Paystack webhook processing (accepted immediately, processed in worker)
- Fraud anomaly flagging (post-transaction async flag)
- Reconciliation job (Celery Beat, every 30 minutes)

---

## Row Locking Strategy

All balance-modifying operations use `SELECT ... FOR UPDATE` on wallet rows:
- Prevents concurrent double-spend
- Scope: within the DB transaction that also writes ledger entries
- Pattern: `session.execute(select(Wallet).where(Wallet.id == ...).with_for_update())`

---

## Celery Worker Configuration

- Single queue for MVP (`default`)
- Worker concurrency: 4 (configurable via env)
- Task time limits: 60s soft, 90s hard
- Retry delays: exponential backoff (2^n seconds, max 300s)
- Max retries per task: 5

---

## Pagination

- All list endpoints: offset pagination with configurable limit
- Default: 20 items/page, max: 100
- Response always includes `total`, `limit`, `offset` in envelope

---

## Caching

MVP: Redis used only for:
- Celery broker
- Rate limit counters (slowapi)
- Idempotency key TTL store (short-lived: 5 minutes)

No query result caching in MVP. Not needed at portfolio scale.

---

## Query Strategy

- Alembic ensures all indexes are in place before launch
- Critical indexes: wallet `user_id`, transaction `reference`, `provider_reference`, `status`, `created_at`
- Avoid `SELECT *` — load only needed columns in repository queries
- Bulk inserts not needed for MVP

---

## Background Job Scheduling (Celery Beat)

| Job | Schedule | Purpose |
|---|---|---|
| reconciliation_check | Every 30 minutes | Find pending transactions >30min, verify with Paystack |
| webhook_retry_sweep | Every 5 minutes | Pick up failed webhook deliveries due for retry |
