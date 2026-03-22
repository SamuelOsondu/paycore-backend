# Security and Risk — PayCore

## Risk Assessment

**Risk Level: HIGH**

This system handles real user funds, identity documents, and irreversible financial operations.
Every security decision must reflect this risk level.

---

## Authentication

- JWT HS256 with 15-minute access tokens
- Refresh tokens stored hashed (SHA256) in DB; invalidated on logout
- Passwords hashed with bcrypt (12 rounds)
- Login returns both access and refresh tokens
- `/auth/refresh` issues new access token from valid refresh token
- Rate limit login: **5 requests/minute per IP**

---

## Authorization

Three roles: `user`, `merchant`, `admin`

- FastAPI dependency `require_role(role)` enforces access at router level
- Users can only access their own wallet, transactions, KYC
- Merchants authenticated via API key on merchant-facing endpoints
- Admin endpoints: only `admin` role — all admin actions are audit-logged
- Ownership checks in service layer: never rely solely on route-level role check

---

## API Key Security (Merchants)

- Generated as `pk_live_{UUID4}` style string on merchant creation
- Shown **once** in the creation response; not retrievable again
- Stored as bcrypt hash in `merchants.api_key_hash`
- `api_key_prefix` (first 8 chars) stored plaintext for identification
- Lookup: hash incoming key, compare bcrypt digest

---

## Paystack Webhook Verification

- `x-paystack-signature` header: HMAC SHA512 of raw request body using `PAYSTACK_WEBHOOK_SECRET`
- Reject any webhook request with missing or invalid signature with 401
- Process webhook body only after signature passes
- Idempotency check: query `transactions` by `provider_reference` before any state change

---

## Outgoing Merchant Webhook Signing

- Each merchant has a `webhook_secret` (UUID4 generated on creation)
- Outgoing webhook payloads are HMAC SHA256 signed with this secret
- Signature sent as `X-PayCore-Signature` header
- Merchants can verify authenticity on their end

---

## Sensitive Operations

All of the following require:
- Active JWT (or API key for merchants)
- KYC tier enforcement
- Balance check before execution
- Idempotency key check

Operations: wallet funding, transfer, merchant payment, withdrawal

---

## KYC Controls

| Tier | Allowed Actions |
|---|---|
| 0 | View wallet, fund wallet (up to 10,000 NGN/day) |
| 1 | Transfers up to 50,000 NGN/day, merchant payments |
| 2 | Full limits, withdrawal enabled (up to 500,000 NGN/day) |

- Limits stored as env variables (not hardcoded)
- Tier checked synchronously before any money movement
- KYC document (S3) only accessible to admin via presigned URL — not exposed to users

---

## Fraud and Abuse Controls

Synchronous checks (block transaction if triggered):
1. Balance insufficient
2. Daily transfer volume exceeded for KYC tier
3. Amount exceeds single-transaction limit for KYC tier
4. Duplicate transfer detected: same sender + recipient + amount within 60 seconds

Async flags (do not block, but record for review):
5. Large merchant payment (> 100,000 NGN single payment from Tier 1)
6. Rapid succession transfers (> 5 transfers to different recipients in 10 minutes)

Flagged transactions:
- Set `metadata.risk_flag = true` on transaction record
- Emit `fraud.flagged` audit log entry
- Admin can view flagged transactions in admin dashboard

---

## Input Validation

- All request bodies validated via Pydantic v2 schemas
- Amount fields: must be positive, max 2 decimal places, max value enforced
- Email: validated format
- Phone: E.164 format required for Tier 1 KYC
- File uploads: MIME type check + size limit (5MB)
- Reject unknown fields via Pydantic `model_config = ConfigDict(extra='forbid')`

---

## Data Exposure Rules

- Passwords never returned in any response
- API keys shown only on creation, never in subsequent reads
- KYC document S3 keys never returned to non-admin users
- Internal error details not exposed: generic message + error code returned; full trace logged
- `balance_after` in ledger entries: returned only to the wallet owner

---

## Rate Limiting (slowapi)

| Endpoint | Limit |
|---|---|
| `POST /auth/login` | 5/minute per IP |
| `POST /auth/register` | 10/minute per IP |
| `POST /transfers` | 10/minute per user |
| `POST /withdrawals` | 5/minute per user |
| `POST /wallets/fund` | 10/minute per user |
| All other endpoints | 60/minute per user |

---

## Admin Hardening

- Admin endpoints under `/api/v1/admin/` prefix
- Extra dependency: `require_role("admin")` + `log_admin_action` (writes audit log)
- Admins cannot modify their own KYC status
- Admin account creation: seeded via management script, not via API

---

## Audit Logging Policy

The following actions always generate an audit log entry:

- user.registered
- user.login
- kyc.submitted
- kyc.approved / kyc.rejected
- wallet.funded
- transfer.initiated / transfer.completed / transfer.failed
- merchant_payment.completed
- withdrawal.initiated / withdrawal.completed / withdrawal.failed
- merchant.created
- api_key.generated
- admin.kyc_reviewed
- admin.transaction_flagged
- webhook_delivery.failed (after max retries)
