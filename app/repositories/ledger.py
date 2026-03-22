import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ledger_entry import EntryType, LedgerEntry
from app.repositories.base import BaseRepository


class LedgerRepository(BaseRepository[LedgerEntry]):
    """
    Read/write access to ledger_entries.

    No update or delete methods exist — ledger entries are immutable once
    written.
    """

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def create_entry(
        self,
        *,
        transaction_id: uuid.UUID,
        wallet_id: uuid.UUID,
        entry_type: EntryType,
        amount: Decimal,
        currency: str,
        balance_after: Decimal,
    ) -> LedgerEntry:
        """
        Persist a single ledger entry and return the flushed instance.

        Caller is responsible for calling session.commit() (or letting the
        outer transaction scope do so).
        """
        entry = LedgerEntry(
            transaction_id=transaction_id,
            wallet_id=wallet_id,
            entry_type=entry_type,
            amount=amount,
            currency=currency,
            balance_after=balance_after,
        )
        self.session.add(entry)
        await self.session.flush()
        await self.session.refresh(entry)
        return entry

    async def get_by_transaction(
        self, transaction_id: uuid.UUID
    ) -> list[LedgerEntry]:
        """Return both entries for a transaction, ordered by created_at ASC."""
        result = await self.session.execute(
            select(LedgerEntry)
            .where(LedgerEntry.transaction_id == transaction_id)
            .order_by(LedgerEntry.created_at)
        )
        return list(result.scalars().all())

    async def get_by_wallet(
        self, wallet_id: uuid.UUID, *, limit: int, offset: int
    ) -> tuple[list[LedgerEntry], int]:
        """
        Paginated ledger history for a single wallet, ordered by
        created_at DESC.  Returns (entries, total_count).
        """
        total: int = (
            await self.session.execute(
                select(func.count())
                .select_from(LedgerEntry)
                .where(LedgerEntry.wallet_id == wallet_id)
            )
        ).scalar_one()

        rows = (
            await self.session.execute(
                select(LedgerEntry)
                .where(LedgerEntry.wallet_id == wallet_id)
                .order_by(LedgerEntry.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        ).scalars().all()

        return list(rows), total
