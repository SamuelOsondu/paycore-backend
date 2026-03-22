# Open Questions — PayCore

## Status: Planning Complete

All critical decisions resolved. The following are low-priority items deferred to implementation time.

---

## Open Items

### 1. Email Verification Flow
**Question:** Should email verification be required before a user can fund their wallet or perform Tier 0 actions?
**Current assumption:** Email verification is a nice-to-have. Users are created as `is_email_verified=false`. Not enforced as a gate for MVP.
**Resolve when:** Email notification service is added.

---

### 2. Bank Account Verification via Paystack
**Question:** Should the `POST /bank-accounts` endpoint call Paystack's `bank/resolve` to verify the account name before saving?
**Current assumption:** Yes — call Paystack `bank/resolve` to confirm account number + bank code returns a valid account name. Store as `is_verified=true`.
**Resolve when:** Implementing the Withdrawals component.

---

### 3. Merchant Webhook Retry Schedule
**Question:** What is the exact exponential backoff schedule for merchant webhook retries?
**Current assumption:** Attempt 1 immediately, then 2^n minutes: 2min, 4min, 8min, 16min, 32min = 5 total attempts.
**Resolve when:** Implementing Outgoing Webhooks component.

---

### 4. Admin Account Seeding
**Question:** How should the first admin user be created?
**Current assumption:** A management script `scripts/create_admin.py` that takes email + password as args and writes to DB with `role=admin`.
**Resolve when:** Implementing Admin component.

---

### 5. Reconciliation Job Behavior
**Question:** When the reconciliation job finds a stale pending transaction, should it automatically verify with Paystack and resolve, or just flag it for admin review?
**Current assumption:** Auto-verify with Paystack. If Paystack says `success`, process the credit. If Paystack says `failed`, mark transaction failed. Log both cases in audit.
**Resolve when:** Implementing Workers component.

---

### 6. Multi-currency Future Path
**Question:** Should currency be enforced as NGN at DB constraint level or just by application default?
**Current assumption:** Store `currency='NGN'` on wallet and transaction. Do not add DB constraint — allows multi-currency without migration later.
**Resolve when:** MVP is complete; revisit if multi-currency scope is added.

---

## Resolved Questions

| Question | Resolution |
|---|---|
| KYC document storage | S3 with boto3 |
| Wallet balance strategy | Maintained field + ledger |
| Payout rail | Paystack Transfer API sandbox |
| Auth token strategy | Access + refresh token pair |
| Real-time / WebSockets | Not needed for MVP |
| Fraud check execution | Synchronous blocking checks |
| Pagination style | Offset pagination |
