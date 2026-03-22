"""
API-layer tests for the Wallets endpoints.

Most tests use the test_user + test_wallet fixtures for speed.
The registration-flow test uses POST /auth/register to validate the
end-to-end atomic wallet creation on signup.
"""

from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.models.wallet import Wallet
from tests.conftest import make_auth_headers

pytestmark = pytest.mark.asyncio


# ── GET /api/v1/wallets/me ────────────────────────────────────────────────────

async def test_get_my_wallet_unauthenticated_returns_401(client: AsyncClient) -> None:
    response = await client.get("/api/v1/wallets/me")
    assert response.status_code == 401
    body = response.json()
    assert body["success"] is False


async def test_get_my_wallet_returns_200(
    client: AsyncClient, test_user: User, test_wallet: Wallet
) -> None:
    response = await client.get(
        "/api/v1/wallets/me", headers=make_auth_headers(test_user)
    )
    assert response.status_code == 200


async def test_get_my_wallet_response_envelope(
    client: AsyncClient, test_user: User, test_wallet: Wallet
) -> None:
    response = await client.get(
        "/api/v1/wallets/me", headers=make_auth_headers(test_user)
    )
    body = response.json()

    assert body["success"] is True
    assert "message" in body
    assert "data" in body
    assert body.get("error") is None


async def test_get_my_wallet_response_shape(
    client: AsyncClient, test_user: User, test_wallet: Wallet
) -> None:
    response = await client.get(
        "/api/v1/wallets/me", headers=make_auth_headers(test_user)
    )
    data = response.json()["data"]

    assert "id" in data
    assert "user_id" in data
    assert "currency" in data
    assert "balance" in data
    assert "is_active" in data
    assert "created_at" in data
    # Sensitive / internal fields must not be exposed
    assert "deleted_at" not in data
    assert "hashed_password" not in data


async def test_get_my_wallet_initial_balance_is_zero(
    client: AsyncClient, test_user: User, test_wallet: Wallet
) -> None:
    response = await client.get(
        "/api/v1/wallets/me", headers=make_auth_headers(test_user)
    )
    data = response.json()["data"]
    assert Decimal(data["balance"]) == Decimal("0.00")


async def test_get_my_wallet_currency_is_ngn(
    client: AsyncClient, test_user: User, test_wallet: Wallet
) -> None:
    response = await client.get(
        "/api/v1/wallets/me", headers=make_auth_headers(test_user)
    )
    assert response.json()["data"]["currency"] == "NGN"


async def test_get_my_wallet_user_id_matches_current_user(
    client: AsyncClient, test_user: User, test_wallet: Wallet
) -> None:
    response = await client.get(
        "/api/v1/wallets/me", headers=make_auth_headers(test_user)
    )
    data = response.json()["data"]
    assert data["user_id"] == str(test_user.id)


async def test_get_my_wallet_deactivated_wallet_returns_403(
    client: AsyncClient,
    db_session: AsyncSession,
    test_user: User,
    test_wallet: Wallet,
) -> None:
    from app.repositories.wallet import WalletRepository

    repo = WalletRepository(db_session)
    await repo.set_active(test_wallet, active=False)

    response = await client.get(
        "/api/v1/wallets/me", headers=make_auth_headers(test_user)
    )
    assert response.status_code == 403
    body = response.json()
    assert body["success"] is False
    assert body["error"] == "FORBIDDEN"


async def test_get_my_wallet_no_wallet_returns_404(
    client: AsyncClient, test_user: User
) -> None:
    """Authenticated user with no wallet gets 404 (wallet not found)."""
    response = await client.get(
        "/api/v1/wallets/me", headers=make_auth_headers(test_user)
    )
    assert response.status_code == 404
    body = response.json()
    assert body["success"] is False


# ── GET /api/v1/wallets/me/transactions ───────────────────────────────────────

async def test_get_my_transactions_unauthenticated_returns_401(
    client: AsyncClient,
) -> None:
    response = await client.get("/api/v1/wallets/me/transactions")
    assert response.status_code == 401


async def test_get_my_transactions_returns_200(
    client: AsyncClient, test_user: User, test_wallet: Wallet
) -> None:
    response = await client.get(
        "/api/v1/wallets/me/transactions",
        headers=make_auth_headers(test_user),
    )
    assert response.status_code == 200


async def test_get_my_transactions_response_envelope(
    client: AsyncClient, test_user: User, test_wallet: Wallet
) -> None:
    response = await client.get(
        "/api/v1/wallets/me/transactions",
        headers=make_auth_headers(test_user),
    )
    body = response.json()

    assert body["success"] is True
    data = body["data"]
    assert "items" in data
    assert "total" in data
    assert "limit" in data
    assert "offset" in data


async def test_get_my_transactions_stub_returns_empty_list(
    client: AsyncClient, test_user: User, test_wallet: Wallet
) -> None:
    response = await client.get(
        "/api/v1/wallets/me/transactions",
        headers=make_auth_headers(test_user),
    )
    data = response.json()["data"]
    assert data["items"] == []
    assert data["total"] == 0


async def test_get_my_transactions_default_pagination(
    client: AsyncClient, test_user: User, test_wallet: Wallet
) -> None:
    response = await client.get(
        "/api/v1/wallets/me/transactions",
        headers=make_auth_headers(test_user),
    )
    data = response.json()["data"]
    assert data["limit"] == 20
    assert data["offset"] == 0


async def test_get_my_transactions_custom_pagination(
    client: AsyncClient, test_user: User, test_wallet: Wallet
) -> None:
    response = await client.get(
        "/api/v1/wallets/me/transactions?limit=5&offset=10",
        headers=make_auth_headers(test_user),
    )
    data = response.json()["data"]
    assert data["limit"] == 5
    assert data["offset"] == 10


async def test_get_my_transactions_limit_too_large_returns_422(
    client: AsyncClient, test_user: User, test_wallet: Wallet
) -> None:
    response = await client.get(
        "/api/v1/wallets/me/transactions?limit=999",
        headers=make_auth_headers(test_user),
    )
    assert response.status_code == 422


async def test_get_my_transactions_no_wallet_returns_404(
    client: AsyncClient, test_user: User
) -> None:
    """User with no wallet gets 404 from the statement endpoint."""
    response = await client.get(
        "/api/v1/wallets/me/transactions",
        headers=make_auth_headers(test_user),
    )
    assert response.status_code == 404


# ── Registration auto-creates wallet ──────────────────────────────────────────

async def test_register_auto_creates_wallet(client: AsyncClient) -> None:
    """
    End-to-end: POST /auth/register creates both a user AND a wallet atomically.
    The new user should immediately be able to GET /wallets/me.
    """
    reg = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "wallettest@example.com",
            "password": "Secure123",
            "full_name": "Wallet Tester",
        },
    )
    assert reg.status_code == 201
    access_token = reg.json()["data"]["access_token"]

    wallet_resp = await client.get(
        "/api/v1/wallets/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert wallet_resp.status_code == 200
    data = wallet_resp.json()["data"]
    assert data["currency"] == "NGN"
    assert Decimal(data["balance"]) == Decimal("0.00")
