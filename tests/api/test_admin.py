"""
API integration tests for the Admin component.

Coverage:
- Auth guards: every new admin endpoint returns 403 for non-admins, 401 for guests
- GET /api/v1/admin/transactions — list, filter by status/type/risk_flagged/date, pagination
- GET /api/v1/admin/transactions/{reference} — detail with ledger entries, 404
- POST /api/v1/admin/reconciliation/run — enqueues Celery task, writes audit log
- GET /api/v1/admin/users — list, filter by role/kyc_tier, pagination
- GET /api/v1/admin/users/{id} — user detail, 404 for missing user
"""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models.audit_log import ActorType, AuditLog
from app.models.ledger_entry import EntryType, LedgerEntry
from app.models.transaction import Transaction, TransactionStatus, TransactionType
from app.models.user import User, UserRole
from app.models.wallet import Wallet
from tests.conftest import make_auth_headers

pytestmark = pytest.mark.asyncio


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def funded_wallet(db_session: AsyncSession, test_user: User) -> Wallet:
    wallet = Wallet(
        id=uuid.uuid4(),
        user_id=test_user.id,
        currency="NGN",
        balance=Decimal("5000.00"),
        is_active=True,
    )
    db_session.add(wallet)
    await db_session.flush()
    await db_session.refresh(wallet)
    return wallet


@pytest_asyncio.fixture
async def completed_txn(
    db_session: AsyncSession, test_user: User, funded_wallet: Wallet
) -> Transaction:
    txn = Transaction(
        id=uuid.uuid4(),
        reference=f"txn_{uuid.uuid4().hex[:12]}",
        type=TransactionType.FUNDING,
        status=TransactionStatus.COMPLETED,
        amount=Decimal("1000.00"),
        currency="NGN",
        destination_wallet_id=funded_wallet.id,
        initiated_by_user_id=test_user.id,
        provider_reference="paystack_ref_abc",
    )
    db_session.add(txn)
    await db_session.flush()
    await db_session.refresh(txn)
    return txn


@pytest_asyncio.fixture
async def flagged_txn(
    db_session: AsyncSession, test_user: User, funded_wallet: Wallet
) -> Transaction:
    txn = Transaction(
        id=uuid.uuid4(),
        reference=f"txn_{uuid.uuid4().hex[:12]}",
        type=TransactionType.TRANSFER,
        status=TransactionStatus.COMPLETED,
        amount=Decimal("2000.00"),
        currency="NGN",
        source_wallet_id=funded_wallet.id,
        initiated_by_user_id=test_user.id,
        risk_flagged=True,
        risk_flag_reason="velocity check exceeded",
    )
    db_session.add(txn)
    await db_session.flush()
    await db_session.refresh(txn)
    return txn


@pytest_asyncio.fixture
async def ledger_entry(
    db_session: AsyncSession, completed_txn: Transaction, funded_wallet: Wallet
) -> LedgerEntry:
    entry = LedgerEntry(
        id=uuid.uuid4(),
        transaction_id=completed_txn.id,
        wallet_id=funded_wallet.id,
        entry_type=EntryType.CREDIT,
        amount=Decimal("1000.00"),
        currency="NGN",
        balance_after=Decimal("6000.00"),
    )
    db_session.add(entry)
    await db_session.flush()
    await db_session.refresh(entry)
    return entry


@pytest_asyncio.fixture
async def extra_user(db_session: AsyncSession) -> User:
    """A second regular user for list/filter tests."""
    user = User(
        id=uuid.uuid4(),
        email="extra_admin_test@example.com",
        hashed_password=hash_password("Pass1234!"),
        full_name="Extra User",
        role=UserRole.USER,
        kyc_tier=1,
        is_active=True,
        is_email_verified=True,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    return user


# ── Helper ────────────────────────────────────────────────────────────────────


async def _count_audit_entries(db_session: AsyncSession, action: str) -> int:
    from sqlalchemy import select

    result = await db_session.execute(
        select(AuditLog).where(AuditLog.action == action)
    )
    return len(result.scalars().all())


# ── Auth guards: GET /api/v1/admin/transactions ───────────────────────────────


async def test_list_transactions_unauthenticated(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/admin/transactions")
    assert resp.status_code == 401


async def test_list_transactions_non_admin_forbidden(
    client: AsyncClient, test_user: User
) -> None:
    resp = await client.get(
        "/api/v1/admin/transactions", headers=make_auth_headers(test_user)
    )
    assert resp.status_code == 403


# ── GET /api/v1/admin/transactions — list & filters ──────────────────────────


async def test_list_transactions_empty(
    client: AsyncClient, test_admin: User
) -> None:
    resp = await client.get(
        "/api/v1/admin/transactions", headers=make_auth_headers(test_admin)
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    data = body["data"]
    assert data["total"] == 0
    assert data["items"] == []


async def test_list_transactions_returns_all(
    client: AsyncClient,
    test_admin: User,
    completed_txn: Transaction,
    flagged_txn: Transaction,
) -> None:
    resp = await client.get(
        "/api/v1/admin/transactions", headers=make_auth_headers(test_admin)
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["total"] == 2
    assert len(data["items"]) == 2


async def test_list_transactions_response_shape(
    client: AsyncClient,
    test_admin: User,
    completed_txn: Transaction,
) -> None:
    """Response includes admin-only fields: provider_reference, risk_flagged."""
    resp = await client.get(
        "/api/v1/admin/transactions", headers=make_auth_headers(test_admin)
    )
    assert resp.status_code == 200
    item = resp.json()["data"]["items"][0]
    # Admin fields present
    assert "provider_reference" in item
    assert "risk_flagged" in item
    assert "risk_flag_reason" in item
    # Core fields present
    assert "id" in item
    assert "reference" in item
    assert "type" in item
    assert "status" in item
    assert "amount" in item


async def test_list_transactions_filter_by_status(
    client: AsyncClient,
    test_admin: User,
    completed_txn: Transaction,
    flagged_txn: Transaction,
) -> None:
    resp = await client.get(
        "/api/v1/admin/transactions?status=completed",
        headers=make_auth_headers(test_admin),
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["total"] == 2  # both are COMPLETED
    assert all(i["status"] == "completed" for i in data["items"])


async def test_list_transactions_filter_by_type(
    client: AsyncClient,
    test_admin: User,
    completed_txn: Transaction,
    flagged_txn: Transaction,
) -> None:
    resp = await client.get(
        "/api/v1/admin/transactions?type=funding",
        headers=make_auth_headers(test_admin),
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["total"] == 1
    assert data["items"][0]["type"] == "funding"


async def test_list_transactions_filter_risk_flagged_true(
    client: AsyncClient,
    test_admin: User,
    completed_txn: Transaction,
    flagged_txn: Transaction,
) -> None:
    resp = await client.get(
        "/api/v1/admin/transactions?risk_flagged=true",
        headers=make_auth_headers(test_admin),
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["total"] == 1
    assert data["items"][0]["risk_flagged"] is True
    assert data["items"][0]["risk_flag_reason"] == "velocity check exceeded"


async def test_list_transactions_filter_risk_flagged_false(
    client: AsyncClient,
    test_admin: User,
    completed_txn: Transaction,
    flagged_txn: Transaction,
) -> None:
    resp = await client.get(
        "/api/v1/admin/transactions?risk_flagged=false",
        headers=make_auth_headers(test_admin),
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["total"] == 1
    assert data["items"][0]["risk_flagged"] is False


async def test_list_transactions_filter_by_date_range(
    client: AsyncClient,
    test_admin: User,
    completed_txn: Transaction,
) -> None:
    now = datetime.now(timezone.utc)
    from_iso = (now - timedelta(minutes=5)).isoformat()
    to_iso = (now + timedelta(minutes=5)).isoformat()

    resp = await client.get(
        f"/api/v1/admin/transactions?from_date={from_iso}&to_date={to_iso}",
        headers=make_auth_headers(test_admin),
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["total"] >= 1

    # Future range excludes all
    future_from = (now + timedelta(hours=1)).isoformat()
    future_to = (now + timedelta(hours=2)).isoformat()
    resp2 = await client.get(
        f"/api/v1/admin/transactions?from_date={future_from}&to_date={future_to}",
        headers=make_auth_headers(test_admin),
    )
    assert resp2.json()["data"]["total"] == 0


async def test_list_transactions_pagination(
    client: AsyncClient,
    test_admin: User,
    completed_txn: Transaction,
    flagged_txn: Transaction,
) -> None:
    resp = await client.get(
        "/api/v1/admin/transactions?limit=1&offset=0",
        headers=make_auth_headers(test_admin),
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["total"] == 2
    assert len(data["items"]) == 1

    resp2 = await client.get(
        "/api/v1/admin/transactions?limit=1&offset=1",
        headers=make_auth_headers(test_admin),
    )
    data2 = resp2.json()["data"]
    assert len(data2["items"]) == 1
    assert data["items"][0]["id"] != data2["items"][0]["id"]


# ── Auth guards: GET /api/v1/admin/transactions/{reference} ──────────────────


async def test_get_transaction_unauthenticated(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/admin/transactions/txn_abc123")
    assert resp.status_code == 401


async def test_get_transaction_non_admin_forbidden(
    client: AsyncClient, test_user: User
) -> None:
    resp = await client.get(
        "/api/v1/admin/transactions/txn_abc123",
        headers=make_auth_headers(test_user),
    )
    assert resp.status_code == 403


# ── GET /api/v1/admin/transactions/{reference} ────────────────────────────────


async def test_get_transaction_not_found(
    client: AsyncClient, test_admin: User
) -> None:
    resp = await client.get(
        "/api/v1/admin/transactions/txn_nonexistent",
        headers=make_auth_headers(test_admin),
    )
    assert resp.status_code == 404


async def test_get_transaction_detail_with_ledger_entries(
    client: AsyncClient,
    test_admin: User,
    completed_txn: Transaction,
    ledger_entry: LedgerEntry,
) -> None:
    resp = await client.get(
        f"/api/v1/admin/transactions/{completed_txn.reference}",
        headers=make_auth_headers(test_admin),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    data = body["data"]

    # Transaction fields
    assert data["id"] == str(completed_txn.id)
    assert data["reference"] == completed_txn.reference
    assert data["provider_reference"] == "paystack_ref_abc"
    assert data["risk_flagged"] is False

    # Ledger entries embedded
    assert "ledger_entries" in data
    assert len(data["ledger_entries"]) == 1
    entry = data["ledger_entries"][0]
    assert entry["entry_type"] == "credit"
    assert entry["wallet_id"] == str(ledger_entry.wallet_id)
    assert entry["transaction_id"] == str(completed_txn.id)


async def test_get_transaction_detail_no_ledger_entries(
    client: AsyncClient,
    test_admin: User,
    completed_txn: Transaction,
) -> None:
    """Transaction with no ledger entries returns empty list (not an error)."""
    resp = await client.get(
        f"/api/v1/admin/transactions/{completed_txn.reference}",
        headers=make_auth_headers(test_admin),
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["ledger_entries"] == []


# ── Auth guards: POST /api/v1/admin/reconciliation/run ───────────────────────


async def test_run_reconciliation_unauthenticated(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/admin/reconciliation/run")
    assert resp.status_code == 401


async def test_run_reconciliation_non_admin_forbidden(
    client: AsyncClient, test_user: User
) -> None:
    resp = await client.post(
        "/api/v1/admin/reconciliation/run",
        headers=make_auth_headers(test_user),
    )
    assert resp.status_code == 403


# ── POST /api/v1/admin/reconciliation/run ────────────────────────────────────


async def test_run_reconciliation_enqueues_celery_task(
    client: AsyncClient,
    test_admin: User,
) -> None:
    """Reconciliation trigger enqueues the Celery task and returns 200."""
    with patch(
        "app.workers.reconciliation_tasks.check_stale_transactions"
    ) as mock_task:
        resp = await client.post(
            "/api/v1/admin/reconciliation/run",
            headers=make_auth_headers(test_admin),
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert "enqueued" in body["message"].lower()
    mock_task.delay.assert_called_once()


async def test_run_reconciliation_writes_audit_log(
    client: AsyncClient,
    db_session: AsyncSession,
    test_admin: User,
) -> None:
    """Reconciliation trigger writes an audit log entry with actor_type=admin."""
    count_before = await _count_audit_entries(
        db_session, "admin.reconciliation_triggered"
    )

    with patch("app.workers.reconciliation_tasks.check_stale_transactions"):
        await client.post(
            "/api/v1/admin/reconciliation/run",
            headers=make_auth_headers(test_admin),
        )

    count_after = await _count_audit_entries(
        db_session, "admin.reconciliation_triggered"
    )
    assert count_after == count_before + 1

    from sqlalchemy import select

    result = await db_session.execute(
        select(AuditLog).where(
            AuditLog.action == "admin.reconciliation_triggered"
        )
    )
    entry = result.scalars().first()
    assert entry is not None
    assert entry.actor_id == test_admin.id
    assert entry.actor_type == ActorType.ADMIN


# ── Auth guards: GET /api/v1/admin/users ─────────────────────────────────────


async def test_list_users_unauthenticated(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/admin/users")
    assert resp.status_code == 401


async def test_list_users_non_admin_forbidden(
    client: AsyncClient, test_user: User
) -> None:
    resp = await client.get(
        "/api/v1/admin/users", headers=make_auth_headers(test_user)
    )
    assert resp.status_code == 403


# ── GET /api/v1/admin/users ───────────────────────────────────────────────────


async def test_list_users_returns_all(
    client: AsyncClient,
    test_admin: User,
    test_user: User,
    extra_user: User,
) -> None:
    """Admin sees all non-deleted users (admin + regular + extra in this test)."""
    resp = await client.get(
        "/api/v1/admin/users", headers=make_auth_headers(test_admin)
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    # At minimum test_admin, test_user, and extra_user exist
    assert data["total"] >= 3


async def test_list_users_response_shape(
    client: AsyncClient, test_admin: User, test_user: User
) -> None:
    resp = await client.get(
        "/api/v1/admin/users", headers=make_auth_headers(test_admin)
    )
    assert resp.status_code == 200
    item = resp.json()["data"]["items"][0]
    assert "id" in item
    assert "email" in item
    assert "full_name" in item
    assert "role" in item
    assert "kyc_tier" in item
    assert "is_active" in item
    assert "created_at" in item
    # Sensitive fields must be absent
    assert "hashed_password" not in item
    assert "deleted_at" not in item


async def test_list_users_filter_by_role(
    client: AsyncClient,
    test_admin: User,
    test_user: User,
    extra_user: User,
) -> None:
    """role= filter returns only users with the specified role."""
    resp = await client.get(
        "/api/v1/admin/users?role=user",
        headers=make_auth_headers(test_admin),
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["total"] >= 1
    assert all(item["role"] == "user" for item in data["items"])


async def test_list_users_filter_by_kyc_tier(
    client: AsyncClient,
    test_admin: User,
    test_user: User,
    extra_user: User,
) -> None:
    """kyc_tier= filter returns only users with the specified tier."""
    # extra_user has kyc_tier=1, test_user has kyc_tier=0
    resp = await client.get(
        "/api/v1/admin/users?kyc_tier=1",
        headers=make_auth_headers(test_admin),
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["total"] >= 1
    assert all(item["kyc_tier"] == 1 for item in data["items"])


async def test_list_users_pagination(
    client: AsyncClient,
    test_admin: User,
    test_user: User,
    extra_user: User,
) -> None:
    resp = await client.get(
        "/api/v1/admin/users?limit=1&offset=0",
        headers=make_auth_headers(test_admin),
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data["items"]) == 1
    assert data["total"] >= 3

    resp2 = await client.get(
        "/api/v1/admin/users?limit=1&offset=1",
        headers=make_auth_headers(test_admin),
    )
    data2 = resp2.json()["data"]
    assert len(data2["items"]) == 1
    assert data["items"][0]["id"] != data2["items"][0]["id"]


async def test_list_users_invalid_pagination(
    client: AsyncClient, test_admin: User
) -> None:
    resp = await client.get(
        "/api/v1/admin/users?limit=0", headers=make_auth_headers(test_admin)
    )
    assert resp.status_code == 422

    resp2 = await client.get(
        "/api/v1/admin/users?offset=-1", headers=make_auth_headers(test_admin)
    )
    assert resp2.status_code == 422


# ── Auth guards: GET /api/v1/admin/users/{id} ────────────────────────────────


async def test_get_user_unauthenticated(client: AsyncClient) -> None:
    resp = await client.get(f"/api/v1/admin/users/{uuid.uuid4()}")
    assert resp.status_code == 401


async def test_get_user_non_admin_forbidden(
    client: AsyncClient, test_user: User
) -> None:
    resp = await client.get(
        f"/api/v1/admin/users/{uuid.uuid4()}",
        headers=make_auth_headers(test_user),
    )
    assert resp.status_code == 403


# ── GET /api/v1/admin/users/{id} ─────────────────────────────────────────────


async def test_get_user_not_found(client: AsyncClient, test_admin: User) -> None:
    resp = await client.get(
        f"/api/v1/admin/users/{uuid.uuid4()}",
        headers=make_auth_headers(test_admin),
    )
    assert resp.status_code == 404


async def test_get_user_returns_correct_data(
    client: AsyncClient, test_admin: User, test_user: User
) -> None:
    resp = await client.get(
        f"/api/v1/admin/users/{test_user.id}",
        headers=make_auth_headers(test_admin),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    data = body["data"]
    assert data["id"] == str(test_user.id)
    assert data["email"] == test_user.email
    assert data["full_name"] == test_user.full_name
    assert data["role"] == test_user.role.value
    assert data["kyc_tier"] == test_user.kyc_tier
    assert "hashed_password" not in data
