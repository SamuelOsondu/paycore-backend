"""
API-layer tests for the Auth endpoints.

Tests call the actual HTTP routes through the ASGI test client, exercising
the full stack from router → service → repository → database.
The outer transaction in conftest rolls back all changes after each test.
"""

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio

# ── Payloads ──────────────────────────────────────────────────────────────────

VALID_REGISTER = {
    "email": "carol@example.com",
    "password": "Secure123",
    "full_name": "Carol Test",
}

VALID_LOGIN = {
    "email": "carol@example.com",
    "password": "Secure123",
}


# ── POST /api/v1/auth/register ────────────────────────────────────────────────

async def test_register_returns_201(client: AsyncClient) -> None:
    response = await client.post("/api/v1/auth/register", json=VALID_REGISTER)
    assert response.status_code == 201


async def test_register_response_envelope(client: AsyncClient) -> None:
    response = await client.post("/api/v1/auth/register", json=VALID_REGISTER)
    body = response.json()

    assert body["success"] is True
    assert "message" in body
    assert "data" in body

    data = body["data"]
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"
    assert data["expires_in"] > 0
    assert "user" in data

    user = data["user"]
    assert user["email"] == "carol@example.com"
    assert user["full_name"] == "Carol Test"
    # Sensitive fields must never appear in the response
    assert "hashed_password" not in user
    assert "deleted_at" not in user


async def test_register_duplicate_email_returns_409(client: AsyncClient) -> None:
    await client.post("/api/v1/auth/register", json=VALID_REGISTER)
    response = await client.post("/api/v1/auth/register", json=VALID_REGISTER)

    assert response.status_code == 409
    body = response.json()
    assert body["success"] is False
    assert body["error"] == "EMAIL_CONFLICT"


async def test_register_weak_password_returns_422(client: AsyncClient) -> None:
    payload = {**VALID_REGISTER, "password": "short"}
    response = await client.post("/api/v1/auth/register", json=payload)

    assert response.status_code == 422
    body = response.json()
    assert body["success"] is False
    assert body["error"] == "VALIDATION_ERROR"


async def test_register_password_missing_digit_returns_422(client: AsyncClient) -> None:
    payload = {**VALID_REGISTER, "password": "NoDigitHere"}
    response = await client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 422


async def test_register_password_missing_letter_returns_422(client: AsyncClient) -> None:
    payload = {**VALID_REGISTER, "password": "12345678"}
    response = await client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 422


async def test_register_blank_full_name_returns_422(client: AsyncClient) -> None:
    payload = {**VALID_REGISTER, "full_name": "   "}
    response = await client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 422


async def test_register_invalid_email_returns_422(client: AsyncClient) -> None:
    payload = {**VALID_REGISTER, "email": "not-an-email"}
    response = await client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 422


async def test_register_unknown_field_rejected(client: AsyncClient) -> None:
    payload = {**VALID_REGISTER, "is_admin": True}
    response = await client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 422


# ── POST /api/v1/auth/login ───────────────────────────────────────────────────

async def test_login_returns_200(client: AsyncClient) -> None:
    await client.post("/api/v1/auth/register", json=VALID_REGISTER)
    response = await client.post("/api/v1/auth/login", json=VALID_LOGIN)
    assert response.status_code == 200


async def test_login_response_envelope(client: AsyncClient) -> None:
    await client.post("/api/v1/auth/register", json=VALID_REGISTER)
    response = await client.post("/api/v1/auth/login", json=VALID_LOGIN)
    body = response.json()

    assert body["success"] is True
    data = body["data"]
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"


async def test_login_wrong_password_returns_401(client: AsyncClient) -> None:
    await client.post("/api/v1/auth/register", json=VALID_REGISTER)
    response = await client.post(
        "/api/v1/auth/login",
        json={**VALID_LOGIN, "password": "WrongPass1"},
    )

    assert response.status_code == 401
    body = response.json()
    assert body["success"] is False
    assert body["error"] == "UNAUTHORIZED"


async def test_login_unknown_email_returns_401(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "ghost@example.com", "password": "Secret123"},
    )
    assert response.status_code == 401
    assert response.json()["success"] is False


async def test_login_missing_password_returns_422(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "carol@example.com"},
    )
    assert response.status_code == 422


# ── POST /api/v1/auth/refresh ─────────────────────────────────────────────────

async def test_refresh_returns_200(client: AsyncClient) -> None:
    reg = await client.post("/api/v1/auth/register", json=VALID_REGISTER)
    refresh_token = reg.json()["data"]["refresh_token"]

    response = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": refresh_token}
    )
    assert response.status_code == 200


async def test_refresh_response_has_new_tokens(client: AsyncClient) -> None:
    reg = await client.post("/api/v1/auth/register", json=VALID_REGISTER)
    original_refresh = reg.json()["data"]["refresh_token"]
    original_access = reg.json()["data"]["access_token"]

    response = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": original_refresh}
    )
    data = response.json()["data"]

    assert data["access_token"] != original_access
    assert data["refresh_token"] != original_refresh


async def test_refresh_old_token_rejected_after_rotation(client: AsyncClient) -> None:
    reg = await client.post("/api/v1/auth/register", json=VALID_REGISTER)
    original_refresh = reg.json()["data"]["refresh_token"]

    # Use it once
    await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": original_refresh}
    )

    # Second use must fail
    response = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": original_refresh}
    )
    assert response.status_code == 401


async def test_refresh_invalid_token_returns_401(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": "fakefakefake"}
    )
    assert response.status_code == 401
    assert response.json()["success"] is False


# ── POST /api/v1/auth/logout ──────────────────────────────────────────────────

async def test_logout_returns_200(client: AsyncClient) -> None:
    reg = await client.post("/api/v1/auth/register", json=VALID_REGISTER)
    refresh_token = reg.json()["data"]["refresh_token"]

    response = await client.post(
        "/api/v1/auth/logout", json={"refresh_token": refresh_token}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"] is None


async def test_logout_invalidates_refresh_token(client: AsyncClient) -> None:
    reg = await client.post("/api/v1/auth/register", json=VALID_REGISTER)
    refresh_token = reg.json()["data"]["refresh_token"]

    await client.post("/api/v1/auth/logout", json={"refresh_token": refresh_token})

    # Refresh after logout must fail
    response = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": refresh_token}
    )
    assert response.status_code == 401


async def test_logout_is_idempotent(client: AsyncClient) -> None:
    reg = await client.post("/api/v1/auth/register", json=VALID_REGISTER)
    refresh_token = reg.json()["data"]["refresh_token"]

    r1 = await client.post(
        "/api/v1/auth/logout", json={"refresh_token": refresh_token}
    )
    r2 = await client.post(
        "/api/v1/auth/logout", json={"refresh_token": refresh_token}
    )

    assert r1.status_code == 200
    assert r2.status_code == 200


async def test_logout_unknown_token_returns_200(client: AsyncClient) -> None:
    """Logout with a completely unknown token should still succeed (idempotent)."""
    response = await client.post(
        "/api/v1/auth/logout", json={"refresh_token": "unknowntoken"}
    )
    assert response.status_code == 200
    assert response.json()["success"] is True
