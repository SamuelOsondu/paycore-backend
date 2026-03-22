import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import DailyLimitError, DuplicateTransferError, KYCTierError
from app.models.transaction import TransactionType
from app.models.user import User
from app.models.wallet import Wallet
from app.repositories.fraud import FraudRepository


class FraudService:
    """
    Synchronous fraud rule enforcement and async risk flagging.

    Sync check methods (check_transfer, check_merchant_payment, check_withdrawal)
    raise typed exceptions to block the operation — call them before any DB writes.

    Async flag methods (maybe_flag_merchant_payment, maybe_flag_rapid_transfers)
    enqueue Celery tasks post-commit and never block the transaction outcome.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._repo = FraudRepository(session)

    # ── Public synchronous checks ─────────────────────────────────────────────

    async def check_transfer(
        self,
        sender_user: User,
        sender_wallet: Wallet,
        recipient_wallet_id: uuid.UUID,
        amount: Decimal,
    ) -> None:
        """
        Block a transfer if any synchronous fraud rule is violated.

        Raises
        ------
        KYCTierError        – tier 0, or amount exceeds single-transaction limit
        DailyLimitError     – today's outgoing volume would exceed the tier cap
        DuplicateTransferError – identical transfer seen within the duplicate window
        """
        self._enforce_kyc_single_limit(sender_user.kyc_tier, amount)
        await self._enforce_daily_limit(
            sender_user.id, sender_user.kyc_tier, amount, TransactionType.TRANSFER
        )
        await self._enforce_no_duplicate_transfer(
            sender_wallet.id, recipient_wallet_id, amount
        )

    async def check_merchant_payment(
        self,
        user: User,
        wallet: Wallet,
        merchant_wallet_id: uuid.UUID,
        amount: Decimal,
    ) -> None:
        """Block a merchant payment if any synchronous fraud rule is violated."""
        self._enforce_kyc_single_limit(user.kyc_tier, amount)
        await self._enforce_daily_limit(
            user.id, user.kyc_tier, amount, TransactionType.MERCHANT_PAYMENT
        )

    async def check_withdrawal(
        self,
        user: User,
        wallet: Wallet,
        amount: Decimal,
    ) -> None:
        """
        Block a withdrawal if any synchronous fraud rule is violated.
        Withdrawals require KYC Tier 2 minimum — Tier 0 and Tier 1 are always rejected.
        """
        if user.kyc_tier < 2:
            raise KYCTierError(
                "Withdrawals require KYC Tier 2 verification. "
                "Please complete identity verification to unlock withdrawals."
            )
        self._enforce_kyc_single_limit(user.kyc_tier, amount)
        await self._enforce_daily_limit(
            user.id, user.kyc_tier, amount, TransactionType.WITHDRAWAL
        )

    # ── Post-commit async flags ───────────────────────────────────────────────

    def maybe_flag_merchant_payment(
        self,
        transaction_id: uuid.UUID,
        user: User,
        amount: Decimal,
    ) -> None:
        """
        Enqueue a risk flag if a Tier 1 user makes a merchant payment above the
        flag threshold.  Does not block; always call after a successful commit.
        """
        threshold = Decimal(str(settings.FRAUD_MERCHANT_PAYMENT_FLAG_THRESHOLD))
        if user.kyc_tier == 1 and amount > threshold:
            from app.workers.fraud_tasks import flag_transaction_risk

            flag_transaction_risk.delay(
                str(transaction_id),
                f"Large merchant payment from Tier 1 user: {amount} NGN.",
            )

    async def maybe_flag_rapid_transfers(
        self,
        transaction_id: uuid.UUID,
        sender_wallet_id: uuid.UUID,
    ) -> None:
        """
        Enqueue a risk flag if the sender has transferred to more than
        FRAUD_RAPID_TRANSFER_COUNT distinct recipients in the last
        FRAUD_RAPID_TRANSFER_WINDOW_SECONDS seconds.
        Does not block; always call after a successful commit.
        """
        window_start = datetime.now(timezone.utc) - timedelta(
            seconds=settings.FRAUD_RAPID_TRANSFER_WINDOW_SECONDS
        )
        distinct_count = await self._repo.count_distinct_recipients_recently(
            sender_wallet_id, since=window_start
        )
        if distinct_count > settings.FRAUD_RAPID_TRANSFER_COUNT:
            from app.workers.fraud_tasks import flag_transaction_risk

            flag_transaction_risk.delay(
                str(transaction_id),
                f"Rapid transfers to {distinct_count} distinct recipients "
                f"within {settings.FRAUD_RAPID_TRANSFER_WINDOW_SECONDS // 60} minutes.",
            )

    # ── Private enforcement helpers ───────────────────────────────────────────

    def _enforce_kyc_single_limit(self, kyc_tier: int, amount: Decimal) -> None:
        if kyc_tier == 0:
            raise KYCTierError(
                "KYC verification is required to initiate transactions. "
                "Please complete Tier 1 verification."
            )
        limit = Decimal(
            str(
                settings.KYC_TIER1_SINGLE_LIMIT
                if kyc_tier == 1
                else settings.KYC_TIER2_SINGLE_LIMIT
            )
        )
        if amount > limit:
            raise KYCTierError(
                f"Amount exceeds the single-transaction limit of "
                f"{limit:,.0f} NGN for KYC Tier {kyc_tier}."
            )

    async def _enforce_daily_limit(
        self,
        user_id: uuid.UUID,
        kyc_tier: int,
        amount: Decimal,
        txn_type: TransactionType,
    ) -> None:
        daily_limit = Decimal(
            str(
                settings.KYC_TIER1_DAILY_LIMIT
                if kyc_tier == 1
                else settings.KYC_TIER2_DAILY_LIMIT
            )
        )
        today_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        daily_sum = await self._repo.get_daily_outgoing_sum(
            user_id, txn_type, day_start=today_start
        )
        if daily_sum + amount > daily_limit:
            raise DailyLimitError()

    async def _enforce_no_duplicate_transfer(
        self,
        source_wallet_id: uuid.UUID,
        destination_wallet_id: uuid.UUID,
        amount: Decimal,
    ) -> None:
        window_start = datetime.now(timezone.utc) - timedelta(
            seconds=settings.FRAUD_DUPLICATE_WINDOW_SECONDS
        )
        count = await self._repo.count_recent_transfers(
            source_wallet_id, destination_wallet_id, amount, since=window_start
        )
        if count > 0:
            raise DuplicateTransferError()
