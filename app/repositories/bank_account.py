import uuid
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bank_account import BankAccount
from app.repositories.base import BaseRepository


class BankAccountRepository(BaseRepository[BankAccount]):

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        account_name: str,
        account_number: str,
        bank_code: str,
        bank_name: str,
        is_default: bool = False,
    ) -> BankAccount:
        account = BankAccount(
            user_id=user_id,
            account_name=account_name,
            account_number=account_number,
            bank_code=bank_code,
            bank_name=bank_name,
            is_default=is_default,
        )
        self.session.add(account)
        await self.session.flush()
        await self.session.refresh(account)
        return account

    async def get_by_id(self, account_id: uuid.UUID) -> Optional[BankAccount]:
        result = await self.session.execute(
            select(BankAccount).where(
                BankAccount.id == account_id,
                BankAccount.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def get_by_user_id(self, user_id: uuid.UUID) -> list[BankAccount]:
        result = await self.session.execute(
            select(BankAccount)
            .where(
                BankAccount.user_id == user_id,
                BankAccount.deleted_at.is_(None),
            )
            .order_by(BankAccount.created_at.asc())
        )
        return list(result.scalars().all())

    async def get_default(self, user_id: uuid.UUID) -> Optional[BankAccount]:
        result = await self.session.execute(
            select(BankAccount).where(
                BankAccount.user_id == user_id,
                BankAccount.is_default.is_(True),
                BankAccount.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def count_by_user(self, user_id: uuid.UUID) -> int:
        """Return number of non-deleted bank accounts for a user."""
        from sqlalchemy import func

        result = await self.session.execute(
            select(func.count()).select_from(BankAccount).where(
                BankAccount.user_id == user_id,
                BankAccount.deleted_at.is_(None),
            )
        )
        return result.scalar_one()

    async def unset_all_defaults(self, user_id: uuid.UUID) -> None:
        """Clear is_default on all active bank accounts for a user."""
        await self.session.execute(
            update(BankAccount)
            .where(
                BankAccount.user_id == user_id,
                BankAccount.deleted_at.is_(None),
            )
            .values(is_default=False)
        )

    async def set_default(self, bank_account: BankAccount) -> BankAccount:
        """Unset all defaults for this user, then mark this account as default."""
        await self.unset_all_defaults(bank_account.user_id)
        bank_account.is_default = True
        await self.session.flush()
        await self.session.refresh(bank_account)
        return bank_account

    async def set_recipient_code(
        self, bank_account: BankAccount, code: str
    ) -> BankAccount:
        bank_account.paystack_recipient_code = code
        await self.session.flush()
        await self.session.refresh(bank_account)
        return bank_account

    async def soft_delete(self, bank_account: BankAccount) -> None:
        bank_account.soft_delete()
        # If the deleted account was the default, promote the oldest remaining one
        if bank_account.is_default:
            bank_account.is_default = False
            await self.session.flush()
            remaining = await self.get_by_user_id(bank_account.user_id)
            if remaining:
                remaining[0].is_default = True
        await self.session.flush()
