# API Integrations — PayCore

## 1. Paystack

### Purpose
External payment rail for:
- Wallet funding initialization (charge users via card/bank transfer)
- Payment event verification
- Withdrawal payouts to bank accounts (Transfer API)
- Incoming webhooks for payment events

### Provider
Paystack — chosen by project brief. Nigerian fintech standard.
Docs: https://paystack.com/docs/api/

### Auth
- Bearer token: `Authorization: Bearer {PAYSTACK_SECRET_KEY}`
- Webhook: `x-paystack-signature` header — HMAC SHA512 of raw request body using `PAYSTACK_WEBHOOK_SECRET`

### Sandbox
- All operations use Paystack test keys (`sk_test_...`)
- Test cards and virtual bank accounts available in dashboard
- Webhook delivery via Paystack dashboard or ngrok for local testing

### Endpoints Used

| Endpoint | Purpose |
|---|---|
| `POST /transaction/initialize` | Create payment session for wallet funding |
| `GET /transaction/verify/{reference}` | Verify payment status |
| `POST /transferrecipient` | Register bank account as payout recipient |
| `POST /transfer` | Initiate withdrawal payout |
| `GET /transfer/{transfer_code}` | Check transfer status |

### Rate Limits
- Not publicly published; treat as 100 req/min per secret key
- Idempotency: use `reference` field (unique per transaction) on initialize

### Webhook Events Handled

| Event | Action |
|---|---|
| `charge.success` | Credit user wallet, complete transaction |
| `transfer.success` | Finalize withdrawal, update transaction to completed |
| `transfer.failed` | Mark withdrawal failed, reverse balance hold |
| `transfer.reversed` | Record reversal, update ledger |

### Failure Modes
- Paystack API unavailable → return 503, do not create transaction record
- Webhook not received → reconciliation job polls pending transactions older than 30 min
- Transfer failure → Celery task handles failure webhook, triggers reversal service

### Integration Location
`app/integrations/paystack.py` — `PaystackClient` class with async methods

### Idempotency
- Every Paystack transaction initialized with a unique `reference` (UUID)
- Webhook events deduplicated by checking `reference` in `transactions` table before processing

---

## 2. AWS S3

### Purpose
Store KYC document uploads (ID images, selfies) with private access control.

### Auth
- `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION` from environment
- Bucket: `S3_BUCKET_NAME`, private ACL on all objects

### Upload Pattern
- FastAPI receives multipart file upload
- File validated (type: image/jpeg, image/png, application/pdf; max size: 5MB)
- Uploaded to S3 as `kyc/{user_id}/{kyc_id}/{filename}` via `boto3`
- S3 object key stored in `kyc_submissions.document_key`
- Access: generate presigned URL (1-hour expiry) when admin needs to view document

### Integration Location
`app/integrations/storage.py` — `StorageService` class
Methods: `upload_file(file_bytes, key) → str`, `get_presigned_url(key, expiry=3600) → str`

### Failure Modes
- S3 upload failure → reject KYC submission with 503
- Presigned URL generation failure → log, return 503

---

## 3. Internal Mock Payout (Fallback)

### Purpose
When `MOCK_PAYOUT=true` in environment, simulate payout success/failure without calling Paystack.

### Behavior
- Celery task sleeps 5 seconds, then calls internal `payout_success_handler` or `payout_failure_handler`
- Deterministic: references ending in `0` fail, all others succeed (for testing)

### Location
`app/integrations/mock_payout.py`

---

## Integration Wrapper Rules

- All external HTTP calls go through dedicated client classes in `app/integrations/`
- No raw `httpx` or `requests` calls scattered in services
- Clients raise typed exceptions: `PaystackError`, `StorageError`
- Services catch these and translate to business-level errors
- Timeout: 10 seconds on all outbound HTTP calls
- Retry: not at client level — retries handled by Celery task retry mechanism
