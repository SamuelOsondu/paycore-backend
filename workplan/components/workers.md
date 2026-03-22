# Workers Component

## Purpose
Defines all Celery task functions and the Celery application configuration.
Workers execute async jobs that must not block the HTTP request cycle.

## Scope
### In Scope
- Celery app factory
- All Celery task definitions
- Celery Beat schedule (periodic tasks)
- Worker-level DB session management (sync SQLAlchemy)

### Out of Scope
- Business logic → delegated to service layer
- HTTP request handling → API component

## Responsibilities
- `app/workers/celery_app.py`: configure Celery with Redis broker/backend
- `app/workers/tasks/`: individual task modules per domain

### Task List

| Task | Module | Trigger |
|---|---|---|
| `process_paystack_webhook` | webhook_tasks.py | Paystack webhook received |
| `process_withdrawal` | withdrawal_tasks.py | Withdrawal initiated |
| `deliver_merchant_webhook` | webhook_tasks.py | Payment to merchant completed |
| `retry_pending_webhooks` | webhook_tasks.py | Celery Beat (every 5 min) |
| `flag_transaction_risk` | fraud_tasks.py | Post-payment async risk flag |
| `check_stale_transactions` | reconciliation_tasks.py | Celery Beat (every 30 min) |

## Dependencies
- All service layers (called from within tasks)
- Sync DB session factory (separate from async app session)
- `PaystackClient` (sync version for worker context)

## Related Models
- All models (workers touch everything via services)

## Related Endpoints
- No HTTP endpoints. Celery-internal.

## Business Rules
- Workers must never mutate data without going through service layer
- All tasks must be idempotent — safe to re-run on retry
- Task failure must not leave system in inconsistent state
- Celery task time limits: soft=60s, hard=90s

## Security Considerations
- Workers share same secrets as app (loaded from .env via pydantic-settings)
- No direct HTTP ingress to workers
- Worker processes should run with minimal OS permissions

## Performance Considerations
- Worker concurrency: 4 (configurable via env `CELERY_WORKER_CONCURRENCY`)
- Use `autoretry_for` on transient errors (network, DB unavailable)
- Retry delays: exponential (base 2, max 300s)

## Reliability Considerations
- Each task begins by verifying the current state from DB (never trust task arguments alone)
- Idempotency: check target record status before acting
  - e.g., `process_withdrawal`: if transaction already `completed` or `failed`, skip
  - e.g., `deliver_merchant_webhook`: if delivery already `delivered`, skip
- `check_stale_transactions`: reconcile pending transactions > 30 min old via Paystack verify API

## Testing Expectations
- Unit: task idempotency (calling twice produces same result)
- Integration: `process_withdrawal` success path (mocked Paystack) → transaction completed
- Integration: `process_withdrawal` failure path → reversal, transaction failed
- Integration: `deliver_merchant_webhook` → HTTP POST made, delivery marked delivered
- Integration: `retry_pending_webhooks` → picks up due retries

## Implementation Notes
- Worker DB session: `from app.core.database import SyncSessionLocal` — separate sync engine
- Tasks use `@app.task(bind=True, autoretry_for=(Exception,), max_retries=3, default_retry_delay=60)`
- Celery Beat config in `celery_app.py` using `beat_schedule` dict
- Task arguments: pass only primitive IDs (UUID strings), not ORM objects

## Status
complete

## Pending Tasks
- None

## Completion Notes
- All task modules were implemented as part of their respective components; Workers component adds `reconciliation_tasks.py` and the beat schedule entry
- `reconciliation_tasks.py`: `check_stale_transactions` beat task (every 30 min) that reconciles stale PENDING FUNDING transactions (via Paystack verify API) and re-enqueues stale PENDING WITHDRAWAL transactions
- Funding reconciliation: Paystack success → `PaystackWebhookService.process_charge_success` (idempotent, same code path as inbound webhook); Paystack non-success → PENDING → PROCESSING → FAILED (two-step to respect state machine); any exception → logged, transaction stays PENDING for next cycle
- Withdrawal reconciliation: re-enqueue `process_withdrawal.delay()` — idempotent since that task checks PROCESSING status before acting
- Beat schedule in `celery_app.py`: `retry_pending_webhooks` every 5 min, `check_stale_transactions` every 30 min
- `reconciliation_tasks.py` uses `asyncio.run()` + deferred imports (same pattern as `paystack_tasks.py` and `withdrawal_tasks.py`) to avoid circular imports at Celery worker startup
- All task names, beat schedule entries, and idempotency behavior covered by 17 tests
