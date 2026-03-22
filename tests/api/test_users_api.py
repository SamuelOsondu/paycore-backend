import pytest

from tests.conftest import make_auth_headers


@pytest.mark.asyncio
async def test_get_profile_unauthenticated(client):
    response = await client.get("/api/v1/users/me")
    assert response.status_code == 401
    body = response.json()
    assert body["success"] is False
    assert body["error"] == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_get_profile_authenticated(client, test_user):
    headers = make_auth_headers(test_user)
    response = await client.get("/api/v1/users/me", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["email"] == test_user.email
    assert body["data"]["id"] == str(test_user.id)
    assert "hashed_password" not in body["data"]
    assert "deleted_at" not in body["data"]


@pytest.mark.asyncio
async def test_get_profile_response_shape(client, test_user):
    """All responses carry success, message, data keys."""
    headers = make_auth_headers(test_user)
    response = await client.get("/api/v1/users/me", headers=headers)
    body = response.json()
    assert "success" in body
    assert "message" in body
    assert "data" in body


@pytest.mark.asyncio
async def test_update_profile_full_name(client, test_user):
    headers = make_auth_headers(test_user)
    response = await client.patch(
        "/api/v1/users/me",
        json={"full_name": "Updated Name"},
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["full_name"] == "Updated Name"


@pytest.mark.asyncio
async def test_update_profile_phone(client, test_user):
    headers = make_auth_headers(test_user)
    response = await client.patch(
        "/api/v1/users/me",
        json={"phone": "+2348055555555"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["data"]["phone"] == "+2348055555555"


@pytest.mark.asyncio
async def test_update_profile_rejects_unknown_fields(client, test_user):
    """extra='forbid' must reject any field not in UserUpdateRequest."""
    headers = make_auth_headers(test_user)
    response = await client.patch(
        "/api/v1/users/me",
        json={"role": "admin"},
        headers=headers,
    )
    assert response.status_code == 422
    body = response.json()
    assert body["success"] is False
    assert body["error"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_update_profile_blank_full_name_rejected(client, test_user):
    headers = make_auth_headers(test_user)
    response = await client.patch(
        "/api/v1/users/me",
        json={"full_name": "   "},
        headers=headers,
    )
    assert response.status_code == 422
    assert response.json()["success"] is False


@pytest.mark.asyncio
async def test_update_profile_unauthenticated(client):
    response = await client.patch("/api/v1/users/me", json={"full_name": "X"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_health_endpoint(client):
    response = await client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["status"] == "ok"
