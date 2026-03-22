"""
API integration tests for POST /api/v1/transfers.

Isolation
---------
All tests use the savepoint-based fixture from conftest — service-level
session.commit() calls become savepoint releases, and the outer
conn.rollback() in db_connection cleans everything after each test.

Fraud checks run for real against the test DB.  Test amounts are kept well
within KYC Tier 1 single-transaction and daily limits (< 50 000 NGN per
transfer, < 200 000 NGN total seeded) to avoid triggering FraudService
guards unintentionally.
"""

import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.models.transaction import Transaction, TransactionStatus, TransactionType
from app.models.user import User, UserRole
from app.models.wallet import Wallet
from tests.conftest import make_auth_headers

# ── URL ───────────────────────────────────────────────────────────────────────

URL = "/api/v1/transfers"


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def sender(db_session: AsyncSession) -> User:
    """Tier 1 active user — can initiate transfers up to 50 000 NGN."""
    from app.core.security import hash_password

    user = User(
        id=uuid.uuid4(),
        email="sender@example.com",
        hashed_password=hash_password("Pass1234!"),
        full_name="Sender User",
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
async def sender_wallet(db_session: AsyncSession, sender: User) -> Wallet:
    wallet = Wallet(
        id=uuid.uuid4(),
        user_id=sender.id,
        currency="NGN",
        balance=Decimal("10000.00"),
        is_active=True,
    )
    db_session.add(wallet)
    await db_session.flush()
    await db_session.refresh(wallet)
    return wallet


@pytest_asyncio.fixture
async def recipient(db_session: AsyncSession) -> User:
    from app.core.security import hash_password

    user = User(
        id=uuid.uuid4(),
        email="recipient@example.com",
        hashed_password=hash_password("Pass1234!"),
        full_name="Recipient User",
        role=UserRole.USER,
        kyc_tier=0,
        is_active=True,
        is_email_verified=True,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def recipient_wallet(db_session: AsyncSession, recipient: User) -> Wallet:
    wallet = Wallet(
        id=uuid.uuid4(),
        user_id=recipient.id,
        currency="NGN",
        balance=Decimal("0.00"),
        is_active=True,
    )
    db_session.add(wallet)
    await db_session.flush()
    await db_session.refresh(wallet)
    return wallet


# ── Unauthenticated ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_transfer_unauthenticated(client: AsyncClient) -> None:
    resp = await client.post(URL, json={"recipient_user_id": str(uuid.uuid4()), "amount": "100.00"})
    assert resp.status_code == 401


# ── Request validation (Pydantic) ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_transfer_both_recipient_fields_rejected(
    client: AsyncClient, sender: User, sender_wallet: Wallet, recipient: User
) -> None:
    """Providing both recipient_user_id and recipient_email is invalid."""
    resp = await client.post(
        URL,
        json={
            "recipient_user_id": str(recipient.id),
            "recipient_email": recipient.email,
            "amount": "100.00",
        },
        headers=make_auth_headers(sender),
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_transfer_no_recipient_field_rejected(
    client: AsyncClient, sender: User, sender_wallet: Wallet
) -> None:
    resp = await client.post(
        URL,
        json={"amount": "100.00"},
        headers=make_auth_headers(sender),
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_transfer_zero_amount_rejected(
    client: AsyncClient, sender: User, sender_wallet: Wallet, recipient: User
) -> None:
    resp = await client.post(
        URL,
        json={"recipient_user_id": str(recipient.id), "amount": "0.00"},
        headers=make_auth_headers(sender),
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_transfer_negative_amount_rejected(
    client: AsyncClient, sender: User, sender_wallet: Wallet, recipient: User
) -> None:
    resp = await client.post(
        URL,
        json={"recipient_user_id": str(recipient.id), "amount": "-50.00"},
        headers=make_auth_headers(sender),
    )
    assert resp.status_code == 422


# ── Self-transfer ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_transfer_self_rejected(
    client: AsyncClient, sender: User, sender_wallet: Wallet
) -> None:
    resp = await client.post(
        URL,
        json={"recipient_user_id": str(sender.id), "amount": "100.00"},
        headers=make_auth_headers(sender),
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"] == "SELF_TRANSFER"


# ── KYC tier enforcement ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_transfer_kyc_tier_0_blocked(
    client: AsyncClient,
    db_session: AsyncSession,
    sender_wallet: Wallet,
    recipient: User,
    recipient_wallet: Wallet,
) -> None:
    """Tier 0 users cannot initiate any transfer."""
    from app.core.security import hash_password

    tier0 = User(
        id=uuid.uuid4(),
        email="tier0@example.com",
        hashed_password=hash_password("Pass1234!"),
        full_name="Tier Zero",
        role=UserRole.USER,
        kyc_tier=0,
        is_active=True,
        is_email_verified=True,
    )
    db_session.add(tier0)
    wallet = Wallet(
        id=uuid.uuid4(),
        user_id=tier0.id,
        currency="NGN",
        balance=Decimal("5000.00"),
        is_active=True,
    )
    db_session.add(wallet)
    await db_session.flush()

    resp = await client.post(
        URL,
        json={"recipient_user_id": str(recipient.id), "amount": "100.00"},
        headers=make_auth_headers(tier0),
    )
    assert resp.status_code == 403
    assert resp.json()["error"] == "KYC_TIER_INSUFFICIENT"


@pytest.mark.asyncio
async def test_transfer_amount_exceeds_single_limit_blocked(
    client: AsyncClient,
    sender: User,
    sender_wallet: Wallet,
    recipient: User,
    recipient_wallet: Wallet,
    db_session: AsyncSession,
) -> None:
    """Tier 1 single-transaction limit is 50 000 NGN."""
    # Give sender enough balance
    sender_wallet.balance = Decimal("100000.00")
    await db_session.flush()

    resp = await client.post(
        URL,
        json={"recipient_user_id": str(recipient.id), "amount": "50001.00"},
        headers=make_auth_headers(sender),
    )
    assert resp.status_code == 403
    assert resp.json()["error"] == "KYC_TIER_INSUFFICIENT"


# ── Recipient resolution ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_transfer_recipient_not_found(
    client: AsyncClient, sender: User, sender_wallet: Wallet
) -> None:
    resp = await client.post(
        URL,
        json={"recipient_user_id": str(uuid.uuid4()), "amount": "100.00"},
        headers=make_auth_headers(sender),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_transfer_inactive_recipient_blocked(
    client: AsyncClient,
    sender: User,
    sender_wallet: Wallet,
    recipient: User,
    recipient_wallet: Wallet,
    db_session: AsyncSession,
) -> None:
    recipient.is_active = False
    await db_session.flush()

    resp = await client.post(
        URL,
        json={"recipient_user_id": str(recipient.id), "amount": "100.00"},
        headers=make_auth_headers(sender),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_transfer_recipient_inactive_wallet_blocked(
    client: AsyncClient,
    sender: User,
    sender_wallet: Wallet,
    recipient: User,
    recipient_wallet: Wallet,
    db_session: AsyncSession,
) -> None:
    recipient_wallet.is_active = False
    await db_session.flush()

    resp = await client.post(
        URL,
        json={"recipient_user_id": str(recipient.id), "amount": "100.00"},
        headers=make_auth_headers(sender),
    )
    assert resp.status_code == 403
    assert resp.json()["error"] == "FORBIDDEN"


# ── Sender wallet guards ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_transfer_inactive_sender_wallet_blocked(
    client: AsyncClient,
    sender: User,
    sender_wallet: Wallet,
    recipient: User,
    recipient_wallet: Wallet,
    db_session: AsyncSession,
) -> None:
    sender_wallet.is_active = False
    await db_session.flush()

    resp = await client.post(
        URL,
        json={"recipient_user_id": str(recipient.id), "amount": "100.00"},
        headers=make_auth_headers(sender),
    )
    assert resp.status_code == 403


# ── Insufficient balance ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_transfer_insufficient_balance_blocked(
    client: AsyncClient,
    db_session: AsyncSession,
    sender: User,
    sender_wallet: Wallet,
    recipient: User,
    recipient_wallet: Wallet,
) -> None:
    sender_wallet.balance = Decimal("50.00")
    await db_session.flush()

    resp = await client.post(
        URL,
        json={"recipient_user_id": str(recipient.id), "amount": "100.00"},
        headers=make_auth_headers(sender),
    )
    assert resp.status_code == 422
    assert resp.json()["error"] == "INSUFFICIENT_BALANCE"

    # Verify no DB changes
    await db_session.refresh(sender_wallet)
    await db_session.refresh(recipient_wallet)
    assert sender_wallet.balance == Decimal("50.00")
    assert recipient_wallet.balance == Decimal("0.00")


# ── Successful transfer ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_transfer_success_by_user_id(
    client: AsyncClient,
    db_session: AsyncSession,
    sender: User,
    sender_wallet: Wallet,
    recipient: User,
    recipient_wallet: Wallet,
) -> None:
    """Full happy path: balances updated, ledger entries written."""
    initial_sender_balance = sender_wallet.balance
    amount = Decimal("500.00")

    resp = await client.post(
        URL,
        json={"recipient_user_id": str(recipient.id), "amount": str(amount)},
        headers=make_auth_headers(sender),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["success"] is True
    data = body["data"]
    assert data["type"] == "transfer"
    assert data["status"] == "completed"
    assert Decimal(data["amount"]) == amount
    assert data["source_wallet_id"] == str(sender_wallet.id)
    assert data["destination_wallet_id"] == str(recipient_wallet.id)
    assert data["initiated_by_user_id"] == str(sender.id)

    # Verify balances persisted
    await db_session.refresh(sender_wallet)
    await db_session.refresh(recipient_wallet)
    assert sender_wallet.balance == initial_sender_balance - amount
    assert recipient_wallet.balance == amount


@pytest.mark.asyncio
async def test_transfer_success_by_email(
    client: AsyncClient,
    db_session: AsyncSession,
    sender: User,
    sender_wallet: Wallet,
    recipient: User,
    recipient_wallet: Wallet,
) -> None:
    """Transfer resolved via recipient_email field."""
    amount = Decimal("250.00")

    resp = await client.post(
        URL,
        json={"recipient_email": recipient.email, "amount": str(amount)},
        headers=make_auth_headers(sender),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["success"] is True
    assert Decimal(body["data"]["amount"]) == amount

    await db_session.refresh(sender_wallet)
    await db_session.refresh(recipient_wallet)
    assert sender_wallet.balance == Decimal("10000.00") - amount
    assert recipient_wallet.balance == amount


@pytest.mark.asyncio
async def test_transfer_creates_two_ledger_entries(
    client: AsyncClient,
    db_session: AsyncSession,
    sender: User,
    sender_wallet: Wallet,
    recipient: User,
    recipient_wallet: Wallet,
) -> None:
    """A successful transfer must produce exactly one DEBIT and one CREDIT ledger entry."""
    from sqlalchemy import select

    from app.models.ledger_entry import EntryType, LedgerEntry

    resp = await client.post(
        URL,
        json={"recipient_user_id": str(recipient.id), "amount": "1000.00"},
        headers=make_auth_headers(sender),
    )
    assert resp.status_code == 201
    txn_id = resp.json()["data"]["id"]

    entries = (
        await db_session.execute(
            select(LedgerEntry).where(
                LedgerEntry.transaction_id == uuid.UUID(txn_id)
            )
        )
    ).scalars().all()

    assert len(entries) == 2
    types = {e.entry_type for e in entries}
    assert EntryType.DEBIT in types
    assert EntryType.CREDIT in types

    debit = next(e for e in entries if e.entry_type == EntryType.DEBIT)
    credit = next(e for e in entries if e.entry_type == EntryType.CREDIT)
    assert debit.wallet_id == sender_wallet.id
    assert credit.wallet_id == recipient_wallet.id
    assert debit.amount == Decimal("1000.00")
    assert credit.amount == Decimal("1000.00")
    assert debit.balance_after == Decimal("10000.00") - Decimal("1000.00")
    assert credit.balance_after == Decimal("1000.00")


# ── Idempotency ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_transfer_idempotency_returns_same_transaction(
    client: AsyncClient,
    db_session: AsyncSession,
    sender: User,
    sender_wallet: Wallet,
    recipient: User,
    recipient_wallet: Wallet,
) -> None:
    """Submitting the same idempotency_key twice returns the original transaction."""
    idem_key = f"idem-{uuid.uuid4()}"
    payload = {
        "recipient_user_id": str(recipient.id),
        "amount": "300.00",
        "idempotency_key": idem_key,
    }

    resp1 = await client.post(URL, json=payload, headers=make_auth_headers(sender))
    assert resp1.status_code == 201
    txn_id_first = resp1.json()["data"]["id"]

    resp2 = await client.post(URL, json=payload, headers=make_auth_headers(sender))
    assert resp2.status_code == 201
    txn_id_second = resp2.json()["data"]["id"]

    # Same transaction returned; no second debit applied
    assert txn_id_first == txn_id_second

    await db_session.refresh(sender_wallet)
    assert sender_wallet.balance == Decimal("10000.00") - Decimal("300.00")


# ── Duplicate detection ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_transfer_duplicate_within_window_blocked(
    client: AsyncClient,
    db_session: AsyncSession,
    sender: User,
    sender_wallet: Wallet,
    recipient: User,
    recipient_wallet: Wallet,
) -> None:
    """
    Two identical transfers (same sender, recipient, amount) within the 60-second
    duplicate window should be blocked.  The first succeeds; the second is rejected.
    """
    sender_wallet.balance = Decimal("5000.00")
    await db_session.flush()

    payload = {"recipient_user_id": str(recipient.id), "amount": "100.00"}

    resp1 = await client.post(URL, json=payload, headers=make_auth_headers(sender))
    assert resp1.status_code == 201

    resp2 = await client.post(URL, json=payload, headers=make_auth_headers(sender))
    assert resp2.status_code == 429
    assert resp2.json()["error"] == "DUPLICATE_TRANSFER"


# ── End-to-end: multiple transfers, running balance ──────────────────────────


@pytest.mark.asyncio
async def test_transfer_running_balance_correct(
    client: AsyncClient,
    db_session: AsyncSession,
    sender: User,
    sender_wallet: Wallet,
    recipient: User,
    recipient_wallet: Wallet,
) -> None:
    """
    Three sequential transfers with distinct amounts; verify the final
    balances are arithmetically correct.
    """
    sender_wallet.balance = Decimal("3000.00")
    await db_session.flush()

    amounts = [Decimal("100.00"), Decimal("200.00"), Decimal("300.00")]
    for amt in amounts:
        # Use unique idempotency keys so the duplicate window does not block them
        resp = await client.post(
            URL,
            json={
                "recipient_user_id": str(recipient.id),
                "amount": str(amt),
                "idempotency_key": f"seq-{uuid.uuid4()}",
            },
            headers=make_auth_headers(sender),
        )
        assert resp.status_code == 201

    await db_session.refresh(sender_wallet)
    await db_session.refresh(recipient_wallet)
    assert sender_wallet.balance == Decimal("3000.00") - sum(amounts)
    assert recipient_wallet.balance == sum(amounts)
