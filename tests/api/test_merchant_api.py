"""
API integration tests for the Merchants component.

Endpoints covered
-----------------
POST   /api/v1/merchants             – create merchant profile
GET    /api/v1/merchants/me          – get own merchant profile
POST   /api/v1/merchants/me/api-key  – rotate API key
PATCH  /api/v1/merchants/me/webhook  – update webhook configuration
"""

import uuid

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import generate_api_key, hash_password
from app.models.merchant import Merchant
from app.models.user import User, UserRole
from app.models.wallet import Wallet
from tests.conftest import make_auth_headers

# ── URL helpers ───────────────────────────────────────────────────────────────

BASE = "/api/v1/merchants"
ME = f"{BASE}/me"
APIKEY = f"{ME}/api-key"
WEBHOOK = f"{ME}/webhook"


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def regular_user(db_session: AsyncSession) -> User:
    """A plain user with a wallet — eligible to become a merchant."""
    user = User(
        id=uuid.uuid4(),
        email="merchant_candidate@example.com",
        hashed_password=hash_password("Pass1234!"),
        full_name="Merchant Candidate",
        role=UserRole.USER,
        kyc_tier=0,
        is_active=True,
        is_email_verified=True,
    )
    db_session.add(user)
    wallet = Wallet(
        id=uuid.uuid4(),
        user_id=user.id,
        currency="NGN",
        is_active=True,
    )
    db_session.add(wallet)
    await db_session.flush()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def merchant_user(db_session: AsyncSession) -> User:
    """A user that already has a merchant profile."""
    user = User(
        id=uuid.uuid4(),
        email="existing_merchant@example.com",
        hashed_password=hash_password("Pass1234!"),
        full_name="Existing Merchant",
        role=UserRole.MERCHANT,
        kyc_tier=0,
        is_active=True,
        is_email_verified=True,
    )
    db_session.add(user)
    wallet = Wallet(
        id=uuid.uuid4(),
        user_id=user.id,
        currency="NGN",
        is_active=True,
    )
    db_session.add(wallet)
    await db_session.flush()
    return user


@pytest_asyncio.fixture
async def existing_merchant(
    db_session: AsyncSession, merchant_user: User
) -> Merchant:
    """Merchant profile pre-seeded for merchant_user."""
    _, prefix, hashed = generate_api_key()
    merchant = Merchant(
        id=uuid.uuid4(),
        user_id=merchant_user.id,
        business_name="Acme Corp",
        api_key_hash=hashed,
        api_key_prefix=prefix,
        webhook_secret=str(uuid.uuid4()),
        is_active=True,
    )
    db_session.add(merchant)
    await db_session.flush()
    await db_session.refresh(merchant)
    return merchant


# ── POST /merchants — create merchant ────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_merchant_unauthenticated(client: AsyncClient) -> None:
    resp = await client.post(BASE, json={"business_name": "My Shop"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_create_merchant_success(
    client: AsyncClient, db_session: AsyncSession, regular_user: User
) -> None:
    resp = await client.post(
        BASE,
        json={"business_name": "My Shop"},
        headers=make_auth_headers(regular_user),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["success"] is True
    data = body["data"]

    # Raw API key is present in the creation response
    assert "api_key" in data
    assert data["api_key"].startswith("pk_live_")

    # api_key_hash is never in the response
    assert "api_key_hash" not in data

    # Prefix matches the first 8 chars of the returned key
    assert data["api_key_prefix"] == data["api_key"][:8]
    assert data["business_name"] == "My Shop"
    assert data["user_id"] == str(regular_user.id)

    # User role was promoted to merchant
    await db_session.refresh(regular_user)
    assert regular_user.role == UserRole.MERCHANT


@pytest.mark.asyncio
async def test_create_merchant_short_name_rejected(
    client: AsyncClient, regular_user: User
) -> None:
    """Business name must be at least 2 characters."""
    resp = await client.post(
        BASE,
        json={"business_name": "X"},
        headers=make_auth_headers(regular_user),
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_merchant_duplicate_rejected(
    client: AsyncClient,
    merchant_user: User,
    existing_merchant: Merchant,
) -> None:
    """A user with an existing merchant profile cannot create another."""
    resp = await client.post(
        BASE,
        json={"business_name": "Another Shop"},
        headers=make_auth_headers(merchant_user),
    )
    assert resp.status_code == 409
    assert resp.json()["error"] == "MERCHANT_ALREADY_EXISTS"


# ── GET /merchants/me — get profile ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_merchant_profile_unauthenticated(client: AsyncClient) -> None:
    resp = await client.get(ME)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_get_merchant_profile_success(
    client: AsyncClient,
    merchant_user: User,
    existing_merchant: Merchant,
) -> None:
    resp = await client.get(ME, headers=make_auth_headers(merchant_user))
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["id"] == str(existing_merchant.id)
    assert data["business_name"] == "Acme Corp"
    assert data["api_key_prefix"] == existing_merchant.api_key_prefix
    # api_key and api_key_hash must never appear in the GET response
    assert "api_key" not in data
    assert "api_key_hash" not in data


@pytest.mark.asyncio
async def test_get_merchant_profile_not_merchant_returns_404(
    client: AsyncClient, regular_user: User
) -> None:
    """A plain user without a merchant profile gets 404."""
    resp = await client.get(ME, headers=make_auth_headers(regular_user))
    assert resp.status_code == 404


# ── POST /merchants/me/api-key — rotate key ───────────────────────────────────


@pytest.mark.asyncio
async def test_rotate_api_key_unauthenticated(client: AsyncClient) -> None:
    resp = await client.post(APIKEY)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_rotate_api_key_not_merchant_returns_404(
    client: AsyncClient, regular_user: User
) -> None:
    resp = await client.post(APIKEY, headers=make_auth_headers(regular_user))
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_rotate_api_key_returns_new_key(
    client: AsyncClient,
    db_session: AsyncSession,
    merchant_user: User,
    existing_merchant: Merchant,
) -> None:
    original_hash = existing_merchant.api_key_hash

    resp = await client.post(APIKEY, headers=make_auth_headers(merchant_user))
    assert resp.status_code == 200
    data = resp.json()["data"]

    assert "api_key" in data
    new_raw_key = data["api_key"]
    assert new_raw_key.startswith("pk_live_")

    # The stored hash was updated
    await db_session.refresh(existing_merchant)
    assert existing_merchant.api_key_hash != original_hash

    # The new raw key matches the new stored hash
    from app.core.security import verify_api_key
    assert verify_api_key(new_raw_key, existing_merchant.api_key_hash)


@pytest.mark.asyncio
async def test_rotate_api_key_invalidates_old_key(
    client: AsyncClient,
    db_session: AsyncSession,
    merchant_user: User,
    existing_merchant: Merchant,
) -> None:
    """After rotation, the old hash must no longer match any issued key."""
    old_hash = existing_merchant.api_key_hash

    await client.post(APIKEY, headers=make_auth_headers(merchant_user))
    await db_session.refresh(existing_merchant)

    # Old hash is gone — the new hash is different
    assert existing_merchant.api_key_hash != old_hash


# ── PATCH /merchants/me/webhook — update webhook ──────────────────────────────


@pytest.mark.asyncio
async def test_update_webhook_unauthenticated(client: AsyncClient) -> None:
    resp = await client.patch(WEBHOOK, json={"webhook_url": "https://example.com/hook"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_update_webhook_not_merchant_returns_404(
    client: AsyncClient, regular_user: User
) -> None:
    resp = await client.patch(
        WEBHOOK,
        json={"webhook_url": "https://example.com/hook"},
        headers=make_auth_headers(regular_user),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_webhook_url(
    client: AsyncClient,
    db_session: AsyncSession,
    merchant_user: User,
    existing_merchant: Merchant,
) -> None:
    resp = await client.patch(
        WEBHOOK,
        json={"webhook_url": "https://example.com/webhooks"},
        headers=make_auth_headers(merchant_user),
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["webhook_url"] == "https://example.com/webhooks"

    await db_session.refresh(existing_merchant)
    assert existing_merchant.webhook_url == "https://example.com/webhooks"


@pytest.mark.asyncio
async def test_update_webhook_regenerate_secret(
    client: AsyncClient,
    db_session: AsyncSession,
    merchant_user: User,
    existing_merchant: Merchant,
) -> None:
    original_secret = existing_merchant.webhook_secret

    resp = await client.patch(
        WEBHOOK,
        json={"regenerate_secret": True},
        headers=make_auth_headers(merchant_user),
    )
    assert resp.status_code == 200
    data = resp.json()["data"]

    # New secret is in the response and differs from the old one
    assert data["webhook_secret"] != original_secret

    await db_session.refresh(existing_merchant)
    assert existing_merchant.webhook_secret != original_secret


@pytest.mark.asyncio
async def test_update_webhook_no_fields_is_noop(
    client: AsyncClient,
    db_session: AsyncSession,
    merchant_user: User,
    existing_merchant: Merchant,
) -> None:
    """Sending an empty PATCH body changes nothing."""
    original_url = existing_merchant.webhook_url
    original_secret = existing_merchant.webhook_secret

    resp = await client.patch(
        WEBHOOK,
        json={},
        headers=make_auth_headers(merchant_user),
    )
    assert resp.status_code == 200

    await db_session.refresh(existing_merchant)
    assert existing_merchant.webhook_url == original_url
    assert existing_merchant.webhook_secret == original_secret


# ── API key never in GET response ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_api_key_hash_never_in_get_response(
    client: AsyncClient,
    merchant_user: User,
    existing_merchant: Merchant,
) -> None:
    """Verify api_key_hash and api_key are absent from the GET /me response."""
    resp = await client.get(ME, headers=make_auth_headers(merchant_user))
    assert resp.status_code == 200
    raw = resp.text
    assert "api_key_hash" not in raw
    # The full hash value should not appear anywhere in the response
    assert existing_merchant.api_key_hash not in raw
