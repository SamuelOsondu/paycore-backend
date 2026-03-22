import uuid
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.wallet import Wallet
from app.repositories.base import BaseRepository


class WalletRepository(BaseRepository[Wallet]):

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def create(
        self, *, user_id: uuid.UUID, currency: str = "NGN"
    ) -> Wallet:
        wallet = Wallet(user_id=user_id, currency=currency)
        self.session.add(wallet)
        await self.session.flush()
        await self.session.refresh(wallet)
        return wallet

    async def get_by_id(self, wallet_id: uuid.UUID) -> Optional[Wallet]:
        result = await self.session.execute(
            select(Wallet).where(
                Wallet.id == wallet_id,
                Wallet.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def get_by_user_id(self, user_id: uuid.UUID) -> Optional[Wallet]:
        result = await self.session.execute(
            select(Wallet).where(
                Wallet.user_id == user_id,
                Wallet.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def lock_for_update(self, wallet_id: uuid.UUID) -> Optional[Wallet]:
        """
        Fetch the wallet with a row-level exclusive lock (SELECT ... FOR UPDATE).

        Must be called inside an active transaction.  All balance-mutating
        operations (transfers, payments, withdrawals) must acquire this lock
        before reading and writing the balance to prevent lost-update races.
        """
        result = await self.session.execute(
            select(Wallet)
            .where(
                Wallet.id == wallet_id,
                Wallet.deleted_at.is_(None),
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def update_balance(self, wallet: Wallet, new_balance: Decimal) -> Wallet:
        """
        Persist a new balance value.

        Callers must:
        1. Hold the row lock obtained from lock_for_update().
        2. Write corresponding double-entry ledger entries in the same transaction.

        Never call this method directly from a router or without ledger entries.
        """
        wallet.balance = new_balance
        await self.session.flush()
        await self.session.refresh(wallet)
        return wallet

    async def set_active(self, wallet: Wallet, *, active: bool) -> Wallet:
        wallet.is_active = active
        await self.session.flush()
        await self.session.refresh(wallet)
        return wallet

    async def soft_delete(self, wallet: Wallet) -> None:
        wallet.soft_delete()
        await self.session.flush()
