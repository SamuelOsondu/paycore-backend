import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.transaction import Transaction, TransactionStatus, TransactionType


class FraudRepository:
    """
    Fraud-specific analytical queries on the transactions table.
    No business logic — returns raw counts and sums for FraudService to evaluate.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_daily_outgoing_sum(
        self,
        user_id: uuid.UUID,
        txn_type: TransactionType,
        day_start: datetime,
    ) -> Decimal:
        """
        Sum of completed outgoing transactions of the given type since day_start.
        Returns Decimal("0") when there are no matching rows.
        """
        result = await self.session.execute(
            select(func.coalesce(func.sum(Transaction.amount), 0))
            .where(
                Transaction.initiated_by_user_id == user_id,
                Transaction.type == txn_type,
                Transaction.status == TransactionStatus.COMPLETED,
                Transaction.created_at >= day_start,
            )
        )
        return Decimal(str(result.scalar_one()))

    async def count_recent_transfers(
        self,
        source_wallet_id: uuid.UUID,
        destination_wallet_id: uuid.UUID,
        amount: Decimal,
        since: datetime,
    ) -> int:
        """
        Count TRANSFER transactions from source to destination at exactly the given
        amount within the time window.  Used for duplicate-transfer detection.
        """
        result = await self.session.execute(
            select(func.count())
            .select_from(Transaction)
            .where(
                Transaction.source_wallet_id == source_wallet_id,
                Transaction.destination_wallet_id == destination_wallet_id,
                Transaction.amount == amount,
                Transaction.type == TransactionType.TRANSFER,
                Transaction.created_at >= since,
            )
        )
        return result.scalar_one()

    async def count_distinct_recipients_recently(
        self,
        source_wallet_id: uuid.UUID,
        since: datetime,
    ) -> int:
        """
        Count distinct destination wallets for TRANSFER transactions from the given
        source wallet within the time window.  Used for rapid-transfer anomaly flagging.
        """
        result = await self.session.execute(
            select(func.count(func.distinct(Transaction.destination_wallet_id)))
            .where(
                Transaction.source_wallet_id == source_wallet_id,
                Transaction.type == TransactionType.TRANSFER,
                Transaction.created_at >= since,
            )
        )
        return result.scalar_one()
