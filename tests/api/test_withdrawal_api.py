"""
Tests for the Withdrawals component.

Coverage
--------
Bank accounts (POST/GET/DELETE /bank-accounts):
  - Unauthenticated → 401
  - Add first account → 201, is_default=True
  - Add second account → 201, is_default=False
  - List accounts → 200, ordered by created_at
  - Remove own account → 200, soft-deleted
  - Remove someone else's account → 404
  - Cannot remove account with active withdrawal → 403

Withdrawals (POST /withdrawals, GET /withdrawals/{reference}):
  - Unauthenticated → 401
  - KYC Tier < 2 → 403 KYC_TIER_INSUFFICIENT
  - No bank account found → 404
  - Insufficient balance → 422 INSUFFICIENT_BALANCE
  - Success → 201, balance held, PENDING txn, task enqueued
  - Duplicate active withdrawal → 422 WITHDRAWAL_ALREADY_PENDING
  - Get withdrawal status → 200
  - Get withdrawal status for another user's ref → 404

WithdrawalService integration (async service calls):
  - process_payout_success → COMPLETED, DEBIT ledger entry, balance unchanged
  - process_payout_failure → FAILED, balance restored
  - process_payout_success idempotent → no double-write
  - process_payout_failure idempotent → no double-credit
"""

import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models.bank_account import BankAccount
from app.models.ledger_entry import EntryType, LedgerEntry
from app.models.transaction import Transaction, TransactionStatus, TransactionType
from app.models.user import User, UserRole
from app.models.wallet import Wallet
from tests.conftest import make_auth_headers

BANK_ACCOUNTS_URL = "/api/v1/bank-accounts"
WITHDRAWALS_URL = "/api/v1/withdrawals"


# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def tier2_user(db_session: AsyncSession) -> User:
    user = User(
        id=uuid.uuid4(),
        email="tier2user@example.com",
        hashed_password=hash_password("Pass1234!"),
        full_name="Tier Two",
        role=UserRole.USER,
        kyc_tier=2,
        is_active=True,
        is_email_verified=True,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def tier2_wallet(db_session: AsyncSession, tier2_user: User) -> Wallet:
    wallet = Wallet(
        id=uuid.uuid4(),
        user_id=tier2_user.id,
        currency="NGN",
        balance=Decimal("50000.00"),
        is_active=True,
    )
    db_session.add(wallet)
    await db_session.flush()
    await db_session.refresh(wallet)
    return wallet


@pytest_asyncio.fixture
async def tier1_user(db_session: AsyncSession) -> User:
    user = User(
        id=uuid.uuid4(),
        email="tier1user@example.com",
        hashed_password=hash_password("Pass1234!"),
        full_name="Tier One",
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
async def tier1_wallet(db_session: AsyncSession, tier1_user: User) -> Wallet:
    wallet = Wallet(
        id=uuid.uuid4(),
        user_id=tier1_user.id,
        currency="NGN",
        balance=Decimal("10000.00"),
        is_active=True,
    )
    db_session.add(wallet)
    await db_session.flush()
    await db_session.refresh(wallet)
    return wallet


@pytest_asyncio.fixture
async def bank_account(db_session: AsyncSession, tier2_user: User) -> BankAccount:
    account = BankAccount(
        id=uuid.uuid4(),
        user_id=tier2_user.id,
        account_name="Test Account",
        account_number="0123456789",
        bank_code="058",
        bank_name="GTBank",
        is_default=True,
    )
    db_session.add(account)
    await db_session.flush()
    await db_session.refresh(account)
    return account


def add_bank_account_payload(**overrides) -> dict:
    return {
        "account_name": "John Doe",
        "account_number": "0123456789",
        "bank_code": "058",
        "bank_name": "GTBank",
        **overrides,
    }


# ══════════════════════════════════════════════════════════════════════════════
# POST /bank-accounts
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_add_bank_account_unauthenticated(client: AsyncClient) -> None:
    resp = await client.post(BANK_ACCOUNTS_URL, json=add_bank_account_payload())
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_add_first_bank_account_becomes_default(
    client: AsyncClient,
    tier2_user: User,
    tier2_wallet: Wallet,
    db_session: AsyncSession,
) -> None:
    """First account added must be marked as is_default=True."""
    with patch(
        "app.services.withdrawal.BankAccountVerificationService.verify_account",
        new_callable=AsyncMock,
        return_value=None,
    ):
        resp = await client.post(
            BANK_ACCOUNTS_URL,
            json=add_bank_account_payload(),
            headers=make_auth_headers(tier2_user),
        )

    assert resp.status_code == 201
    data = resp.json()["data"]
    assert data["is_default"] is True
    assert data["account_number"] == "0123456789"
    assert data["bank_name"] == "GTBank"
    # paystack_recipient_code must NOT be in response
    assert "paystack_recipient_code" not in data


@pytest.mark.asyncio
async def test_add_second_bank_account_not_default(
    client: AsyncClient,
    tier2_user: User,
    tier2_wallet: Wallet,
    bank_account: BankAccount,  # first account already exists
    db_session: AsyncSession,
) -> None:
    with patch(
        "app.services.withdrawal.BankAccountVerificationService.verify_account",
        new_callable=AsyncMock,
        return_value=None,
    ):
        resp = await client.post(
            BANK_ACCOUNTS_URL,
            json=add_bank_account_payload(
                account_number="9876543210", bank_name="Access Bank"
            ),
            headers=make_auth_headers(tier2_user),
        )

    assert resp.status_code == 201
    assert resp.json()["data"]["is_default"] is False


@pytest.mark.asyncio
async def test_add_bank_account_paystack_verifies_name(
    client: AsyncClient,
    tier2_user: User,
    tier2_wallet: Wallet,
    db_session: AsyncSession,
) -> None:
    """If Paystack verification returns a name, it overrides the client-supplied name."""
    with patch(
        "app.services.withdrawal.BankAccountVerificationService.verify_account",
        new_callable=AsyncMock,
        return_value="JOHN DOE VERIFIED",
    ):
        resp = await client.post(
            BANK_ACCOUNTS_URL,
            json=add_bank_account_payload(account_name="Wrong Name"),
            headers=make_auth_headers(tier2_user),
        )

    assert resp.status_code == 201
    assert resp.json()["data"]["account_name"] == "JOHN DOE VERIFIED"


# ══════════════════════════════════════════════════════════════════════════════
# GET /bank-accounts
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_list_bank_accounts(
    client: AsyncClient,
    tier2_user: User,
    tier2_wallet: Wallet,
    bank_account: BankAccount,
) -> None:
    resp = await client.get(
        BANK_ACCOUNTS_URL,
        headers=make_auth_headers(tier2_user),
    )
    assert resp.status_code == 200
    items = resp.json()["data"]
    assert len(items) == 1
    assert items[0]["id"] == str(bank_account.id)


@pytest.mark.asyncio
async def test_list_bank_accounts_empty(
    client: AsyncClient,
    tier2_user: User,
    tier2_wallet: Wallet,
) -> None:
    resp = await client.get(
        BANK_ACCOUNTS_URL,
        headers=make_auth_headers(tier2_user),
    )
    assert resp.status_code == 200
    assert resp.json()["data"] == []


# ══════════════════════════════════════════════════════════════════════════════
# DELETE /bank-accounts/{id}
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_remove_own_bank_account(
    client: AsyncClient,
    tier2_user: User,
    tier2_wallet: Wallet,
    bank_account: BankAccount,
    db_session: AsyncSession,
) -> None:
    resp = await client.delete(
        f"{BANK_ACCOUNTS_URL}/{bank_account.id}",
        headers=make_auth_headers(tier2_user),
    )
    assert resp.status_code == 200

    # Soft-deleted — not visible in list
    resp2 = await client.get(
        BANK_ACCOUNTS_URL, headers=make_auth_headers(tier2_user)
    )
    assert resp2.json()["data"] == []


@pytest.mark.asyncio
async def test_remove_other_users_account_returns_404(
    client: AsyncClient,
    tier1_user: User,
    tier1_wallet: Wallet,
    bank_account: BankAccount,  # belongs to tier2_user
) -> None:
    resp = await client.delete(
        f"{BANK_ACCOUNTS_URL}/{bank_account.id}",
        headers=make_auth_headers(tier1_user),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_remove_account_with_active_withdrawal_blocked(
    client: AsyncClient,
    db_session: AsyncSession,
    tier2_user: User,
    tier2_wallet: Wallet,
    bank_account: BankAccount,
) -> None:
    """Cannot soft-delete a bank account that has an active withdrawal."""
    # Create a PENDING withdrawal referencing this account
    txn = Transaction(
        reference=f"txn_{uuid.uuid4().hex}",
        type=TransactionType.WITHDRAWAL,
        status=TransactionStatus.PENDING,
        amount=Decimal("1000.00"),
        currency="NGN",
        source_wallet_id=tier2_wallet.id,
        initiated_by_user_id=tier2_user.id,
        extra_data={"bank_account_id": str(bank_account.id)},
    )
    db_session.add(txn)
    await db_session.flush()

    resp = await client.delete(
        f"{BANK_ACCOUNTS_URL}/{bank_account.id}",
        headers=make_auth_headers(tier2_user),
    )
    assert resp.status_code == 403


# ══════════════════════════════════════════════════════════════════════════════
# POST /withdrawals
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_withdrawal_unauthenticated(
    client: AsyncClient, bank_account: BankAccount
) -> None:
    resp = await client.post(
        WITHDRAWALS_URL,
        json={"bank_account_id": str(bank_account.id), "amount": "1000.00"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_withdrawal_kyc_tier1_blocked(
    client: AsyncClient,
    tier1_user: User,
    tier1_wallet: Wallet,
    bank_account: BankAccount,
) -> None:
    """Tier 1 users cannot initiate withdrawals — requires Tier 2."""
    resp = await client.post(
        WITHDRAWALS_URL,
        json={"bank_account_id": str(bank_account.id), "amount": "1000.00"},
        headers=make_auth_headers(tier1_user),
    )
    assert resp.status_code == 403
    assert resp.json()["error"] == "KYC_TIER_INSUFFICIENT"


@pytest.mark.asyncio
async def test_withdrawal_bank_account_not_found(
    client: AsyncClient,
    tier2_user: User,
    tier2_wallet: Wallet,
) -> None:
    resp = await client.post(
        WITHDRAWALS_URL,
        json={"bank_account_id": str(uuid.uuid4()), "amount": "1000.00"},
        headers=make_auth_headers(tier2_user),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_withdrawal_insufficient_balance(
    client: AsyncClient,
    db_session: AsyncSession,
    tier2_user: User,
    tier2_wallet: Wallet,
    bank_account: BankAccount,
) -> None:
    tier2_wallet.balance = Decimal("100.00")
    await db_session.flush()

    with patch("app.workers.withdrawal_tasks.process_withdrawal"):
        resp = await client.post(
            WITHDRAWALS_URL,
            json={"bank_account_id": str(bank_account.id), "amount": "500.00"},
            headers=make_auth_headers(tier2_user),
        )

    assert resp.status_code == 422
    assert resp.json()["error"] == "INSUFFICIENT_BALANCE"

    # Balance unchanged
    await db_session.refresh(tier2_wallet)
    assert tier2_wallet.balance == Decimal("100.00")


@pytest.mark.asyncio
async def test_withdrawal_success(
    client: AsyncClient,
    db_session: AsyncSession,
    tier2_user: User,
    tier2_wallet: Wallet,
    bank_account: BankAccount,
) -> None:
    """Successful initiation holds balance and creates a PENDING transaction."""
    initial_balance = tier2_wallet.balance
    amount = Decimal("5000.00")

    with patch("app.workers.withdrawal_tasks.process_withdrawal") as mock_task:
        resp = await client.post(
            WITHDRAWALS_URL,
            json={"bank_account_id": str(bank_account.id), "amount": str(amount)},
            headers=make_auth_headers(tier2_user),
        )

    assert resp.status_code == 201
    body = resp.json()
    assert body["success"] is True
    data = body["data"]
    assert data["type"] == "withdrawal"
    assert data["status"] == "pending"
    assert Decimal(data["amount"]) == amount
    assert data["source_wallet_id"] == str(tier2_wallet.id)
    assert data["extra_data"]["bank_account_id"] == str(bank_account.id)

    # Balance held immediately
    await db_session.refresh(tier2_wallet)
    assert tier2_wallet.balance == initial_balance - amount

    # Celery task enqueued
    mock_task.delay.assert_called_once_with(data["id"])


@pytest.mark.asyncio
async def test_withdrawal_duplicate_pending_blocked(
    client: AsyncClient,
    db_session: AsyncSession,
    tier2_user: User,
    tier2_wallet: Wallet,
    bank_account: BankAccount,
) -> None:
    """A second withdrawal is rejected while the first is PENDING."""
    # Manually create a PENDING withdrawal
    txn = Transaction(
        reference=f"txn_{uuid.uuid4().hex}",
        type=TransactionType.WITHDRAWAL,
        status=TransactionStatus.PENDING,
        amount=Decimal("1000.00"),
        currency="NGN",
        source_wallet_id=tier2_wallet.id,
        initiated_by_user_id=tier2_user.id,
        extra_data={"bank_account_id": str(bank_account.id)},
    )
    db_session.add(txn)
    await db_session.flush()

    resp = await client.post(
        WITHDRAWALS_URL,
        json={"bank_account_id": str(bank_account.id), "amount": "1000.00"},
        headers=make_auth_headers(tier2_user),
    )
    assert resp.status_code == 422
    assert resp.json()["error"] == "WITHDRAWAL_ALREADY_PENDING"


# ══════════════════════════════════════════════════════════════════════════════
# GET /withdrawals/{reference}
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_get_withdrawal_status(
    client: AsyncClient,
    db_session: AsyncSession,
    tier2_user: User,
    tier2_wallet: Wallet,
    bank_account: BankAccount,
) -> None:
    with patch("app.workers.withdrawal_tasks.process_withdrawal"):
        resp = await client.post(
            WITHDRAWALS_URL,
            json={"bank_account_id": str(bank_account.id), "amount": "3000.00"},
            headers=make_auth_headers(tier2_user),
        )
    assert resp.status_code == 201
    reference = resp.json()["data"]["reference"]

    status_resp = await client.get(
        f"{WITHDRAWALS_URL}/{reference}",
        headers=make_auth_headers(tier2_user),
    )
    assert status_resp.status_code == 200
    assert status_resp.json()["data"]["reference"] == reference


@pytest.mark.asyncio
async def test_get_withdrawal_status_wrong_user_returns_404(
    client: AsyncClient,
    db_session: AsyncSession,
    tier2_user: User,
    tier2_wallet: Wallet,
    tier1_user: User,
    tier1_wallet: Wallet,
    bank_account: BankAccount,
) -> None:
    with patch("app.workers.withdrawal_tasks.process_withdrawal"):
        resp = await client.post(
            WITHDRAWALS_URL,
            json={"bank_account_id": str(bank_account.id), "amount": "3000.00"},
            headers=make_auth_headers(tier2_user),
        )
    reference = resp.json()["data"]["reference"]

    # tier1_user tries to read tier2_user's withdrawal
    status_resp = await client.get(
        f"{WITHDRAWALS_URL}/{reference}",
        headers=make_auth_headers(tier1_user),
    )
    assert status_resp.status_code == 404


# ══════════════════════════════════════════════════════════════════════════════
# WithdrawalService integration — process_payout_success / failure
# ══════════════════════════════════════════════════════════════════════════════


@pytest_asyncio.fixture
async def processing_withdrawal(
    db_session: AsyncSession,
    tier2_user: User,
    tier2_wallet: Wallet,
    bank_account: BankAccount,
) -> Transaction:
    """A PROCESSING WITHDRAWAL with the wallet balance already held."""
    # Simulate balance hold
    tier2_wallet.balance -= Decimal("8000.00")
    txn = Transaction(
        reference=f"txn_{uuid.uuid4().hex}",
        type=TransactionType.WITHDRAWAL,
        status=TransactionStatus.PROCESSING,
        amount=Decimal("8000.00"),
        currency="NGN",
        source_wallet_id=tier2_wallet.id,
        initiated_by_user_id=tier2_user.id,
        provider_reference="TRF_test_001",
        extra_data={"bank_account_id": str(bank_account.id)},
    )
    db_session.add(txn)
    await db_session.flush()
    await db_session.refresh(txn)
    await db_session.refresh(tier2_wallet)
    return txn


@pytest.mark.asyncio
async def test_process_payout_success_completes_transaction(
    db_session: AsyncSession,
    tier2_wallet: Wallet,
    processing_withdrawal: Transaction,
) -> None:
    """transfer.success → COMPLETED, DEBIT ledger entry written, balance unchanged."""
    from app.services.withdrawal import WithdrawalService

    balance_before = tier2_wallet.balance  # already held (reduced at initiation)
    service = WithdrawalService(db_session)
    await service.process_payout_success(
        reference=processing_withdrawal.reference,
        transfer_code="TRF_test_001",
    )

    # Transaction COMPLETED
    await db_session.refresh(processing_withdrawal)
    assert processing_withdrawal.status == TransactionStatus.COMPLETED

    # Wallet balance unchanged (it was held at initiation, not again now)
    await db_session.refresh(tier2_wallet)
    assert tier2_wallet.balance == balance_before

    # DEBIT ledger entry written
    entries = (
        await db_session.execute(
            select(LedgerEntry).where(
                LedgerEntry.transaction_id == processing_withdrawal.id
            )
        )
    ).scalars().all()
    assert len(entries) == 1
    entry = entries[0]
    assert entry.entry_type == EntryType.DEBIT
    assert entry.wallet_id == tier2_wallet.id
    assert entry.amount == Decimal("8000.00")


@pytest.mark.asyncio
async def test_process_payout_success_idempotent(
    db_session: AsyncSession,
    tier2_wallet: Wallet,
    processing_withdrawal: Transaction,
) -> None:
    """Duplicate transfer.success call does not create duplicate ledger entries."""
    from app.services.withdrawal import WithdrawalService

    service = WithdrawalService(db_session)
    await service.process_payout_success(
        reference=processing_withdrawal.reference,
        transfer_code="TRF_test_001",
    )
    # Second call (idempotent)
    await service.process_payout_success(
        reference=processing_withdrawal.reference,
        transfer_code="TRF_test_001",
    )

    entries = (
        await db_session.execute(
            select(LedgerEntry).where(
                LedgerEntry.transaction_id == processing_withdrawal.id
            )
        )
    ).scalars().all()
    assert len(entries) == 1  # only one entry despite two calls


@pytest.mark.asyncio
async def test_process_payout_failure_returns_balance(
    db_session: AsyncSession,
    tier2_wallet: Wallet,
    processing_withdrawal: Transaction,
) -> None:
    """transfer.failed → FAILED, held balance returned to wallet."""
    from app.services.withdrawal import WithdrawalService

    balance_before = tier2_wallet.balance  # already held (reduced)
    service = WithdrawalService(db_session)
    await service.process_payout_failure(
        reference=processing_withdrawal.reference,
        transfer_code="TRF_test_001",
    )

    # Transaction FAILED
    await db_session.refresh(processing_withdrawal)
    assert processing_withdrawal.status == TransactionStatus.FAILED
    assert processing_withdrawal.failure_reason is not None

    # Balance restored
    await db_session.refresh(tier2_wallet)
    assert tier2_wallet.balance == balance_before + Decimal("8000.00")


@pytest.mark.asyncio
async def test_process_payout_failure_idempotent(
    db_session: AsyncSession,
    tier2_wallet: Wallet,
    processing_withdrawal: Transaction,
) -> None:
    """Duplicate transfer.failed call does not double-credit the wallet."""
    from app.services.withdrawal import WithdrawalService

    service = WithdrawalService(db_session)
    await service.process_payout_failure(
        reference=processing_withdrawal.reference,
        transfer_code="TRF_test_001",
    )
    balance_after_first = tier2_wallet.balance

    await db_session.refresh(tier2_wallet)
    balance_after_first = tier2_wallet.balance

    # Second call (idempotent)
    await service.process_payout_failure(
        reference=processing_withdrawal.reference,
        transfer_code="TRF_test_001",
    )

    await db_session.refresh(tier2_wallet)
    assert tier2_wallet.balance == balance_after_first  # unchanged
