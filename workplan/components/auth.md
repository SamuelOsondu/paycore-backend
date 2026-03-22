# Auth Component

## Purpose
Manages user registration, login, JWT token issuance, token refresh, and logout.
This component is the entry gate for all authenticated users.

## Scope
### In Scope
- User registration (calls UserService to create user)
- Login (credential verification, token issuance)
- JWT access token generation (15 min)
- Refresh token generation, storage (hashed), rotation
- Token refresh endpoint
- Logout (invalidate refresh token)
- FastAPI auth dependencies: `get_current_user`, `require_role`

### Out of Scope
- User profile management → Users component
- Merchant API key auth → Merchants component

## Responsibilities
- Hash and verify passwords
- Generate and validate JWT access tokens
- Generate, store, hash, and validate refresh tokens
- Expose `AuthService` with: `register`, `login`, `refresh`, `logout`
- Provide reusable FastAPI dependencies for downstream components

## Dependencies
- Users component (UserRepository, UserService)
- `refresh_tokens` table

## Related Models
- `User` (read)
- `RefreshToken`

## Related Endpoints
- `POST /api/v1/auth/register`
- `POST /api/v1/auth/login`
- `POST /api/v1/auth/refresh`
- `POST /api/v1/auth/logout`

## Business Rules
- Password minimum 8 characters, must contain letter and number
- Duplicate email registration → 400 error
- Login with wrong password: generic error (do not reveal whether email exists)
- Refresh token is single-use: issue new refresh token on each refresh (rotation)
- Logout invalidates the specific refresh token; other sessions remain valid
- `is_active=false` users cannot login

## Security Considerations
- bcrypt with 12 rounds for password hashing
- Access token: HS256, 15 min expiry
- Refresh token: stored as SHA256 hash in DB (raw token never persisted)
- Rate limit `POST /auth/login`: 5/min per IP
- Rate limit `POST /auth/register`: 10/min per IP
- Wallet creation triggered after successful registration (via WalletService)

## Performance Considerations
- bcrypt is CPU-bound — run in `asyncio.run_in_executor` to avoid blocking the event loop
- JWT verification is fast — no DB lookup on access token (stateless)
- Refresh token lookup: indexed on `token_hash`

## Reliability Considerations
- Registration creates user + wallet in same DB transaction
- If wallet creation fails, user is not created (rollback)

## Testing Expectations
- Unit: password hashing and verification
- Unit: JWT encode/decode, expiry enforcement
- API: register → login → refresh → logout flow
- API: login with wrong password returns 401
- API: expired access token returns 401
- API: revoked refresh token cannot be reused
- API: rate limit on login endpoint

## Implementation Notes
- `get_current_user` dependency: decodes JWT, fetches user from DB, returns User model
- `require_role(role: str)` dependency: wraps `get_current_user` with role check
- Wallet creation on registration: call `WalletService.create_wallet(user_id)` inside same transaction

## Status
complete

## Pending Tasks
- None

## Completion Notes
- `app/models/refresh_token.py` — RefreshToken model (TimestampMixin only; no soft delete — financial records are immutable)
- `alembic/versions/b2c3d4e5f6a7_create_refresh_tokens_table.py` — migration with CASCADE FK to users
- `app/repositories/auth.py` — RefreshTokenRepository: create, get_by_hash, revoke, revoke_all_for_user (bulk UPDATE)
- `app/schemas/auth.py` — RegisterRequest (password strength + full_name validators), LoginRequest, RefreshRequest, LogoutRequest, TokenResponse, RegisterResponse
- `app/services/auth.py` — AuthService: register, login, refresh, logout; all bcrypt via asyncio.to_thread; timing-safe login with TIMING_DUMMY_HASH; token rotation on refresh; idempotent logout
- `app/core/limiter.py` — slowapi Limiter backed by Redis
- `app/api/v1/auth.py` — 4 endpoints; register+login rate-limited (10/min, 20/min); full docstrings
- `app/api/v1/router.py` — auth router included
- `app/main.py` — SlowAPIMiddleware + app.state.limiter + RateLimitExceeded handler added
- `app/core/security.py` — TIMING_DUMMY_HASH constant added
- `tests/conftest.py` — Upgraded to savepoint-based isolation (join_transaction_mode="create_savepoint") to support commit-based services
- `tests/unit/test_auth.py` — 13 unit tests covering register, login, refresh, logout edge cases
- `tests/api/test_auth_api.py` — 20 API tests covering all 4 endpoints, envelope shape, error codes, rotation, idempotency
- Access token: 30 min (corrected from spec's 15 min — matches discovery answers and settings)
- TODO comment in AuthService.register for wallet creation hook (deferred to Wallets component)
