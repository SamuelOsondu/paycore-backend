"""
Unit tests for FraudService fraud rules.

Synchronous checks
------------------
check_transfer:
- KYC Tier 0 always raises KYCTierError
- Amount above Tier 1 single limit raises KYCTierError
- Amount at exactly Tier 1 single limit passes
- Daily volume exceeded raises DailyLimitError
- Daily limit only counts COMPLETED transactions (not pending/failed)
- Daily limit only counts transactions from today (not yesterday)
- Duplicate transfer within window raises DuplicateTransferError
- Same params outside window passes

check_withdrawal:
- KYC Tier 0 raises KYCTierError (tier requirement)
- KYC Tier 1 raises KYCTierError (tier requirement)
- KYC Tier 2 passes (no other violation)

check_merchant_payment:
- KYC Tier 0 raises KYCTierError
- Tier 1 within single limit passes

Async flags
-----------
- maybe_flag_merchant_payment enqueues task for Tier 1 above threshold
- maybe_flag_merchant_payment does NOT enqueue for Tier 2 above threshold
- maybe_flag_rapid_transfers enqueues when distinct recipient count exceeds limit
- maybe_flag_rapid_transfers does NOT enqueue when count is at/below limit
"""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import DailyLimitError, DuplicateTransferError, KYCTierError
from app.core.security import hash_password
from app.models.transaction import Transaction, TransactionStatus, TransactionType
from app.models.user import User, UserRole
from app.models.wallet import Wallet
from app.services.fraud import FraudService

pytestmark = pytest.mark.asyncio


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _create_user(db: AsyncSession, kyc_tier: int) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"fraud_{uuid.uuid4().hex[:8]}@test.com",
        hashed_password=hash_password("Test1234!"),
        full_name="Fraud Test",
        role=UserRole.USER,
        kyc_tier=kyc_tier,
        is_active=True,
        is_email_verified=True,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user


async def _create_wallet(
    db: AsyncSession,
    user: User,
    balance: Decimal = Decimal("100000.00"),
) -> Wallet:
    wallet = Wallet(
        id=uuid.uuid4(),
        user_id=user.id,
        currency="NGN",
        balance=balance,
        is_active=True,
    )
    db.add(wallet)
    await db.flush()
    await db.refresh(wallet)
    return wallet


async def _create_txn(
    db: AsyncSession,
    user: User,
    *,
    amount: Decimal,
    txn_type: TransactionType = TransactionType.TRANSFER,
    status: TransactionStatus = TransactionStatus.COMPLETED,
    source_wallet_id: uuid.UUID | None = None,
    destination_wallet_id: uuid.UUID | None = None,
    created_at: datetime | None = None,
) -> Transaction:
    txn = Transaction(
        reference=f"txn_{uuid.uuid4()}",
        type=txn_type,
        status=status,
        amount=amount,
        currency="NGN",
        initiated_by_user_id=user.id,
        source_wallet_id=source_wallet_id,
        destination_wallet_id=destination_wallet_id,
    )
    if created_at is not None:
        txn.created_at = created_at
    db.add(txn)
    await db.flush()
    await db.refresh(txn)
    return txn


# ── check_transfer — KYC tier single limit ────────────────────────────────────


async def test_check_transfer_tier0_raises_kyc_error(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session, kyc_tier=0)
    wallet = await _create_wallet(db_session, user)
    recipient_id = uuid.uuid4()

    service = FraudService(db_session)
    with pytest.raises(KYCTierError):
        await service.check_transfer(user, wallet, recipient_id, Decimal("100.00"))


async def test_check_transfer_tier1_above_single_limit_raises(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session, kyc_tier=1)
    wallet = await _create_wallet(db_session, user)
    recipient_id = uuid.uuid4()

    service = FraudService(db_session)
    with pytest.raises(KYCTierError):
        # 50,001 exceeds the 50,000 NGN Tier 1 single-transaction limit
        await service.check_transfer(user, wallet, recipient_id, Decimal("50001.00"))


async def test_check_transfer_tier1_at_single_limit_passes(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session, kyc_tier=1)
    wallet = await _create_wallet(db_session, user)
    recipient_id = uuid.uuid4()

    service = FraudService(db_session)
    # Exactly at the limit — must not raise
    await service.check_transfer(user, wallet, recipient_id, Decimal("50000.00"))


async def test_check_transfer_tier2_above_tier1_limit_passes(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session, kyc_tier=2)
    wallet = await _create_wallet(db_session, user)
    recipient_id = uuid.uuid4()

    service = FraudService(db_session)
    # Tier 2 limit is 500,000 — 100,000 should pass
    await service.check_transfer(user, wallet, recipient_id, Decimal("100000.00"))


# ── check_transfer — daily volume limit ───────────────────────────────────────


async def test_check_transfer_daily_limit_exceeded_raises(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session, kyc_tier=1)
    wallet = await _create_wallet(db_session, user)
    recipient = await _create_wallet(db_session, await _create_user(db_session, kyc_tier=1))

    # 45,000 already sent today
    await _create_txn(
        db_session,
        user,
        amount=Decimal("45000.00"),
        txn_type=TransactionType.TRANSFER,
        status=TransactionStatus.COMPLETED,
        source_wallet_id=wallet.id,
        destination_wallet_id=recipient.id,
    )

    service = FraudService(db_session)
    with pytest.raises(DailyLimitError):
        # 45,000 + 10,000 = 55,000 > 50,000 daily limit
        await service.check_transfer(user, wallet, recipient.id, Decimal("10000.00"))


async def test_check_transfer_daily_limit_counts_only_completed(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session, kyc_tier=1)
    wallet = await _create_wallet(db_session, user)
    recipient = await _create_wallet(db_session, await _create_user(db_session, kyc_tier=1))

    # 45,000 in a PENDING transaction — must NOT count toward daily limit
    await _create_txn(
        db_session,
        user,
        amount=Decimal("45000.00"),
        txn_type=TransactionType.TRANSFER,
        status=TransactionStatus.PENDING,
        source_wallet_id=wallet.id,
    )

    service = FraudService(db_session)
    # Only 0 completed — 10,000 is well within the 50,000 daily limit
    await service.check_transfer(user, wallet, recipient.id, Decimal("10000.00"))


async def test_check_transfer_daily_limit_excludes_yesterday(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session, kyc_tier=1)
    wallet = await _create_wallet(db_session, user)
    recipient = await _create_wallet(db_session, await _create_user(db_session, kyc_tier=1))

    # 49,000 sent yesterday — must NOT count toward today's limit
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    await _create_txn(
        db_session,
        user,
        amount=Decimal("49000.00"),
        txn_type=TransactionType.TRANSFER,
        status=TransactionStatus.COMPLETED,
        source_wallet_id=wallet.id,
        created_at=yesterday,
    )

    service = FraudService(db_session)
    # Yesterday's volume should not count — 10,000 today is within limit
    await service.check_transfer(user, wallet, recipient.id, Decimal("10000.00"))


# ── check_transfer — duplicate detection ─────────────────────────────────────


async def test_check_transfer_duplicate_in_window_raises(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session, kyc_tier=1)
    wallet = await _create_wallet(db_session, user)
    recipient = await _create_wallet(db_session, await _create_user(db_session, kyc_tier=1))
    amount = Decimal("1000.00")

    # Create an identical transfer 30 seconds ago (within 60s window)
    recent = datetime.now(timezone.utc) - timedelta(seconds=30)
    await _create_txn(
        db_session,
        user,
        amount=amount,
        txn_type=TransactionType.TRANSFER,
        status=TransactionStatus.COMPLETED,
        source_wallet_id=wallet.id,
        destination_wallet_id=recipient.id,
        created_at=recent,
    )

    service = FraudService(db_session)
    with pytest.raises(DuplicateTransferError):
        await service.check_transfer(user, wallet, recipient.id, amount)


async def test_check_transfer_outside_duplicate_window_passes(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session, kyc_tier=1)
    wallet = await _create_wallet(db_session, user)
    recipient = await _create_wallet(db_session, await _create_user(db_session, kyc_tier=1))
    amount = Decimal("1000.00")

    # Create an identical transfer 2 minutes ago (outside 60s window)
    old = datetime.now(timezone.utc) - timedelta(seconds=120)
    await _create_txn(
        db_session,
        user,
        amount=amount,
        txn_type=TransactionType.TRANSFER,
        status=TransactionStatus.COMPLETED,
        source_wallet_id=wallet.id,
        destination_wallet_id=recipient.id,
        created_at=old,
    )

    service = FraudService(db_session)
    # Outside the window — should not raise DuplicateTransferError
    await service.check_transfer(user, wallet, recipient.id, amount)


# ── check_withdrawal ──────────────────────────────────────────────────────────


async def test_check_withdrawal_tier0_raises_kyc_error(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session, kyc_tier=0)
    wallet = await _create_wallet(db_session, user)
    service = FraudService(db_session)

    with pytest.raises(KYCTierError):
        await service.check_withdrawal(user, wallet, Decimal("1000.00"))


async def test_check_withdrawal_tier1_raises_kyc_error(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session, kyc_tier=1)
    wallet = await _create_wallet(db_session, user)
    service = FraudService(db_session)

    with pytest.raises(KYCTierError):
        await service.check_withdrawal(user, wallet, Decimal("1000.00"))


async def test_check_withdrawal_tier2_passes(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session, kyc_tier=2)
    wallet = await _create_wallet(db_session, user)
    service = FraudService(db_session)

    # Should not raise for a reasonable amount within tier 2 limits
    await service.check_withdrawal(user, wallet, Decimal("10000.00"))


async def test_check_withdrawal_tier2_above_limit_raises(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session, kyc_tier=2)
    wallet = await _create_wallet(db_session, user)
    service = FraudService(db_session)

    with pytest.raises(KYCTierError):
        # 500,001 exceeds Tier 2 single-transaction limit of 500,000
        await service.check_withdrawal(user, wallet, Decimal("500001.00"))


# ── check_merchant_payment ────────────────────────────────────────────────────


async def test_check_merchant_payment_tier0_raises(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session, kyc_tier=0)
    wallet = await _create_wallet(db_session, user)
    service = FraudService(db_session)

    with pytest.raises(KYCTierError):
        await service.check_merchant_payment(user, wallet, uuid.uuid4(), Decimal("500.00"))


async def test_check_merchant_payment_tier1_within_limit_passes(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session, kyc_tier=1)
    wallet = await _create_wallet(db_session, user)
    service = FraudService(db_session)

    await service.check_merchant_payment(user, wallet, uuid.uuid4(), Decimal("5000.00"))


async def test_check_merchant_payment_tier1_above_single_limit_raises(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session, kyc_tier=1)
    wallet = await _create_wallet(db_session, user)
    service = FraudService(db_session)

    with pytest.raises(KYCTierError):
        await service.check_merchant_payment(user, wallet, uuid.uuid4(), Decimal("60000.00"))


# ── maybe_flag_merchant_payment ───────────────────────────────────────────────


async def test_maybe_flag_merchant_payment_enqueues_for_tier1_above_threshold(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session, kyc_tier=1)
    txn_id = uuid.uuid4()

    with patch("app.workers.fraud_tasks.flag_transaction_risk") as mock_task:
        mock_task.delay = MagicMock()
        service = FraudService(db_session)
        # 100,001 > 100,000 threshold for Tier 1
        service.maybe_flag_merchant_payment(txn_id, user, Decimal("100001.00"))
        mock_task.delay.assert_called_once_with(
            str(txn_id),
            f"Large merchant payment from Tier 1 user: 100001.00 NGN.",
        )


async def test_maybe_flag_merchant_payment_no_flag_for_tier2(
    db_session: AsyncSession,
) -> None:
    """Tier 2 users are not flagged for large merchant payments."""
    user = await _create_user(db_session, kyc_tier=2)
    txn_id = uuid.uuid4()

    with patch("app.workers.fraud_tasks.flag_transaction_risk") as mock_task:
        mock_task.delay = MagicMock()
        service = FraudService(db_session)
        service.maybe_flag_merchant_payment(txn_id, user, Decimal("200000.00"))
        mock_task.delay.assert_not_called()


async def test_maybe_flag_merchant_payment_no_flag_at_threshold(
    db_session: AsyncSession,
) -> None:
    """Exactly at threshold (not above) — must not flag."""
    user = await _create_user(db_session, kyc_tier=1)
    txn_id = uuid.uuid4()

    with patch("app.workers.fraud_tasks.flag_transaction_risk") as mock_task:
        mock_task.delay = MagicMock()
        service = FraudService(db_session)
        service.maybe_flag_merchant_payment(txn_id, user, Decimal("100000.00"))
        mock_task.delay.assert_not_called()


# ── maybe_flag_rapid_transfers ────────────────────────────────────────────────


async def test_maybe_flag_rapid_transfers_enqueues_when_exceeds_limit(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session, kyc_tier=2)
    sender_wallet = await _create_wallet(db_session, user)
    recent = datetime.now(timezone.utc) - timedelta(seconds=30)

    # Create 6 transfers to 6 distinct recipients (> 5 threshold)
    for _ in range(6):
        recipient_user = await _create_user(db_session, kyc_tier=1)
        recipient_wallet = await _create_wallet(db_session, recipient_user)
        await _create_txn(
            db_session,
            user,
            amount=Decimal("500.00"),
            txn_type=TransactionType.TRANSFER,
            source_wallet_id=sender_wallet.id,
            destination_wallet_id=recipient_wallet.id,
            created_at=recent,
        )

    txn_id = uuid.uuid4()
    with patch("app.workers.fraud_tasks.flag_transaction_risk") as mock_task:
        mock_task.delay = MagicMock()
        service = FraudService(db_session)
        await service.maybe_flag_rapid_transfers(txn_id, sender_wallet.id)
        mock_task.delay.assert_called_once()


async def test_maybe_flag_rapid_transfers_no_flag_at_limit(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session, kyc_tier=2)
    sender_wallet = await _create_wallet(db_session, user)
    recent = datetime.now(timezone.utc) - timedelta(seconds=30)

    # Create exactly 5 transfers to 5 distinct recipients (== threshold, not above)
    for _ in range(5):
        recipient_user = await _create_user(db_session, kyc_tier=1)
        recipient_wallet = await _create_wallet(db_session, recipient_user)
        await _create_txn(
            db_session,
            user,
            amount=Decimal("500.00"),
            txn_type=TransactionType.TRANSFER,
            source_wallet_id=sender_wallet.id,
            destination_wallet_id=recipient_wallet.id,
            created_at=recent,
        )

    txn_id = uuid.uuid4()
    with patch("app.workers.fraud_tasks.flag_transaction_risk") as mock_task:
        mock_task.delay = MagicMock()
        service = FraudService(db_session)
        await service.maybe_flag_rapid_transfers(txn_id, sender_wallet.id)
        mock_task.delay.assert_not_called()
