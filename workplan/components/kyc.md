# KYC Component

## Purpose
Manages KYC tier submission, document upload to S3, admin review, and tier promotion.
KYC tier is the primary control gate for transaction limits and withdrawal eligibility.

## Scope
### In Scope
- KYC submission by user (Tier 1 and Tier 2 requests)
- Document upload (image/PDF to S3)
- Submission status tracking
- Admin review: approve or reject with reason
- Tier promotion on approval (calls UserService.update_kyc_tier)
- Admin view: list pending submissions, view document via presigned URL

### Out of Scope
- KYC limit enforcement during transactions → Fraud component
- User profile data → Users component

## Responsibilities
- `KYCRepository`: CRUD on `kyc_submissions`
- `KYCService`: submit_kyc, approve_kyc, reject_kyc, get_submission, get_presigned_doc_url
- `StorageService`: upload to S3, generate presigned URL
- Validate file type (image/jpeg, image/png, application/pdf) and size (max 5MB)

## Dependencies
- Users component (UserService.update_kyc_tier)
- S3 integration (StorageService)
- Audit component (emit audit log on submission, approval, rejection)

## Related Models
- `KYCSubmission`
- `User` (read kyc_tier, update kyc_tier)

## Related Endpoints
- `POST /api/v1/kyc/submit` — user submits KYC with document upload (multipart/form-data)
- `GET /api/v1/kyc/me` — user views their KYC submission status
- `GET /api/v1/admin/kyc` — admin lists pending submissions (paginated)
- `GET /api/v1/admin/kyc/{submission_id}` — admin views submission + presigned doc URL
- `POST /api/v1/admin/kyc/{submission_id}/approve` — admin approves
- `POST /api/v1/admin/kyc/{submission_id}/reject` — admin rejects with reason

## Business Rules
- A user can only have one active (non-rejected) submission per tier at a time
- User must be at Tier N-1 to apply for Tier N (cannot skip tiers)
- Approval upgrades user kyc_tier to the requested tier
- Rejection sets status to `rejected`; user may resubmit
- Only admins can approve or reject
- Admin cannot review their own KYC submissions

## Security Considerations
- Document S3 keys never returned to users; only presigned URLs for admins
- KYC endpoint requires authenticated user
- Admin endpoints require `admin` role
- File upload: validate MIME type from file bytes (not just extension)
- S3 object key pattern: `kyc/{user_id}/{submission_id}/{original_filename}`
- Presigned URL expiry: 1 hour

## Performance Considerations
- S3 upload is sync (boto3) — run in `asyncio.run_in_executor` to avoid blocking
- Admin list: paginated, filter by `status=pending` default

## Reliability Considerations
- If S3 upload fails, submission is not saved to DB (transaction not committed)
- Approval is idempotent: approving an already-approved submission returns 400

## Testing Expectations
- Integration: full submission → admin approval → user tier upgraded flow
- Integration: rejection flow, user can resubmit
- API: non-admin cannot access admin KYC endpoints
- Failure: S3 upload failure → 503, no DB record created
- Edge: double submission for same tier while pending → 409 conflict

## Implementation Notes
- Use `python-multipart` for file upload handling in FastAPI
- `StorageService.upload_file(file_bytes: bytes, key: str) -> str`
- `StorageService.get_presigned_url(key: str, expiry: int = 3600) -> str`
- KYC approval must call `UserService.update_kyc_tier` within same DB transaction

## Status
complete

## Pending Tasks
- None

## Completion Notes
- `app/models/kyc_submission.py` — `KYCStatus` enum (pending/approved/rejected); `KYCSubmission` model with `TimestampMixin` (no SoftDeleteMixin — compliance records); `document_key` stored but never returned to users; `reviewer_id` FK with `ondelete="SET NULL"`
- `alembic/versions/g7h8i9j0k1l2_create_kyc_submissions_table.py` — creates `kycstatus` PG enum, `kyc_submissions` table with FKs to `users.id`; 2 indexes (user_id, status); chains from `f6a7b8c9d0e1`
- `app/integrations/storage.py` — `StorageService`: async `upload_file` and `get_presigned_url` backed by boto3 via `run_in_executor`; `detect_mime_type` using magic bytes (JPEG/PNG/PDF); `MAX_FILE_SIZE = 5MB`; raises `ExternalServiceError` on S3 failure
- `app/repositories/kyc.py` — `KYCRepository`: `create`, `get_by_id`, `get_latest_for_user`, `get_active_for_tier` (non-rejected constraint), `list_by_status` (paginated)
- `app/schemas/kyc.py` — `KYCSubmitRequest`, `KYCRejectRequest` (min_length=10), `KYCSubmissionOut` (no document_key), `KYCSubmissionAdminOut` (adds `reviewer_id`, `document_url`)
- `app/services/kyc.py` — `KYCService`: `submit_kyc` (validates file, tier progression, active-sub uniqueness, S3 upload before DB insert, commit), `get_my_submission`, `get_submission`, `approve_kyc` (idempotent + tier promotion + commit), `reject_kyc` (commit), `get_presigned_doc_url`, `list_submissions`
- `app/api/v1/kyc.py` — `POST /kyc/submit` (multipart), `GET /kyc/me`
- `app/api/v1/admin.py` — admin router: `GET /admin/kyc`, `GET /admin/kyc/{id}`, `POST /admin/kyc/{id}/approve`, `POST /admin/kyc/{id}/reject`; all require `admin` role via `require_role("admin")`
- `app/api/v1/router.py` — `kyc` and `admin` routers included
- `app/models/__init__.py` + `alembic/env.py` — `KYCSubmission` registered
- `tests/api/test_kyc_api.py` — 28 API tests: submit flow (auth, file validation, tier skip, double-pending, resubmit-after-reject, S3 failure), get-me, admin list (role guard, status filter), admin detail (presigned URL), admin approve (tier upgrade, idempotent guard, own-submission guard), admin reject (reason, non-pending guard, short-reason 422, own-submission guard), full end-to-end approval and rejection+resubmit flows
