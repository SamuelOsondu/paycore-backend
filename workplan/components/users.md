# Users Component

## Purpose
Owns the User entity. Manages user creation, profile data, role assignment, and KYC tier state.
It is the foundation all other components depend on for actor identity.

## Scope
### In Scope
- User model definition
- User creation (called by Auth on registration)
- User profile read/update
- Role field (user, merchant, admin)
- KYC tier field (read by all financial components)
- User lookup by ID, email

### Out of Scope
- Authentication and token management → Auth component
- KYC submission and document handling → KYC component
- Wallet creation → Wallets component (triggered after user creation)

## Responsibilities
- Define and own the `users` table
- Expose `UserRepository` with methods: `create`, `get_by_id`, `get_by_email`, `update_profile`, `update_kyc_tier`, `soft_delete`
- Expose `UserService` with: `get_profile`, `update_profile`, `update_kyc_tier`

## Dependencies
- None (foundation layer)

## Related Models
- `User`

## Related Endpoints
- `GET /api/v1/users/me` — get own profile
- `PATCH /api/v1/users/me` — update profile (full_name, phone)

## Business Rules
- One user per email (unique constraint enforced at DB level)
- Role defaults to `user` on creation; can only be elevated by admin action (not via API)
- KYC tier defaults to 0; only updated by KYC approval flow
- Users cannot change their own role or KYC tier via profile update — enforced by schema (extra='forbid') + service signature
- `is_active=false` users cannot authenticate

## Security Considerations
- Password hash not returned in any response (not in UserOut schema)
- `deleted_at` not returned in any response
- `role` and `kyc_tier` not in UserUpdateRequest — enforced at schema level
- Rate limit profile update: 10/min per user (to be wired via slowapi in Auth component)

## Performance Considerations
- `get_by_email` is a hot path during login — `ix_users_email` unique index in place
- Profile reads are infrequent; no caching needed

## Reliability Considerations
- User creation is called inside Auth service transaction — consistent or rolled back together
- Soft-deleted users excluded from all repo reads via explicit `WHERE deleted_at IS NULL`

## Testing Expectations
- Unit: profile update does not allow role/tier modification ✓
- Unit: soft-deleted user is not found ✓
- Unit: phone conflict raises ConflictError ✓
- Unit: update_kyc_tier only available via dedicated method ✓
- API: GET /users/me returns correct profile ✓
- API: PATCH /users/me updates allowed fields only ✓
- API: Unauthenticated access returns 401 ✓
- API: Unknown fields (e.g. role) rejected with 422 ✓
- API: Blank full_name rejected ✓
- API: Response always follows {success, message, data} shape ✓

## Implementation Notes
- `UserRepository.update_profile(user, ...)` — operates on already-loaded User instance
- `UserService.update_kyc_tier` is a dedicated method, not part of the general update
- Wallet creation not here — Wallets component handles that
- `app/core/security.py` owns all JWT and password utilities (not this component)
- `app/core/deps.py` owns `get_current_user` dependency (not this component)

## Status
complete

## Pending Tasks
- None — all tasks complete

## Completion Notes
- User model implemented with TimestampMixin + SoftDeleteMixin
- UserRole enum: user, merchant, admin
- UserRepository: create, get_by_id, get_by_email, get_by_phone, update_profile, update_kyc_tier, set_active, soft_delete
- UserService: get_profile, update_profile (with phone conflict check), update_kyc_tier
- UserOut schema: exposes safe fields only (no password, no deleted_at)
- UserUpdateRequest schema: extra='forbid', only full_name and phone allowed
- Standard response envelope applied to all endpoints
- 8 unit tests + 8 API tests written
- Initial Alembic migration creates users table with all indexes
