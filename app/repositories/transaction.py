import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.models.transaction import (
    VALID_TRANSITIONS,
    Transaction,
    TransactionStatus,
    TransactionType,
)
from app.repositories.base import BaseRepository


class TransactionRepository(BaseRepository[Transaction]):

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def create(
        self,
        *,
        reference: str,
        type: TransactionType,
        amount: Decimal,
        initiated_by_user_id: uuid.UUID,
        status: TransactionStatus = TransactionStatus.PENDING,
        currency: str = "NGN",
        source_wallet_id: Optional[uuid.UUID] = None,
        destination_wallet_id: Optional[uuid.UUID] = None,
        provider_reference: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        extra_data: Optional[dict] = None,
        failure_reason: Optional[str] = None,
    ) -> Transaction:
        txn = Transaction(
            reference=reference,
            type=type,
            status=status,
            amount=amount,
            currency=currency,
            source_wallet_id=source_wallet_id,
            destination_wallet_id=destination_wallet_id,
            initiated_by_user_id=initiated_by_user_id,
            provider_reference=provider_reference,
            idempotency_key=idempotency_key,
            extra_data=extra_data,
            failure_reason=failure_reason,
        )
        self.session.add(txn)
        await self.session.flush()
        await self.session.refresh(txn)
        return txn

    async def get_by_id(self, transaction_id: uuid.UUID) -> Optional[Transaction]:
        result = await self.session.execute(
            select(Transaction).where(Transaction.id == transaction_id)
        )
        return result.scalar_one_or_none()

    async def get_by_reference(self, reference: str) -> Optional[Transaction]:
        result = await self.session.execute(
            select(Transaction).where(Transaction.reference == reference)
        )
        return result.scalar_one_or_none()

    async def get_by_provider_reference(
        self, provider_reference: str
    ) -> Optional[Transaction]:
        result = await self.session.execute(
            select(Transaction).where(
                Transaction.provider_reference == provider_reference
            )
        )
        return result.scalar_one_or_none()

    async def get_by_idempotency_key(
        self, idempotency_key: str
    ) -> Optional[Transaction]:
        result = await self.session.execute(
            select(Transaction).where(
                Transaction.idempotency_key == idempotency_key
            )
        )
        return result.scalar_one_or_none()

    async def get_by_wallet(
        self, wallet_id: uuid.UUID, *, limit: int, offset: int
    ) -> tuple[list[Transaction], int]:
        """
        Return all transactions where the wallet was source OR destination,
        ordered by created_at DESC.  Used by the wallet statement endpoint.
        """
        wallet_filter = or_(
            Transaction.source_wallet_id == wallet_id,
            Transaction.destination_wallet_id == wallet_id,
        )

        total = (
            await self.session.execute(
                select(func.count()).select_from(Transaction).where(wallet_filter)
            )
        ).scalar_one()

        rows = (
            await self.session.execute(
                select(Transaction)
                .where(wallet_filter)
                .order_by(Transaction.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        ).scalars().all()

        return list(rows), total

    async def get_by_user(
        self,
        user_id: uuid.UUID,
        *,
        limit: int,
        offset: int,
        type_filter: Optional[TransactionType] = None,
        status_filter: Optional[TransactionStatus] = None,
    ) -> tuple[list[Transaction], int]:
        """
        Return transactions initiated by this user, with optional type/status
        filters, ordered by created_at DESC.
        """
        filters = [Transaction.initiated_by_user_id == user_id]
        if type_filter is not None:
            filters.append(Transaction.type == type_filter)
        if status_filter is not None:
            filters.append(Transaction.status == status_filter)

        where_clause = and_(*filters)

        total = (
            await self.session.execute(
                select(func.count()).select_from(Transaction).where(where_clause)
            )
        ).scalar_one()

        rows = (
            await self.session.execute(
                select(Transaction)
                .where(where_clause)
                .order_by(Transaction.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        ).scalars().all()

        return list(rows), total

    async def update_status(
        self,
        transaction: Transaction,
        new_status: TransactionStatus,
        *,
        failure_reason: Optional[str] = None,
        provider_reference: Optional[str] = None,
    ) -> Transaction:
        """
        Transition a transaction to a new status, enforcing the state machine.

        Raises
        ------
        ValidationError – the transition is not allowed from the current status
        """
        allowed = VALID_TRANSITIONS.get(transaction.status, frozenset())
        if new_status not in allowed:
            raise ValidationError(
                f"Cannot transition transaction from "
                f"'{transaction.status.value}' to '{new_status.value}'.",
                error_code="INVALID_STATUS_TRANSITION",
            )

        transaction.status = new_status
        if failure_reason is not None:
            transaction.failure_reason = failure_reason
        if provider_reference is not None:
            transaction.provider_reference = provider_reference

        await self.session.flush()
        await self.session.refresh(transaction)
        return transaction

    async def list_admin(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        status: Optional[TransactionStatus] = None,
        type: Optional[TransactionType] = None,
        risk_flagged: Optional[bool] = None,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
    ) -> list[Transaction]:
        """
        Paginated list of all transactions for admin inspection, newest first.
        Optionally filtered by status, type, risk_flagged flag, and date range.
        """
        stmt = select(Transaction)
        if status is not None:
            stmt = stmt.where(Transaction.status == status)
        if type is not None:
            stmt = stmt.where(Transaction.type == type)
        if risk_flagged is not None:
            stmt = stmt.where(Transaction.risk_flagged == risk_flagged)
        if from_date is not None:
            stmt = stmt.where(Transaction.created_at >= from_date)
        if to_date is not None:
            stmt = stmt.where(Transaction.created_at <= to_date)
        stmt = stmt.order_by(Transaction.created_at.desc()).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_admin(
        self,
        *,
        status: Optional[TransactionStatus] = None,
        type: Optional[TransactionType] = None,
        risk_flagged: Optional[bool] = None,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
    ) -> int:
        """Return total count matching the given admin filters."""
        stmt = select(func.count()).select_from(Transaction)
        if status is not None:
            stmt = stmt.where(Transaction.status == status)
        if type is not None:
            stmt = stmt.where(Transaction.type == type)
        if risk_flagged is not None:
            stmt = stmt.where(Transaction.risk_flagged == risk_flagged)
        if from_date is not None:
            stmt = stmt.where(Transaction.created_at >= from_date)
        if to_date is not None:
            stmt = stmt.where(Transaction.created_at <= to_date)
        result = await self.session.execute(stmt)
        return result.scalar_one()
