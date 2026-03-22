# Admin Component

## Purpose
Provides admin-only endpoints for KYC review, transaction monitoring, audit log inspection,
fraud flag management, and reconciliation controls.

## Scope
### In Scope
- KYC submission list and review (approve/reject)
- Transaction monitoring (list, filter, view flagged)
- Audit log viewing
- Manual reconciliation trigger
- Webhook delivery inspection

### Out of Scope
- Admin user creation → management script (not API)
- Modifying financial records directly → not allowed
- Changing user balances directly → not allowed

## Responsibilities
- `AdminService`: aggregation of read queries and review actions
- Wire `require_role("admin")` on all admin routes
- All admin actions must call `AuditService.log` with `actor_type=admin`

## Dependencies
- KYC component (approve/reject)
- Transactions component (list, filter by flag)
- Audit component (read logs, write admin action logs)
- Outgoing Webhooks component (read delivery status)
- Fraud component (flag/unflag transactions)

## Related Models
- All models (read-only for most)
- `KYCSubmission` (update on review)

## Related Endpoints
- `GET /api/v1/admin/kyc` — list KYC submissions (filter: status)
- `GET /api/v1/admin/kyc/{id}` — view submission + presigned doc URL
- `POST /api/v1/admin/kyc/{id}/approve`
- `POST /api/v1/admin/kyc/{id}/reject`
- `GET /api/v1/admin/transactions` — list transactions (filter: status, type, flagged, date range)
- `GET /api/v1/admin/transactions/{reference}` — view single transaction with ledger entries
- `GET /api/v1/admin/audit-logs` — list audit logs (filter: actor, action, date range)
- `GET /api/v1/admin/webhook-deliveries` — list webhook deliveries (filter: status, merchant)
- `POST /api/v1/admin/reconciliation/run` — manually trigger reconciliation Celery task
- `GET /api/v1/admin/users` — list users (paginated, filter by role, kyc_tier)
- `GET /api/v1/admin/users/{id}` — view user detail

## Business Rules
- All endpoints require `role=admin`
- Admins cannot approve/reject their own KYC submission
- Admins cannot modify financial records (balances, transactions, ledger)
- Every admin action is audit-logged
- Reconciliation can only be triggered manually; does not auto-modify data without verification

## Security Considerations
- `require_role("admin")` dependency on all routes in this router
- Admin actions wrapped in `log_admin_action` decorator that writes audit log
- No write access to financial tables from admin endpoints (read + state transitions only)
- Rate limit admin endpoints: 60/minute per admin user

## Performance Considerations
- All list endpoints paginated
- Transaction list with flag filter: index on `metadata->>'risk_flag'` or use a `is_flagged` boolean column
- Decision: add `is_flagged BOOLEAN DEFAULT FALSE` column to `transactions` for index efficiency

## Reliability Considerations
- KYC approval must be atomic with user tier update
- Reconciliation trigger is idempotent (Celery task checks before acting)

## Testing Expectations
- API: non-admin returns 403 on all admin endpoints
- API: KYC approval updates user tier, emits audit log
- API: KYC rejection stores reason, emits audit log
- API: admin cannot approve own KYC
- Integration: reconciliation trigger enqueues Celery task

## Implementation Notes
- Admin router: `app/api/v1/admin.py` — imports from multiple service layers
- Use `Depends(require_role("admin"))` on the router itself (applies to all routes)
- `log_admin_action` helper: thin wrapper around `AuditService.log` with `actor_type=admin`
- Add `is_flagged` boolean to `Transaction` model (simpler than JSONB metadata query)

## Status
complete

## Pending Tasks
- None

## Completion Notes
- Existing `admin.py` already had KYC, audit-log, and webhook-delivery endpoints from prior components; this component adds the remaining 5 endpoints
- `is_flagged` field: `risk_flagged` (already on Transaction model from the Fraud component) serves this role — no migration needed; used directly in the admin transaction filter
- `TransactionAdminOut`: extends user-facing schema to include `provider_reference`, `risk_flagged`, `risk_flag_reason`
- `TransactionDetailAdminOut`: adds `ledger_entries: list[LedgerEntryOut]` for financial audit view
- `UserRepository.list_all()` / `count_all()`: new paginated query methods with role and kyc_tier filters
- `TransactionRepository.list_admin()` / `count_admin()`: new paginated query methods with status, type, risk_flagged, and date-range filters
- `POST /admin/reconciliation/run`: calls `check_stale_transactions.delay()` + writes `admin.reconciliation_triggered` audit log with `actor_type=ADMIN`
- `GET /admin/users` / `GET /admin/users/{id}`: read-only user management for admin inspection
- `GET /admin/transactions` / `GET /admin/transactions/{reference}`: transaction monitoring with embedded ledger entries on detail view
- Reconciliation trigger patch target: `app.workers.reconciliation_tasks.check_stale_transactions`
- 38 tests covering auth guards (401/403 on all 5 new endpoint groups), response shape, all filters, pagination, 404 cases, Celery task dispatch, and audit log write
