# Audit Component

## Purpose
Records an immutable audit trail of all significant actions across the platform.
Provides admin visibility into system events, user actions, and financial movements.

## Scope
### In Scope
- `AuditLog` model
- `AuditService.log(actor_id, actor_type, action, target_type, target_id, metadata, ip_address)`
- Admin audit log query endpoint (paginated, filterable)

### Out of Scope
- Triggering business logic → audit is purely a side-effect recorder

## Responsibilities
- `AuditRepository`: create, get_by_actor, get_by_action, list (paginated)
- `AuditService`: single `log(...)` method used by all other components
- Admin endpoint to query audit logs

## Dependencies
- All other components call AuditService (no circular dependency — audit only reads actor/target IDs)

## Related Models
- `AuditLog`

## Related Endpoints
- `GET /api/v1/admin/audit-logs` — admin only, paginated, filter by actor, action, date range

## Business Rules
- Audit logs are never deleted or modified
- All actions listed in `07_security_and_risk.md` must produce an audit log
- Audit log write failure must NOT fail the parent transaction — wrap in try/except
- `metadata` field captures relevant context (e.g., amount, before/after tier, rejection reason)

## Security Considerations
- Admin-only read access
- `ip_address` captured from request context where available
- No sensitive data (passwords, API keys) ever stored in metadata

## Performance Considerations
- Audit log writes are append-only — very fast
- Index on `actor_id`, `action`, `created_at` for admin queries
- Admin list: paginated (default 20, max 100), never unbounded

## Reliability Considerations
- Write failures must NOT propagate to caller — audit is a non-blocking side effect
- If audit write fails: log error internally, continue parent operation
- Eventual consistency on audit is acceptable

## Testing Expectations
- Integration: after each major flow (transfer, KYC approval, etc.), confirm audit log entry exists
- API: non-admin cannot access audit log endpoint
- Edge: audit write failure does not fail parent operation

## Implementation Notes
- `AuditService.log(...)` is a fire-and-forget write — call after transaction commit
- For async (Celery) contexts: write audit log inside worker task, not in the HTTP request
- `actor_type=system` for automated Celery-driven events
- AuditService is a thin wrapper — no business logic

## Status
complete

## Pending Tasks
- None

## Completion Notes
- `AuditLog` model uses `metadata_` Python attribute aliased to `"metadata"` column to avoid shadowing SQLAlchemy's reserved `Base.metadata` name
- `AuditService` (async) and `log_sync()` (sync) both follow the never-raise contract; any write failure is caught, logged, session rolled back, and the caller is unaffected
- `ActorType` enum: USER / SYSTEM / ADMIN
- Audit calls wired into: auth.register, auth.login, kyc.submit/approve/reject, transfer.completed, merchant.created, merchant.api_key_generated (on create + rotate), paystack_webhook wallet.funded, merchant_payment.completed, withdrawal.initiated/completed/failed
- Celery sync tasks (fraud_tasks, webhook_tasks) use `log_sync()` with `SyncSessionLocal` sessions
- Admin endpoint `GET /api/v1/admin/audit-logs` — paginated, filterable by actor_id, action, date range; admin-only
- `AuditLogOut` schema uses `validation_alias="metadata_"` to serialize ORM's `metadata_` attribute as `"metadata"` in JSON
- 24 tests: unit (AuditService.log, log_sync), integration (register, login, transfer), edge case (audit failure does not break parent), admin endpoint (auth, list, filters, pagination)
