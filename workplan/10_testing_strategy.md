# Testing Strategy — PayCore

## Philosophy

Tests reflect risk. Critical financial flows must have integration tests.
Supporting logic (fraud checks, KYC validation, balance math) must have unit tests.
Permission boundaries must be tested explicitly.

Not every line needs a test. Every critical path does.

---

## Test Types

### Unit Tests
Target: Service layer logic in isolation

- Fraud check rules (daily limit, frequency, amount thresholds)
- KYC tier limit enforcement
- Balance calculation correctness
- Idempotency key deduplication logic
- Ledger entry construction (correct debit/credit pairs)
- Token generation and validation (JWT)

### Integration Tests
Target: Full flow from request to DB state

- Wallet funding: mock Paystack webhook → wallet credited, ledger written, transaction completed
- Wallet-to-wallet transfer: balance debited/credited atomically, both ledger entries exist
- Merchant payment: user wallet debited, merchant wallet credited, webhook delivery queued
- Withdrawal initiation: balance held, transaction pending, Celery task queued
- KYC submission and admin approval: tier upgraded, limits change
- Concurrent transfer safety: two simultaneous transfers from same wallet (only one should succeed or both should succeed correctly without double-spend)

### API Tests
Target: HTTP contract + auth + permissions

- Auth: register, login, refresh, logout
- Unauthorized access returns 401
- Wrong role returns 403
- Merchant API key auth works on merchant endpoints
- Admin endpoints reject non-admin users
- Input validation: missing fields, invalid amounts, bad email format
- Pagination: respects limit/offset, returns correct metadata

### Worker Tests
Target: Celery task behavior

- Withdrawal payout task: success path (mock Paystack), failure path (reversal triggered)
- Merchant webhook delivery: delivered on first attempt, retried on failure, marked failed after max retries
- Reconciliation job: detects stale pending transactions, triggers verification

### Failure Path Tests

- Paystack webhook with invalid signature → rejected
- Duplicate webhook event → idempotency prevents double credit
- Transfer with insufficient balance → rejected, no ledger entries written
- Withdrawal with KYC Tier < 2 → rejected
- Concurrent transfer race: no double-spend possible

---

## Test Structure

```
tests/
  conftest.py          # shared fixtures: db session, test user, test wallet, test merchant
  unit/
    test_fraud.py
    test_kyc_limits.py
    test_ledger.py
    test_jwt.py
  integration/
    test_funding_flow.py
    test_transfer_flow.py
    test_merchant_payment_flow.py
    test_withdrawal_flow.py
    test_kyc_flow.py
  api/
    test_auth.py
    test_wallets.py
    test_transfers.py
    test_admin.py
    test_merchants.py
  workers/
    test_withdrawal_worker.py
    test_webhook_delivery_worker.py
```

---

## Test Environment

- Separate PostgreSQL test database (or SQLite in-memory for pure unit tests)
- Fixtures: create test users, wallets, merchants in DB before each test
- Paystack API: mocked via `unittest.mock` or `respx` (httpx mock library)
- S3: mocked via `moto` library
- Celery: run in `CELERY_ALWAYS_EAGER=True` mode (sync execution in tests)

---

## Coverage Expectations

| Area | Minimum Coverage |
|---|---|
| Transfer service | 90% |
| Ledger service | 95% |
| Fraud checks | 90% |
| KYC service | 85% |
| Withdrawal flow | 85% |
| Auth service | 85% |
| Webhook processing | 85% |
| Admin endpoints | 80% |

Overall target: 80% line coverage.
