"""
API integration tests for POST /api/v1/merchants/{merchant_id}/pay.

Webhook delivery tasks are patched so tests have no Redis/Celery dependency.
Fraud checks run for real; test amounts stay within Tier 1 limits (< 50 000 NGN).
"""

import uuid
from decimal import Decimal
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import generate_api_key, hash_password
from app.models.merchant import Merchant
from app.models.user import User, UserRole
from app.models.wallet import Wallet
from app.models.webhook_delivery import WebhookDelivery
from tests.conftest import make_auth_headers

# ── URL helper ────────────────────────────────────────────────────────────────


def pay_url(merchant_id: uuid.UUID) -> str:
    return f"/api/v1/merchants/{merchant_id}/pay"


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def payer(db_session: AsyncSession) -> User:
    """Tier 1 user with a funded wallet."""
    user = User(
        id=uuid.uuid4(),
        email="payer@example.com",
        hashed_password=hash_password("Pass1234!"),
        full_name="Payer User",
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
async def payer_wallet(db_session: AsyncSession, payer: User) -> Wallet:
    wallet = Wallet(
        id=uuid.uuid4(),
        user_id=payer.id,
        currency="NGN",
        balance=Decimal("10000.00"),
        is_active=True,
    )
    db_session.add(wallet)
    await db_session.flush()
    await db_session.refresh(wallet)
    return wallet


@pytest_asyncio.fixture
async def merchant_owner(db_session: AsyncSession) -> User:
    user = User(
        id=uuid.uuid4(),
        email="merchant_owner@example.com",
        hashed_password=hash_password("Pass1234!"),
        full_name="Merchant Owner",
        role=UserRole.MERCHANT,
        kyc_tier=0,
        is_active=True,
        is_email_verified=True,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def merchant_wallet(db_session: AsyncSession, merchant_owner: User) -> Wallet:
    wallet = Wallet(
        id=uuid.uuid4(),
        user_id=merchant_owner.id,
        currency="NGN",
        balance=Decimal("0.00"),
        is_active=True,
    )
    db_session.add(wallet)
    await db_session.flush()
    await db_session.refresh(wallet)
    return wallet


@pytest_asyncio.fixture
async def active_merchant(
    db_session: AsyncSession,
    merchant_owner: User,
    merchant_wallet: Wallet,
) -> Merchant:
    """Active merchant with a webhook URL configured."""
    _, prefix, hashed = generate_api_key()
    merchant = Merchant(
        id=uuid.uuid4(),
        user_id=merchant_owner.id,
        business_name="Test Shop",
        api_key_hash=hashed,
        api_key_prefix=prefix,
        webhook_url="https://example.com/webhook",
        webhook_secret=str(uuid.uuid4()),
        is_active=True,
    )
    db_session.add(merchant)
    await db_session.flush()
    await db_session.refresh(merchant)
    return merchant


@pytest_asyncio.fixture
async def merchant_no_webhook(
    db_session: AsyncSession,
    merchant_owner: User,
    merchant_wallet: Wallet,
) -> Merchant:
    """Active merchant with NO webhook URL."""
    _, prefix, hashed = generate_api_key()
    merchant = Merchant(
        id=uuid.uuid4(),
        user_id=merchant_owner.id,
        business_name="Webhook-less Shop",
        api_key_hash=hashed,
        api_key_prefix=prefix,
        webhook_url=None,
        webhook_secret=None,
        is_active=True,
    )
    db_session.add(merchant)
    await db_session.flush()
    await db_session.refresh(merchant)
    return merchant


# ── Unauthenticated ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pay_merchant_unauthenticated(
    client: AsyncClient, active_merchant: Merchant
) -> None:
    resp = await client.post(pay_url(active_merchant.id), json={"amount": "100.00"})
    assert resp.status_code == 401


# ── Request validation ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pay_merchant_zero_amount_rejected(
    client: AsyncClient, payer: User, payer_wallet: Wallet, active_merchant: Merchant
) -> None:
    resp = await client.post(
        pay_url(active_merchant.id),
        json={"amount": "0.00"},
        headers=make_auth_headers(payer),
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_pay_merchant_invalid_uuid_rejected(
    client: AsyncClient, payer: User, payer_wallet: Wallet
) -> None:
    resp = await client.post(
        "/api/v1/merchants/not-a-uuid/pay",
        json={"amount": "100.00"},
        headers=make_auth_headers(payer),
    )
    assert resp.status_code == 422


# ── Merchant resolution ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pay_merchant_not_found(
    client: AsyncClient, payer: User, payer_wallet: Wallet
) -> None:
    resp = await client.post(
        pay_url(uuid.uuid4()),
        json={"amount": "100.00"},
        headers=make_auth_headers(payer),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_pay_inactive_merchant_blocked(
    client: AsyncClient,
    db_session: AsyncSession,
    payer: User,
    payer_wallet: Wallet,
    active_merchant: Merchant,
) -> None:
    active_merchant.is_active = False
    await db_session.flush()

    resp = await client.post(
        pay_url(active_merchant.id),
        json={"amount": "100.00"},
        headers=make_auth_headers(payer),
    )
    assert resp.status_code == 403


# ── Self-payment ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pay_merchant_self_payment_blocked(
    client: AsyncClient,
    db_session: AsyncSession,
    merchant_owner: User,
    merchant_wallet: Wallet,
    active_merchant: Merchant,
) -> None:
    """The merchant's own user account cannot pay themselves."""
    resp = await client.post(
        pay_url(active_merchant.id),
        json={"amount": "100.00"},
        headers=make_auth_headers(merchant_owner),
    )
    assert resp.status_code == 422
    assert resp.json()["error"] == "SELF_PAYMENT"


# ── KYC enforcement ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pay_merchant_kyc_tier_0_blocked(
    client: AsyncClient,
    db_session: AsyncSession,
    active_merchant: Merchant,
) -> None:
    """Tier 0 users cannot initiate any payment."""
    tier0 = User(
        id=uuid.uuid4(),
        email="tier0payer@example.com",
        hashed_password=hash_password("Pass1234!"),
        full_name="Tier Zero",
        role=UserRole.USER,
        kyc_tier=0,
        is_active=True,
        is_email_verified=True,
    )
    db_session.add(tier0)
    tier0_wallet = Wallet(
        id=uuid.uuid4(),
        user_id=tier0.id,
        currency="NGN",
        balance=Decimal("5000.00"),
        is_active=True,
    )
    db_session.add(tier0_wallet)
    await db_session.flush()

    resp = await client.post(
        pay_url(active_merchant.id),
        json={"amount": "100.00"},
        headers=make_auth_headers(tier0),
    )
    assert resp.status_code == 403
    assert resp.json()["error"] == "KYC_TIER_INSUFFICIENT"


# ── Balance guard ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pay_merchant_insufficient_balance(
    client: AsyncClient,
    db_session: AsyncSession,
    payer: User,
    payer_wallet: Wallet,
    merchant_wallet: Wallet,
    active_merchant: Merchant,
) -> None:
    payer_wallet.balance = Decimal("50.00")
    await db_session.flush()

    with patch("app.workers.webhook_tasks.deliver_merchant_webhook"):
        resp = await client.post(
            pay_url(active_merchant.id),
            json={"amount": "100.00"},
            headers=make_auth_headers(payer),
        )
    assert resp.status_code == 422
    assert resp.json()["error"] == "INSUFFICIENT_BALANCE"

    # No balance changes
    await db_session.refresh(payer_wallet)
    await db_session.refresh(merchant_wallet)
    assert payer_wallet.balance == Decimal("50.00")
    assert merchant_wallet.balance == Decimal("0.00")


# ── Successful payment ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pay_merchant_success(
    client: AsyncClient,
    db_session: AsyncSession,
    payer: User,
    payer_wallet: Wallet,
    merchant_wallet: Wallet,
    active_merchant: Merchant,
) -> None:
    amount = Decimal("500.00")

    with patch("app.workers.webhook_tasks.deliver_merchant_webhook"):
        resp = await client.post(
            pay_url(active_merchant.id),
            json={"amount": str(amount)},
            headers=make_auth_headers(payer),
        )

    assert resp.status_code == 201
    body = resp.json()
    assert body["success"] is True
    data = body["data"]
    assert data["type"] == "merchant_payment"
    assert data["status"] == "completed"
    assert Decimal(data["amount"]) == amount
    assert data["source_wallet_id"] == str(payer_wallet.id)
    assert data["destination_wallet_id"] == str(merchant_wallet.id)
    assert data["initiated_by_user_id"] == str(payer.id)

    # Verify balances
    await db_session.refresh(payer_wallet)
    await db_session.refresh(merchant_wallet)
    assert payer_wallet.balance == Decimal("10000.00") - amount
    assert merchant_wallet.balance == amount


@pytest.mark.asyncio
async def test_pay_merchant_creates_ledger_entries(
    client: AsyncClient,
    db_session: AsyncSession,
    payer: User,
    payer_wallet: Wallet,
    merchant_wallet: Wallet,
    active_merchant: Merchant,
) -> None:
    """Payment must produce exactly one DEBIT and one CREDIT ledger entry."""
    from app.models.ledger_entry import EntryType, LedgerEntry

    with patch("app.workers.webhook_tasks.deliver_merchant_webhook"):
        resp = await client.post(
            pay_url(active_merchant.id),
            json={"amount": "1000.00"},
            headers=make_auth_headers(payer),
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
    assert debit.wallet_id == payer_wallet.id
    assert credit.wallet_id == merchant_wallet.id


# ── Webhook delivery ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pay_merchant_creates_webhook_delivery_record(
    client: AsyncClient,
    db_session: AsyncSession,
    payer: User,
    payer_wallet: Wallet,
    active_merchant: Merchant,
) -> None:
    """A WebhookDelivery record is created when the merchant has a webhook URL."""
    with patch(
        "app.workers.webhook_tasks.deliver_merchant_webhook"
    ) as mock_task:
        resp = await client.post(
            pay_url(active_merchant.id),
            json={"amount": "200.00"},
            headers=make_auth_headers(payer),
        )
    assert resp.status_code == 201
    txn_id = uuid.UUID(resp.json()["data"]["id"])

    # Delivery record persisted
    deliveries = (
        await db_session.execute(
            select(WebhookDelivery).where(
                WebhookDelivery.transaction_id == txn_id
            )
        )
    ).scalars().all()
    assert len(deliveries) == 1
    delivery = deliveries[0]
    assert delivery.merchant_id == active_merchant.id
    assert delivery.event_type == "payment.received"
    assert delivery.payload["event"] == "payment.received"
    assert delivery.payload["data"]["amount"] == "200.00"

    # Delivery task was enqueued
    mock_task.delay.assert_called_once_with(str(delivery.id))


@pytest.mark.asyncio
async def test_pay_merchant_no_webhook_url_skips_delivery(
    client: AsyncClient,
    db_session: AsyncSession,
    payer: User,
    payer_wallet: Wallet,
    merchant_no_webhook: Merchant,
) -> None:
    """No WebhookDelivery record is created if the merchant has no webhook URL."""
    with patch("app.workers.webhook_tasks.deliver_merchant_webhook") as mock_task:
        resp = await client.post(
            pay_url(merchant_no_webhook.id),
            json={"amount": "100.00"},
            headers=make_auth_headers(payer),
        )
    assert resp.status_code == 201
    txn_id = uuid.UUID(resp.json()["data"]["id"])

    deliveries = (
        await db_session.execute(
            select(WebhookDelivery).where(
                WebhookDelivery.transaction_id == txn_id
            )
        )
    ).scalars().all()
    assert len(deliveries) == 0
    mock_task.delay.assert_not_called()


# ── Idempotency ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pay_merchant_idempotency(
    client: AsyncClient,
    db_session: AsyncSession,
    payer: User,
    payer_wallet: Wallet,
    active_merchant: Merchant,
) -> None:
    """Same idempotency key returns the original transaction without double-charging."""
    idem_key = f"pay-idem-{uuid.uuid4()}"
    payload = {"amount": "300.00", "idempotency_key": idem_key}

    with patch("app.workers.webhook_tasks.deliver_merchant_webhook"):
        resp1 = await client.post(
            pay_url(active_merchant.id),
            json=payload,
            headers=make_auth_headers(payer),
        )
    assert resp1.status_code == 201
    txn_id_first = resp1.json()["data"]["id"]

    with patch("app.workers.webhook_tasks.deliver_merchant_webhook"):
        resp2 = await client.post(
            pay_url(active_merchant.id),
            json=payload,
            headers=make_auth_headers(payer),
        )
    assert resp2.status_code == 201
    txn_id_second = resp2.json()["data"]["id"]

    assert txn_id_first == txn_id_second

    # Only charged once
    await db_session.refresh(payer_wallet)
    assert payer_wallet.balance == Decimal("10000.00") - Decimal("300.00")


# ── Inactive payer wallet ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pay_merchant_inactive_payer_wallet(
    client: AsyncClient,
    db_session: AsyncSession,
    payer: User,
    payer_wallet: Wallet,
    active_merchant: Merchant,
) -> None:
    payer_wallet.is_active = False
    await db_session.flush()

    resp = await client.post(
        pay_url(active_merchant.id),
        json={"amount": "100.00"},
        headers=make_auth_headers(payer),
    )
    assert resp.status_code == 403
