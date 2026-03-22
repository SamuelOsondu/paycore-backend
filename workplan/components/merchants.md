# Merchants Component

## Purpose
Manages merchant profiles, wallet creation, API key generation, and webhook configuration.
Merchants are businesses that receive payments from users via the platform.

## Scope
### In Scope
- Merchant profile creation (user promotes themselves to merchant)
- Merchant wallet creation (on merchant profile creation)
- API key generation (shown once on creation, stored hashed)
- API key rotation
- Webhook URL and secret management
- Merchant profile read

### Out of Scope
- Receiving payments from users → Merchant Payments component
- Payment history → Merchant Payments / Transactions component
- Webhook delivery → Outgoing Webhooks component

## Responsibilities
- `MerchantRepository`: create, get_by_id, get_by_user_id, get_by_api_key_hash
- `MerchantService`: create_merchant, rotate_api_key, update_webhook_config, get_merchant
- `MerchantAuthService`: verify_api_key — used by merchant-facing auth dependency

## Dependencies
- Users component (user_id FK, user must exist and not already be a merchant)
- Wallets component (create merchant wallet on merchant creation)
- Audit component

## Related Models
- `Merchant`
- `Wallet` (owned by merchant)

## Related Endpoints
- `POST /api/v1/merchants` — create merchant profile (authenticated user)
- `GET /api/v1/merchants/me` — get own merchant profile
- `POST /api/v1/merchants/me/api-key` — rotate API key (returns new key once)
- `PATCH /api/v1/merchants/me/webhook` — update webhook URL and secret

## Business Rules
- One merchant profile per user (enforced by UNIQUE on `user_id`)
- Creating a merchant profile creates a wallet for the merchant
- API key is a UUID4-based string: `pk_live_{uuid4_hex}` format
- API key is hashed (bcrypt) before storage; the raw key is shown only once in the response
- `api_key_prefix` (first 8 chars of raw key) stored for display/identification
- Merchants can update their webhook URL and can regenerate webhook secret
- A user with `role=merchant` may still use user endpoints (backwards compatible)

## Security Considerations
- API key verification: hash incoming key with bcrypt and compare
- Merchant-facing endpoints (receiving webhooks, payment confirmation) use API key auth
- `get_merchant_from_api_key` FastAPI dependency for API key authentication
- Webhook secret is a random UUID4 — used to sign outgoing delivery payloads
- Rotating API key invalidates the old one immediately

## Performance Considerations
- API key lookup: bcrypt hash is slow — `api_key_prefix` can be used to narrow lookup before bcrypt compare
- Merchant profile read: simple PK lookup

## Reliability Considerations
- Merchant creation (profile + wallet) must be atomic: single DB transaction
- API key rotation: write new hash before invalidating old in same transaction

## Testing Expectations
- Integration: merchant creation creates profile + wallet
- API: API key shown only once on creation
- API: rotated API key invalidates old one
- API: non-merchant user cannot access merchant-only endpoints
- API: one merchant per user enforcement

## Implementation Notes
- `create_merchant`: begin transaction → create Merchant record → call WalletService.create_wallet → commit
- API key generation: `f"pk_live_{uuid.uuid4().hex}"` → bcrypt hash → store hash + prefix
- FastAPI dep: `get_merchant_from_api_key(api_key: str = Header(...))` → returns Merchant or 401

## Status
complete

## Pending Tasks
- None

## Completion Notes
- `app/models/merchant.py` — `Merchant` model with `TimestampMixin` + `SoftDeleteMixin`; UNIQUE on `user_id`; `api_key_hash` (bcrypt, never returned), `api_key_prefix` (first 8 chars, indexed, for pre-filter), `webhook_url`, `webhook_secret` (UUID4, for HMAC signing), `is_active`
- `alembic/versions/h8i9j0k1l2m3_create_merchants_table.py` — creates `merchants` table; 3 indexes (user_id, api_key_prefix, deleted_at); chains from `g7h8i9j0k1l2`
- `app/repositories/merchant.py` — `MerchantRepository`: `create`, `get_by_id`, `get_by_user_id`, `get_active_by_prefix` (for API key auth pre-filter), `update_api_key`, `update_webhook`, `soft_delete`
- `app/schemas/merchant.py` — `CreateMerchantRequest` (business_name min=2), `UpdateWebhookRequest` (webhook_url optional, regenerate_secret bool), `MerchantOut` (no api_key_hash; includes webhook_secret for merchant HMAC verification), `MerchantCreatedOut` (extends MerchantOut with api_key field)
- `app/services/merchant.py` — `MerchantService`: `create_merchant` (duplicate guard, generate_api_key, ensure wallet exists, promote user.role to MERCHANT, commit), `get_merchant_profile`, `rotate_api_key` (atomically replaces hash + prefix, commit), `update_webhook_config` (partial update, optional secret regeneration, commit); `MerchantAuthService`: `authenticate` (prefix pre-filter → bcrypt verify each candidate → UnauthorizedError if none match)
- `app/core/deps.py` — `get_merchant_from_api_key` dependency added (`X-API-Key` header → `MerchantAuthService.authenticate`)
- `app/api/v1/merchants.py` — 4 endpoints: `POST /merchants` (201), `GET /merchants/me` (200), `POST /merchants/me/api-key` (200), `PATCH /merchants/me/webhook` (200); all require `get_current_user`
- `app/api/v1/router.py` + `app/models/__init__.py` + `alembic/env.py` — Merchant registered
- `tests/api/test_merchant_api.py` — 16 API tests: unauthenticated guards (3), create success (role promoted, api_key in response, prefix matches), short name 422, duplicate 409, get profile (no api_key_hash/api_key), get profile 404 for non-merchant, rotate key (new key returned, old hash replaced, new key verifiable), rotate key 404, update webhook URL, regenerate secret, no-op PATCH, api_key_hash never in GET response
