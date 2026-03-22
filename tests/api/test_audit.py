"""
Tests for the Audit component.

Coverage:
- Unit: AuditService.log() writes a correct AuditLog record
- Unit: AuditService.log() never raises on write failure (fire-and-forget)
- Unit: log_sync() never raises on write failure (sync Celery tasks)
- Unit: log_sync() passes the correct entity to the session
- Integration: 'user.registered' audit entry on AuthService.register()
- Integration: 'user.login' audit entry on AuthService.login()
- Integration: 'transfer.completed' audit entry on TransferService.initiate_transfer()
- Admin endpoint: GET /api/v1/admin/audit-logs — auth, list, filters, pagination
- Edge: audit write failure does NOT propagate to the parent operation
"""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import ActorType, AuditLog
from app.models.user import User, UserRole
from app.models.wallet import Wallet
from app.repositories.audit_log import AuditLogRepository
from app.services.audit import AuditService, log_sync
from tests.conftest import make_auth_headers


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def audit_sender(db_session: AsyncSession) -> User:
    """Tier 1 user who can initiate transfers."""
    from app.core.security import hash_password

    user = User(
        id=uuid.uuid4(),
        email="audit_sender@example.com",
        hashed_password=hash_password("Pass1234!"),
        full_name="Audit Sender",
        role=UserRole.USER,
        kyc_tier=1,
        is_active=True,
        is_email_verified=True,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def audit_sender_wallet(db_session: AsyncSession, audit_sender: User) -> Wallet:
    wallet = Wallet(
        id=uuid.uuid4(),
        user_id=audit_sender.id,
        currency="NGN",
        balance=Decimal("10000.00"),
        is_active=True,
    )
    db_session.add(wallet)
    await db_session.flush()
    await db_session.refresh(wallet)
    return wallet


@pytest_asyncio.fixture
async def audit_recipient(db_session: AsyncSession) -> User:
    from app.core.security import hash_password

    user = User(
        id=uuid.uuid4(),
        email="audit_recipient@example.com",
        hashed_password=hash_password("Pass1234!"),
        full_name="Audit Recipient",
        role=UserRole.USER,
        kyc_tier=1,
        is_active=True,
        is_email_verified=True,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def audit_recipient_wallet(
    db_session: AsyncSession, audit_recipient: User
) -> Wallet:
    wallet = Wallet(
        id=uuid.uuid4(),
        user_id=audit_recipient.id,
        currency="NGN",
        balance=Decimal("0.00"),
        is_active=True,
    )
    db_session.add(wallet)
    await db_session.flush()
    await db_session.refresh(wallet)
    return wallet


# ── Helper: query audit entries from DB ──────────────────────────────────────


async def _get_entries(
    db_session: AsyncSession,
    *,
    action: str | None = None,
    actor_id: uuid.UUID | None = None,
) -> list[AuditLog]:
    stmt = select(AuditLog)
    if action is not None:
        stmt = stmt.where(AuditLog.action == action)
    if actor_id is not None:
        stmt = stmt.where(AuditLog.actor_id == actor_id)
    result = await db_session.execute(stmt)
    return list(result.scalars().all())


# ── Unit: AuditService.log() ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_audit_service_log_writes_correct_record(
    db_session: AsyncSession,
    test_user: User,
) -> None:
    """AuditService.log() persists an AuditLog row with every provided field."""
    target_id = uuid.uuid4()
    service = AuditService(db_session)

    await service.log(
        actor_id=test_user.id,
        actor_type=ActorType.USER,
        action="transfer.completed",
        target_type="transaction",
        target_id=target_id,
        metadata={"amount": "500.00", "currency": "NGN"},
        ip_address="192.168.1.1",
    )

    entries = await _get_entries(db_session, action="transfer.completed")
    assert len(entries) == 1
    entry = entries[0]
    assert entry.actor_id == test_user.id
    assert entry.actor_type == ActorType.USER
    assert entry.action == "transfer.completed"
    assert entry.target_type == "transaction"
    assert entry.target_id == target_id
    assert entry.metadata_["amount"] == "500.00"
    assert entry.metadata_["currency"] == "NGN"
    assert entry.ip_address == "192.168.1.1"
    assert entry.created_at is not None


@pytest.mark.asyncio
async def test_audit_service_log_null_actor_id_allowed(
    db_session: AsyncSession,
) -> None:
    """System events with no human actor use actor_id=None."""
    service = AuditService(db_session)

    await service.log(
        actor_id=None,
        actor_type=ActorType.SYSTEM,
        action="webhook_delivery.failed",
        target_type="webhook_delivery",
        target_id=uuid.uuid4(),
        metadata={"attempts": 6, "last_error": "Connection refused"},
    )

    entries = await _get_entries(db_session, action="webhook_delivery.failed")
    assert len(entries) == 1
    assert entries[0].actor_id is None
    assert entries[0].actor_type == ActorType.SYSTEM
    assert entries[0].metadata_["attempts"] == 6


@pytest.mark.asyncio
async def test_audit_service_log_never_raises_on_write_failure(
    db_session: AsyncSession,
    test_user: User,
) -> None:
    """
    AuditService.log() swallows all exceptions — the caller must never be
    disrupted by an audit write failure.
    """
    service = AuditService(db_session)

    with patch.object(
        AuditLogRepository,
        "create",
        new_callable=AsyncMock,
        side_effect=RuntimeError("Simulated DB write failure"),
    ):
        # Must not raise
        await service.log(
            actor_id=test_user.id,
            actor_type=ActorType.USER,
            action="transfer.completed",
        )


# ── Unit: log_sync() ──────────────────────────────────────────────────────────


def test_log_sync_writes_entry_to_session() -> None:
    """log_sync() constructs the AuditLog entity and flushes+commits the session."""
    mock_session = MagicMock()
    actor_id = uuid.uuid4()
    target_id = uuid.uuid4()

    log_sync(
        mock_session,
        actor_id=actor_id,
        actor_type=ActorType.SYSTEM,
        action="admin.transaction_flagged",
        target_type="transaction",
        target_id=target_id,
        metadata={"reason": "velocity"},
    )

    mock_session.add.assert_called_once()
    added = mock_session.add.call_args[0][0]
    assert isinstance(added, AuditLog)
    assert added.actor_id == actor_id
    assert added.actor_type == ActorType.SYSTEM
    assert added.action == "admin.transaction_flagged"
    assert added.target_type == "transaction"
    assert added.target_id == target_id
    assert added.metadata_["reason"] == "velocity"
    mock_session.flush.assert_called_once()
    mock_session.commit.assert_called_once()


def test_log_sync_never_raises_on_write_failure() -> None:
    """
    log_sync() swallows all exceptions — Celery task callers must not fail
    due to an audit write error.
    """
    mock_session = MagicMock()
    mock_session.add.side_effect = RuntimeError("Simulated DB error")

    # Must not raise
    log_sync(
        mock_session,
        actor_id=None,
        actor_type=ActorType.SYSTEM,
        action="webhook_delivery.failed",
        target_type="webhook_delivery",
        target_id=uuid.uuid4(),
        metadata={"attempts": 6},
    )

    # Session must have been rolled back to leave it in a clean state
    mock_session.rollback.assert_called_once()


def test_log_sync_rollback_failure_is_also_swallowed() -> None:
    """
    Even if both the write AND the rollback fail, log_sync must not raise.
    """
    mock_session = MagicMock()
    mock_session.add.side_effect = RuntimeError("Write failure")
    mock_session.rollback.side_effect = RuntimeError("Rollback failure")

    # Must not raise
    log_sync(
        mock_session,
        actor_id=None,
        actor_type=ActorType.SYSTEM,
        action="webhook_delivery.failed",
    )


# ── Integration: user.registered ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_register_produces_user_registered_audit_entry(
    db_session: AsyncSession,
) -> None:
    """AuthService.register() writes a 'user.registered' audit log entry."""
    from app.services.auth import AuthService

    service = AuthService(db_session)
    await service.register(
        email="auditregister@example.com",
        password="Password1!",
        full_name="Audit Register Test",
    )

    entries = await _get_entries(db_session, action="user.registered")
    assert len(entries) == 1
    entry = entries[0]
    assert entry.actor_type == ActorType.USER
    assert entry.target_type == "user"
    # actor_id and target_id both point to the newly created user
    assert entry.actor_id is not None
    assert entry.actor_id == entry.target_id


# ── Integration: user.login ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_login_produces_user_login_audit_entry(
    db_session: AsyncSession,
) -> None:
    """AuthService.login() writes a 'user.login' audit log entry."""
    from app.services.auth import AuthService

    service = AuthService(db_session)
    await service.register(
        email="auditlogin@example.com",
        password="Password1!",
        full_name="Audit Login Test",
    )

    # Count registration entries so we can isolate the login entry
    entries_before = await _get_entries(db_session, action="user.login")
    count_before = len(entries_before)

    await service.login(email="auditlogin@example.com", password="Password1!")

    entries_after = await _get_entries(db_session, action="user.login")
    assert len(entries_after) == count_before + 1
    login_entry = entries_after[-1]
    assert login_entry.actor_type == ActorType.USER
    assert login_entry.action == "user.login"
    assert login_entry.target_type == "user"
    assert login_entry.actor_id is not None


# ── Integration: transfer.completed ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_transfer_produces_transfer_completed_audit_entry(
    db_session: AsyncSession,
    audit_sender: User,
    audit_sender_wallet: Wallet,
    audit_recipient: User,
    audit_recipient_wallet: Wallet,
) -> None:
    """
    TransferService.initiate_transfer() writes a 'transfer.completed' audit
    log entry referencing the created transaction.
    """
    from app.services.transfer import TransferService

    service = TransferService(db_session)
    txn = await service.initiate_transfer(
        audit_sender,
        recipient_user_id=audit_recipient.id,
        amount=Decimal("500.00"),
    )

    entries = await _get_entries(
        db_session, action="transfer.completed", actor_id=audit_sender.id
    )
    assert len(entries) == 1
    entry = entries[0]
    assert entry.actor_type == ActorType.USER
    assert entry.target_type == "transaction"
    assert entry.target_id == txn.id
    assert entry.metadata_ is not None
    assert entry.metadata_["amount"] == "500.00"


# ── Edge: audit failure does not break parent operation ───────────────────────


@pytest.mark.asyncio
async def test_audit_failure_does_not_fail_registration(
    db_session: AsyncSession,
) -> None:
    """
    When the audit log write raises after AuthService.register() commits,
    the registration itself must still succeed — the user must exist in the DB.
    """
    from app.models.user import User as UserModel
    from app.services.auth import AuthService

    with patch.object(
        AuditLogRepository,
        "create",
        new_callable=AsyncMock,
        side_effect=RuntimeError("Audit DB temporarily unavailable"),
    ):
        service = AuthService(db_session)
        resp = await service.register(
            email="audit_fail_test@example.com",
            password="Password1!",
            full_name="Audit Fail Test",
        )
        # Registration response is fully formed despite audit failure
        assert resp.access_token is not None
        assert resp.user.email == "audit_fail_test@example.com"

    # The user must exist in the DB — parent commit was unaffected
    result = await db_session.execute(
        select(UserModel).where(UserModel.email == "audit_fail_test@example.com")
    )
    user = result.scalars().first()
    assert user is not None
    assert user.full_name == "Audit Fail Test"


# ── Admin endpoint: GET /api/v1/admin/audit-logs ──────────────────────────────


@pytest.mark.asyncio
async def test_audit_logs_unauthenticated(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/admin/audit-logs")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_audit_logs_non_admin_forbidden(
    client: AsyncClient,
    test_user: User,
) -> None:
    resp = await client.get(
        "/api/v1/admin/audit-logs",
        headers=make_auth_headers(test_user),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_audit_logs_admin_empty_list(
    client: AsyncClient,
    test_admin: User,
) -> None:
    """Admin receives a well-formed paginated response even with no entries."""
    resp = await client.get(
        "/api/v1/admin/audit-logs",
        headers=make_auth_headers(test_admin),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    data = body["data"]
    assert data["items"] == []
    assert data["total"] == 0
    assert data["limit"] == 20
    assert data["offset"] == 0


@pytest.mark.asyncio
async def test_audit_logs_returns_all_entries(
    client: AsyncClient,
    db_session: AsyncSession,
    test_admin: User,
    test_user: User,
) -> None:
    """Admin endpoint returns all audit log entries in the database."""
    repo = AuditLogRepository(db_session)
    target_id = uuid.uuid4()
    await repo.create(
        actor_id=test_user.id,
        actor_type=ActorType.USER,
        action="transfer.completed",
        target_type="transaction",
        target_id=target_id,
        metadata={"amount": "100.00"},
    )
    await repo.create(
        actor_id=test_user.id,
        actor_type=ActorType.USER,
        action="user.login",
        target_type="user",
        target_id=test_user.id,
    )
    await db_session.flush()

    resp = await client.get(
        "/api/v1/admin/audit-logs",
        headers=make_auth_headers(test_admin),
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["total"] == 2
    assert len(data["items"]) == 2


@pytest.mark.asyncio
async def test_audit_logs_response_shape(
    client: AsyncClient,
    db_session: AsyncSession,
    test_admin: User,
    test_user: User,
) -> None:
    """Response items contain all expected AuditLogOut fields with correct values."""
    repo = AuditLogRepository(db_session)
    target_id = uuid.uuid4()
    await repo.create(
        actor_id=test_user.id,
        actor_type=ActorType.USER,
        action="transfer.completed",
        target_type="transaction",
        target_id=target_id,
        metadata={"amount": "100.00", "currency": "NGN"},
        ip_address="10.0.0.1",
    )
    await db_session.flush()

    resp = await client.get(
        "/api/v1/admin/audit-logs",
        headers=make_auth_headers(test_admin),
    )
    assert resp.status_code == 200
    item = resp.json()["data"]["items"][0]

    assert "id" in item
    assert "actor_id" in item
    assert "actor_type" in item
    assert "action" in item
    assert "target_type" in item
    assert "target_id" in item
    assert "metadata" in item
    assert "ip_address" in item
    assert "created_at" in item

    assert item["actor_id"] == str(test_user.id)
    assert item["actor_type"] == "user"
    assert item["action"] == "transfer.completed"
    assert item["target_type"] == "transaction"
    assert item["target_id"] == str(target_id)
    assert item["metadata"] == {"amount": "100.00", "currency": "NGN"}
    assert item["ip_address"] == "10.0.0.1"


@pytest.mark.asyncio
async def test_audit_logs_filter_by_action(
    client: AsyncClient,
    db_session: AsyncSession,
    test_admin: User,
    test_user: User,
) -> None:
    """action= filter returns only entries matching the exact action string."""
    repo = AuditLogRepository(db_session)
    await repo.create(
        actor_id=test_user.id, actor_type=ActorType.USER, action="transfer.completed"
    )
    await repo.create(
        actor_id=test_user.id, actor_type=ActorType.USER, action="user.login"
    )
    await db_session.flush()

    resp = await client.get(
        "/api/v1/admin/audit-logs?action=transfer.completed",
        headers=make_auth_headers(test_admin),
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["total"] == 1
    assert data["items"][0]["action"] == "transfer.completed"


@pytest.mark.asyncio
async def test_audit_logs_filter_by_actor_id(
    client: AsyncClient,
    db_session: AsyncSession,
    test_admin: User,
    test_user: User,
) -> None:
    """actor_id= filter returns only entries for the specified actor."""
    other_actor_id = uuid.uuid4()
    repo = AuditLogRepository(db_session)
    await repo.create(
        actor_id=test_user.id, actor_type=ActorType.USER, action="transfer.completed"
    )
    await repo.create(
        actor_id=other_actor_id,
        actor_type=ActorType.USER,
        action="user.login",
    )
    await db_session.flush()

    resp = await client.get(
        f"/api/v1/admin/audit-logs?actor_id={test_user.id}",
        headers=make_auth_headers(test_admin),
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["total"] == 1
    assert data["items"][0]["actor_id"] == str(test_user.id)


@pytest.mark.asyncio
async def test_audit_logs_filter_by_date_range(
    client: AsyncClient,
    db_session: AsyncSession,
    test_admin: User,
    test_user: User,
) -> None:
    """from_date and to_date filters correctly bound the result set."""
    repo = AuditLogRepository(db_session)
    await repo.create(
        actor_id=test_user.id, actor_type=ActorType.USER, action="user.login"
    )
    await db_session.flush()

    now = datetime.now(timezone.utc)
    from_iso = (now - timedelta(minutes=5)).isoformat()
    to_iso = (now + timedelta(minutes=5)).isoformat()

    # Entries within the range are returned
    resp = await client.get(
        f"/api/v1/admin/audit-logs?from_date={from_iso}&to_date={to_iso}",
        headers=make_auth_headers(test_admin),
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["total"] >= 1

    # Entries outside the future range are excluded
    future_from = (now + timedelta(hours=1)).isoformat()
    future_to = (now + timedelta(hours=2)).isoformat()
    resp2 = await client.get(
        f"/api/v1/admin/audit-logs?from_date={future_from}&to_date={future_to}",
        headers=make_auth_headers(test_admin),
    )
    assert resp2.status_code == 200
    assert resp2.json()["data"]["total"] == 0


@pytest.mark.asyncio
async def test_audit_logs_pagination(
    client: AsyncClient,
    db_session: AsyncSession,
    test_admin: User,
    test_user: User,
) -> None:
    """limit and offset query params correctly page through results."""
    repo = AuditLogRepository(db_session)
    for i in range(5):
        await repo.create(
            actor_id=test_user.id,
            actor_type=ActorType.USER,
            action=f"action.{i}",
        )
    await db_session.flush()

    # First page
    resp = await client.get(
        "/api/v1/admin/audit-logs?limit=2&offset=0",
        headers=make_auth_headers(test_admin),
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["total"] == 5
    assert len(data["items"]) == 2
    assert data["limit"] == 2
    assert data["offset"] == 0

    # Last page (only 1 remaining)
    resp2 = await client.get(
        "/api/v1/admin/audit-logs?limit=2&offset=4",
        headers=make_auth_headers(test_admin),
    )
    assert resp2.status_code == 200
    data2 = resp2.json()["data"]
    assert data2["total"] == 5
    assert len(data2["items"]) == 1


@pytest.mark.asyncio
async def test_audit_logs_newest_first(
    client: AsyncClient,
    db_session: AsyncSession,
    test_admin: User,
    test_user: User,
) -> None:
    """Entries are returned newest-first (descending created_at)."""
    repo = AuditLogRepository(db_session)
    await repo.create(
        actor_id=test_user.id, actor_type=ActorType.USER, action="action.alpha"
    )
    await repo.create(
        actor_id=test_user.id, actor_type=ActorType.USER, action="action.beta"
    )
    await db_session.flush()

    resp = await client.get(
        "/api/v1/admin/audit-logs",
        headers=make_auth_headers(test_admin),
    )
    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    assert len(items) == 2
    # created_at of [0] >= [1] — newest first
    ts0 = items[0]["created_at"]
    ts1 = items[1]["created_at"]
    assert ts0 >= ts1


@pytest.mark.asyncio
async def test_audit_logs_invalid_pagination_rejected(
    client: AsyncClient,
    test_admin: User,
) -> None:
    """limit=0 or negative offset should be rejected with 422."""
    resp = await client.get(
        "/api/v1/admin/audit-logs?limit=0",
        headers=make_auth_headers(test_admin),
    )
    assert resp.status_code == 422

    resp2 = await client.get(
        "/api/v1/admin/audit-logs?offset=-1",
        headers=make_auth_headers(test_admin),
    )
    assert resp2.status_code == 422
