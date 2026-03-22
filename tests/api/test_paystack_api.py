"""
Tests for the Paystack component.

Coverage
--------
Wallet funding (POST /api/v1/wallets/fund):
  - Unauthenticated → 401
  - Zero / negative amount → 422 (Pydantic)
  - Below 100 NGN minimum → 422 BELOW_MINIMUM_AMOUNT
  - Inactive wallet → 403
  - No wallet → 403
  - Paystack client unavailable → 503
  - Success → 201, PENDING transaction in DB, payment_url returned
  - Idempotency → same transaction returned on repeat request

Paystack webhook (POST /api/v1/webhooks/paystack):
  - Missing / invalid signature → 401
  - Valid signature, charge.success → 200, task enqueued
  - Valid signature, unknown event → 200 (always accept)
  - Malformed JSON → 422 (caught before task enqueue)

PaystackWebhookService.process_charge_success (integration):
  - Credits wallet, completes transaction, writes ledger entry
  - Duplicate event → idempotent skip, no double credit
  - Unknown provider_reference → no-op (no crash)
"""

import hashlib
import hmac
import json
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import hash_password
from app.models.ledger_entry import EntryType, LedgerEntry
from app.models.transaction import Transaction, TransactionStatus, TransactionType
from app.models.user import User, UserRole
from app.models.wallet import Wallet
from tests.conftest import make_auth_headers

# ── Test constants ─────────────────────────────────────────────────────────────

FUND_URL = "/api/v1/wallets/fund"
WEBHOOK_URL = "/api/v1/webhooks/paystack"
TEST_WEBHOOK_SECRET = "test_webhook_secret_abc123"


def make_paystack_signature(body: bytes, secret: str = TEST_WEBHOOK_SECRET) -> str:
    """Compute HMAC-SHA512 signature matching Paystack's algorithm."""
    return hmac.new(secret.encode(), body, hashlib.sha512).hexdigest()


# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def funded_user(db_session: AsyncSession) -> User:
    """Tier-1 user with a funded wallet."""
    user = User(
        id=uuid.uuid4(),
        email="funder@example.com",
        hashed_password=hash_password("Pass1234!"),
        full_name="Fund User",
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
async def funded_wallet(db_session: AsyncSession, funded_user: User) -> Wallet:
    wallet = Wallet(
        id=uuid.uuid4(),
        user_id=funded_user.id,
        currency="NGN",
        balance=Decimal("0.00"),
        is_active=True,
    )
    db_session.add(wallet)
    await db_session.flush()
    await db_session.refresh(wallet)
    return wallet


# ── Paystack mock helpers ──────────────────────────────────────────────────────


def mock_paystack_init(payment_url: str = "https://checkout.paystack.com/test123") -> MagicMock:
    """Return a mock PaystackClient whose initialize_transaction succeeds."""
    mock = AsyncMock()
    mock.initialize_transaction.return_value = {
        "authorization_url": payment_url,
        "access_code": "acc_test",
        "reference": "txn_mock_ref",
    }
    return mock


# ══════════════════════════════════════════════════════════════════════════════
# POST /api/v1/wallets/fund
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_fund_wallet_unauthenticated(client: AsyncClient) -> None:
    resp = await client.post(FUND_URL, json={"amount": "500.00"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_fund_wallet_zero_amount_rejected(
    client: AsyncClient,
    funded_user: User,
    funded_wallet: Wallet,
) -> None:
    resp = await client.post(
        FUND_URL,
        json={"amount": "0.00"},
        headers=make_auth_headers(funded_user),
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_fund_wallet_below_minimum_rejected(
    client: AsyncClient,
    funded_user: User,
    funded_wallet: Wallet,
) -> None:
    """Amounts < 100 NGN are rejected before calling Paystack."""
    resp = await client.post(
        FUND_URL,
        json={"amount": "99.99"},
        headers=make_auth_headers(funded_user),
    )
    assert resp.status_code == 422
    assert resp.json()["error"] == "BELOW_MINIMUM_AMOUNT"


@pytest.mark.asyncio
async def test_fund_wallet_inactive_wallet_rejected(
    client: AsyncClient,
    db_session: AsyncSession,
    funded_user: User,
    funded_wallet: Wallet,
) -> None:
    funded_wallet.is_active = False
    await db_session.flush()

    resp = await client.post(
        FUND_URL,
        json={"amount": "500.00"},
        headers=make_auth_headers(funded_user),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_fund_wallet_no_wallet_rejected(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """User with no wallet gets 403."""
    no_wallet_user = User(
        id=uuid.uuid4(),
        email="nowallet@example.com",
        hashed_password=hash_password("Pass1234!"),
        full_name="No Wallet",
        role=UserRole.USER,
        kyc_tier=0,
        is_active=True,
        is_email_verified=True,
    )
    db_session.add(no_wallet_user)
    await db_session.flush()

    resp = await client.post(
        FUND_URL,
        json={"amount": "500.00"},
        headers=make_auth_headers(no_wallet_user),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_fund_wallet_paystack_down_returns_503(
    client: AsyncClient,
    funded_user: User,
    funded_wallet: Wallet,
    db_session: AsyncSession,
) -> None:
    """If Paystack is unreachable, 503 is returned and no transaction row is created."""
    from app.core.exceptions import ExternalServiceError

    with patch(
        "app.services.wallet_funding.PaystackClient"
    ) as MockClient:
        instance = MockClient.return_value
        instance.initialize_transaction = AsyncMock(
            side_effect=ExternalServiceError("Paystack")
        )
        resp = await client.post(
            FUND_URL,
            json={"amount": "500.00"},
            headers=make_auth_headers(funded_user),
        )

    assert resp.status_code == 503

    # No FUNDING transaction should have been created
    txns = (
        await db_session.execute(
            select(Transaction).where(
                Transaction.initiated_by_user_id == funded_user.id,
                Transaction.type == TransactionType.FUNDING,
            )
        )
    ).scalars().all()
    assert len(txns) == 0


@pytest.mark.asyncio
async def test_fund_wallet_success(
    client: AsyncClient,
    funded_user: User,
    funded_wallet: Wallet,
    db_session: AsyncSession,
) -> None:
    """Successful funding creates a PENDING transaction and returns the payment URL."""
    payment_url = "https://checkout.paystack.com/abc123"

    with patch(
        "app.services.wallet_funding.PaystackClient"
    ) as MockClient:
        MockClient.return_value = mock_paystack_init(payment_url)
        resp = await client.post(
            FUND_URL,
            json={"amount": "1000.00"},
            headers=make_auth_headers(funded_user),
        )

    assert resp.status_code == 201
    body = resp.json()
    assert body["success"] is True
    data = body["data"]
    assert data["payment_url"] == payment_url
    assert Decimal(data["amount"]) == Decimal("1000.00")
    assert data["currency"] == "NGN"
    assert "transaction_id" in data
    assert "reference" in data

    # PENDING transaction persisted
    txn = (
        await db_session.execute(
            select(Transaction).where(
                Transaction.id == uuid.UUID(data["transaction_id"])
            )
        )
    ).scalar_one()
    assert txn.status == TransactionStatus.PENDING
    assert txn.type == TransactionType.FUNDING
    assert txn.destination_wallet_id == funded_wallet.id
    assert txn.extra_data["payment_url"] == payment_url


@pytest.mark.asyncio
async def test_fund_wallet_idempotency(
    client: AsyncClient,
    funded_user: User,
    funded_wallet: Wallet,
    db_session: AsyncSession,
) -> None:
    """Same idempotency key returns the original transaction without calling Paystack again."""
    idem_key = f"fund-idem-{uuid.uuid4()}"
    payment_url = "https://checkout.paystack.com/idem_test"

    with patch("app.services.wallet_funding.PaystackClient") as MockClient:
        MockClient.return_value = mock_paystack_init(payment_url)
        resp1 = await client.post(
            FUND_URL,
            json={"amount": "500.00", "idempotency_key": idem_key},
            headers=make_auth_headers(funded_user),
        )
    assert resp1.status_code == 201
    txn_id_first = resp1.json()["data"]["transaction_id"]

    # Second request — Paystack must NOT be called again
    with patch("app.services.wallet_funding.PaystackClient") as MockClient2:
        MockClient2.return_value = mock_paystack_init("https://different.url/")
        resp2 = await client.post(
            FUND_URL,
            json={"amount": "500.00", "idempotency_key": idem_key},
            headers=make_auth_headers(funded_user),
        )
    assert resp2.status_code == 201
    txn_id_second = resp2.json()["data"]["transaction_id"]

    assert txn_id_first == txn_id_second
    # Original payment URL returned
    assert resp2.json()["data"]["payment_url"] == payment_url

    # Only one transaction in DB
    txns = (
        await db_session.execute(
            select(Transaction).where(
                Transaction.initiated_by_user_id == funded_user.id,
                Transaction.type == TransactionType.FUNDING,
            )
        )
    ).scalars().all()
    assert len(txns) == 1


# ══════════════════════════════════════════════════════════════════════════════
# POST /api/v1/webhooks/paystack
# ══════════════════════════════════════════════════════════════════════════════


def make_charge_success_payload(reference: str, amount_kobo: int = 100_000) -> bytes:
    return json.dumps(
        {
            "event": "charge.success",
            "data": {
                "reference": reference,
                "amount": amount_kobo,
                "status": "success",
            },
        }
    ).encode()


@pytest.mark.asyncio
async def test_webhook_missing_signature_rejected(client: AsyncClient) -> None:
    body = make_charge_success_payload("ref_test_missing")
    resp = await client.post(
        WEBHOOK_URL,
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_webhook_invalid_signature_rejected(client: AsyncClient) -> None:
    body = make_charge_success_payload("ref_test_invalid")
    resp = await client.post(
        WEBHOOK_URL,
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Paystack-Signature": "invalidsignature",
        },
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_webhook_valid_signature_charge_success_accepted(
    client: AsyncClient,
) -> None:
    """Valid signature → 200 and task enqueued."""
    reference = f"ref_{uuid.uuid4().hex[:12]}"
    body = make_charge_success_payload(reference)
    sig = make_paystack_signature(body)

    with (
        patch.object(settings, "PAYSTACK_WEBHOOK_SECRET", TEST_WEBHOOK_SECRET),
        patch("app.workers.paystack_tasks.process_paystack_webhook") as mock_task,
    ):
        resp = await client.post(
            WEBHOOK_URL,
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Paystack-Signature": sig,
            },
        )

    assert resp.status_code == 200
    assert resp.json()["success"] is True
    mock_task.delay.assert_called_once_with(
        "charge.success",
        {
            "reference": reference,
            "amount": 100_000,
            "status": "success",
        },
    )


@pytest.mark.asyncio
async def test_webhook_valid_signature_unknown_event_accepted(
    client: AsyncClient,
) -> None:
    """Unknown events are accepted (200) without error — never drop a validated webhook."""
    body = json.dumps({"event": "subscription.not_renew", "data": {}}).encode()
    sig = make_paystack_signature(body)

    with (
        patch.object(settings, "PAYSTACK_WEBHOOK_SECRET", TEST_WEBHOOK_SECRET),
        patch("app.workers.paystack_tasks.process_paystack_webhook") as mock_task,
    ):
        resp = await client.post(
            WEBHOOK_URL,
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Paystack-Signature": sig,
            },
        )

    assert resp.status_code == 200
    mock_task.delay.assert_called_once_with("subscription.not_renew", {})


@pytest.mark.asyncio
async def test_webhook_malformed_json_rejected(
    client: AsyncClient,
) -> None:
    """Valid signature but unparseable body → 422."""
    body = b"not valid json {"
    sig = make_paystack_signature(body)

    with patch.object(settings, "PAYSTACK_WEBHOOK_SECRET", TEST_WEBHOOK_SECRET):
        resp = await client.post(
            WEBHOOK_URL,
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Paystack-Signature": sig,
            },
        )

    assert resp.status_code == 422


# ══════════════════════════════════════════════════════════════════════════════
# PaystackWebhookService.process_charge_success — integration
# ══════════════════════════════════════════════════════════════════════════════


@pytest_asyncio.fixture
async def pending_funding_txn(
    db_session: AsyncSession,
    funded_user: User,
    funded_wallet: Wallet,
) -> Transaction:
    """A PENDING FUNDING transaction awaiting Paystack confirmation."""
    txn = Transaction(
        reference=f"txn_{uuid.uuid4().hex}",
        type=TransactionType.FUNDING,
        status=TransactionStatus.PENDING,
        amount=Decimal("2000.00"),
        currency="NGN",
        destination_wallet_id=funded_wallet.id,
        initiated_by_user_id=funded_user.id,
        provider_reference="ps_ref_test_001",
    )
    db_session.add(txn)
    await db_session.flush()
    await db_session.refresh(txn)
    return txn


@pytest.mark.asyncio
async def test_charge_success_credits_wallet(
    db_session: AsyncSession,
    funded_wallet: Wallet,
    pending_funding_txn: Transaction,
) -> None:
    """charge.success credits wallet, completes transaction, writes ledger entry."""
    from app.services.paystack_webhook import PaystackWebhookService

    initial_balance = funded_wallet.balance  # 0.00

    service = PaystackWebhookService(db_session)
    await service.process_charge_success(
        {
            "reference": "ps_ref_test_001",
            "amount": 200_000,  # 2000 NGN in kobo
        }
    )

    # Wallet credited
    await db_session.refresh(funded_wallet)
    assert funded_wallet.balance == initial_balance + Decimal("2000.00")

    # Transaction COMPLETED
    await db_session.refresh(pending_funding_txn)
    assert pending_funding_txn.status == TransactionStatus.COMPLETED

    # Ledger CREDIT entry written
    entries = (
        await db_session.execute(
            select(LedgerEntry).where(
                LedgerEntry.transaction_id == pending_funding_txn.id
            )
        )
    ).scalars().all()
    assert len(entries) == 1
    entry = entries[0]
    assert entry.entry_type == EntryType.CREDIT
    assert entry.wallet_id == funded_wallet.id
    assert entry.amount == Decimal("2000.00")
    assert entry.balance_after == Decimal("2000.00")


@pytest.mark.asyncio
async def test_charge_success_idempotent_skip(
    db_session: AsyncSession,
    funded_wallet: Wallet,
    pending_funding_txn: Transaction,
) -> None:
    """Duplicate charge.success event does not double-credit the wallet."""
    from app.services.paystack_webhook import PaystackWebhookService

    service = PaystackWebhookService(db_session)
    data = {"reference": "ps_ref_test_001", "amount": 200_000}

    # First delivery
    await service.process_charge_success(data)
    await db_session.refresh(funded_wallet)
    balance_after_first = funded_wallet.balance

    # Second delivery (duplicate)
    await service.process_charge_success(data)
    await db_session.refresh(funded_wallet)

    # Balance unchanged after second delivery
    assert funded_wallet.balance == balance_after_first

    # Still only one ledger entry
    entries = (
        await db_session.execute(
            select(LedgerEntry).where(
                LedgerEntry.transaction_id == pending_funding_txn.id
            )
        )
    ).scalars().all()
    assert len(entries) == 1


@pytest.mark.asyncio
async def test_charge_success_orphan_reference_no_error(
    db_session: AsyncSession,
    funded_wallet: Wallet,
) -> None:
    """An unknown provider_reference is a no-op — no crash, no DB mutation."""
    from app.services.paystack_webhook import PaystackWebhookService

    initial_balance = funded_wallet.balance
    service = PaystackWebhookService(db_session)
    # Should not raise
    await service.process_charge_success(
        {"reference": "completely_unknown_ref", "amount": 100_000}
    )

    await db_session.refresh(funded_wallet)
    assert funded_wallet.balance == initial_balance


# ── Signature unit tests ───────────────────────────────────────────────────────


def test_verify_signature_valid() -> None:
    from app.services.paystack_webhook import PaystackWebhookService

    body = b'{"event":"charge.success","data":{"reference":"ref_001"}}'
    secret = "my_webhook_secret"
    sig = hmac.new(secret.encode(), body, hashlib.sha512).hexdigest()

    with patch.object(settings, "PAYSTACK_WEBHOOK_SECRET", secret):
        assert PaystackWebhookService.verify_signature(body, sig) is True


def test_verify_signature_invalid() -> None:
    from app.services.paystack_webhook import PaystackWebhookService

    body = b'{"event":"charge.success"}'
    with patch.object(settings, "PAYSTACK_WEBHOOK_SECRET", "real_secret"):
        assert PaystackWebhookService.verify_signature(body, "wrong_sig") is False


def test_verify_signature_no_secret_configured() -> None:
    from app.services.paystack_webhook import PaystackWebhookService

    body = b'{"event":"charge.success"}'
    sig = hmac.new(b"any_secret", body, hashlib.sha512).hexdigest()

    with patch.object(settings, "PAYSTACK_WEBHOOK_SECRET", ""):
        assert PaystackWebhookService.verify_signature(body, sig) is False
