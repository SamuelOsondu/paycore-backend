# Agent Directive — PayCore

## Project Intent

PayCore is a recruiter-facing open source fintech wallet and payment infrastructure API.
It must demonstrate real fintech engineering: double-entry ledger, internal money movement,
Paystack integration, async payout processing, KYC controls, merchant payments, audit logging.

It is NOT a Paystack wrapper. Paystack is only the external rail.
The platform's own wallet and ledger are the core product.

---

## Non-Negotiable Rules

1. Never update wallet balance outside a database transaction that also writes the ledger entries.
2. All money-moving operations must be idempotent. Duplicate requests must not create duplicate effects.
3. Every critical action must produce an audit log entry.
4. KYC tier must be checked and enforced before any transfer, payment, or withdrawal.
5. Wallet balance is a maintained field — updated atomically with ledger entries. Never derive balance from SUM on hot paths.
6. Paystack webhooks must be verified (HMAC signature) before processing.
7. Merchant outgoing webhooks must be delivered via background queue with retry logic.
8. Withdrawals must be processed asynchronously by a Celery worker.
9. All list endpoints must be paginated. No unbounded queries.
10. Never expose internal error details to API consumers. Log internally.

---

## Behavior Rules for Any Agent Continuing Work

- Read this file first.
- Read 01_project_summary.md to understand the product.
- Check 12_progress_tracker.md to know where to continue.
- Read the relevant component file before touching any module.
- Implement one component at a time. Do not scatter changes.
- Update component status and progress tracker after each unit of work.
- Follow the coding standards in 09_coding_standards.md exactly.
- Do not introduce new dependencies without documenting them in 04_stack_and_infra.md.
- When in doubt about a business rule, consult 01_project_summary.md and 07_security_and_risk.md.

---

## Stack Summary

- FastAPI, Python 3.12+
- PostgreSQL (via SQLAlchemy async + Alembic)
- Redis + Celery (background jobs)
- JWT (access + refresh tokens)
- S3 (KYC document storage, via boto3)
- Paystack sandbox (funding inflow + payout outflow)
- Docker + Docker Compose

---

## Project Status

Planning phase complete. Ready for implementation.
See 12_progress_tracker.md for current implementation state.
