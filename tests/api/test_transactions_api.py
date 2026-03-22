"""
API-layer tests for the Transactions endpoints.

GET /api/v1/transactions          – list user's own transactions
GET /api/v1/transactions/{ref}    – single transaction by reference
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.transaction import Transaction, TransactionStatus, TransactionType
from app.models.user import User
from app.models.wallet import Wallet
from tests.conftest import make_auth_headers

pytestmark = pytest.mark.asyncio


# ── GET /api/v1/transactions ──────────────────────────────────────────────────

async def test_list_transactions_unauthenticated_returns_401(
    client: AsyncClient,
) -> None:
    response = await client.get("/api/v1/transactions")
    assert response.status_code == 401
    assert response.json()["success"] is False


async def test_list_transactions_authenticated_returns_200(
    client: AsyncClient, test_user: User
) -> None:
    response = await client.get(
        "/api/v1/transactions", headers=make_auth_headers(test_user)
    )
    assert response.status_code == 200


async def test_list_transactions_empty_for_new_user(
    client: AsyncClient, test_user: User
) -> None:
    response = await client.get(
        "/api/v1/transactions", headers=make_auth_headers(test_user)
    )
    body = response.json()
    assert body["success"] is True
    assert body["data"]["items"] == []
    assert body["data"]["total"] == 0


async def test_list_transactions_response_envelope(
    client: AsyncClient, test_user: User
) -> None:
    response = await client.get(
        "/api/v1/transactions", headers=make_auth_headers(test_user)
    )
    body = response.json()

    assert body["success"] is True
    assert "message" in body
    data = body["data"]
    assert "items" in data
    assert "total" in data
    assert "limit" in data
    assert "offset" in data


async def test_list_transactions_returns_existing_transaction(
    client: AsyncClient,
    test_user: User,
    test_transaction: Transaction,
) -> None:
    response = await client.get(
        "/api/v1/transactions", headers=make_auth_headers(test_user)
    )
    body = response.json()
    assert body["data"]["total"] == 1
    assert body["data"]["items"][0]["reference"] == test_transaction.reference


async def test_list_transactions_response_item_shape(
    client: AsyncClient,
    test_user: User,
    test_transaction: Transaction,
) -> None:
    response = await client.get(
        "/api/v1/transactions", headers=make_auth_headers(test_user)
    )
    item = response.json()["data"]["items"][0]

    assert "id" in item
    assert "reference" in item
    assert "type" in item
    assert "status" in item
    assert "amount" in item
    assert "currency" in item
    assert "created_at" in item
    assert "updated_at" in item
    # provider_reference must never appear in the response
    assert "provider_reference" not in item


async def test_list_transactions_does_not_show_other_users(
    client: AsyncClient,
    test_user: User,
    test_admin: User,
    test_transaction: Transaction,
) -> None:
    """Admin should see zero transactions (test_transaction belongs to test_user)."""
    response = await client.get(
        "/api/v1/transactions", headers=make_auth_headers(test_admin)
    )
    assert response.json()["data"]["total"] == 0


async def test_list_transactions_type_filter(
    client: AsyncClient,
    db_session: AsyncSession,
    test_user: User,
    test_wallet: Wallet,
    test_transaction: Transaction,
) -> None:
    # test_transaction is FUNDING; add a TRANSFER
    from decimal import Decimal
    from app.models.transaction import Transaction as TxnModel

    transfer = TxnModel(
        reference=f"txn_transfer_{test_user.id.hex[:8]}",
        type=TransactionType.TRANSFER,
        status=TransactionStatus.COMPLETED,
        amount=Decimal("200.00"),
        currency="NGN",
        source_wallet_id=test_wallet.id,
        initiated_by_user_id=test_user.id,
    )
    db_session.add(transfer)
    await db_session.flush()

    response = await client.get(
        "/api/v1/transactions?type=funding",
        headers=make_auth_headers(test_user),
    )
    body = response.json()
    assert body["data"]["total"] == 1
    assert body["data"]["items"][0]["type"] == "funding"


async def test_list_transactions_status_filter(
    client: AsyncClient,
    test_user: User,
    test_transaction: Transaction,
) -> None:
    # test_transaction is COMPLETED
    response = await client.get(
        "/api/v1/transactions?status=completed",
        headers=make_auth_headers(test_user),
    )
    assert response.json()["data"]["total"] == 1

    response = await client.get(
        "/api/v1/transactions?status=pending",
        headers=make_auth_headers(test_user),
    )
    assert response.json()["data"]["total"] == 0


async def test_list_transactions_default_pagination(
    client: AsyncClient, test_user: User
) -> None:
    response = await client.get(
        "/api/v1/transactions", headers=make_auth_headers(test_user)
    )
    data = response.json()["data"]
    assert data["limit"] == 20
    assert data["offset"] == 0


async def test_list_transactions_custom_pagination(
    client: AsyncClient, test_user: User
) -> None:
    response = await client.get(
        "/api/v1/transactions?limit=5&offset=10",
        headers=make_auth_headers(test_user),
    )
    data = response.json()["data"]
    assert data["limit"] == 5
    assert data["offset"] == 10


async def test_list_transactions_limit_exceeds_max_returns_422(
    client: AsyncClient, test_user: User
) -> None:
    response = await client.get(
        "/api/v1/transactions?limit=999",
        headers=make_auth_headers(test_user),
    )
    assert response.status_code == 422


async def test_list_transactions_invalid_type_returns_422(
    client: AsyncClient, test_user: User
) -> None:
    response = await client.get(
        "/api/v1/transactions?type=invalid_type",
        headers=make_auth_headers(test_user),
    )
    assert response.status_code == 422


# ── GET /api/v1/transactions/{reference} ─────────────────────────────────────

async def test_get_transaction_unauthenticated_returns_401(
    client: AsyncClient, test_transaction: Transaction
) -> None:
    response = await client.get(
        f"/api/v1/transactions/{test_transaction.reference}"
    )
    assert response.status_code == 401


async def test_get_transaction_returns_200(
    client: AsyncClient, test_user: User, test_transaction: Transaction
) -> None:
    response = await client.get(
        f"/api/v1/transactions/{test_transaction.reference}",
        headers=make_auth_headers(test_user),
    )
    assert response.status_code == 200


async def test_get_transaction_response_envelope(
    client: AsyncClient, test_user: User, test_transaction: Transaction
) -> None:
    response = await client.get(
        f"/api/v1/transactions/{test_transaction.reference}",
        headers=make_auth_headers(test_user),
    )
    body = response.json()
    assert body["success"] is True
    assert "data" in body
    assert body["data"]["reference"] == test_transaction.reference


async def test_get_transaction_does_not_expose_provider_reference(
    client: AsyncClient, test_user: User, test_transaction: Transaction
) -> None:
    response = await client.get(
        f"/api/v1/transactions/{test_transaction.reference}",
        headers=make_auth_headers(test_user),
    )
    assert "provider_reference" not in response.json()["data"]


async def test_get_transaction_not_found_returns_404(
    client: AsyncClient, test_user: User
) -> None:
    response = await client.get(
        "/api/v1/transactions/txn_does_not_exist",
        headers=make_auth_headers(test_user),
    )
    assert response.status_code == 404
    body = response.json()
    assert body["success"] is False
    assert body["error"] == "TRANSACTION_NOT_FOUND"


async def test_get_transaction_other_users_transaction_returns_404(
    client: AsyncClient,
    test_admin: User,
    test_transaction: Transaction,
) -> None:
    """Admin querying test_user's transaction must get 404, not 403."""
    response = await client.get(
        f"/api/v1/transactions/{test_transaction.reference}",
        headers=make_auth_headers(test_admin),
    )
    assert response.status_code == 404
