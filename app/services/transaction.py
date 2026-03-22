import uuid
from decimal import Decimal
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.models.transaction import Transaction, TransactionStatus, TransactionType
from app.repositories.transaction import TransactionRepository
from app.schemas.common import PaginatedData
from app.schemas.transaction import TransactionOut


class TransactionService:
    """
    Helper layer for creating and querying Transaction records.

    This service does NOT orchestrate money movement — that belongs to
    Transfer, Paystack, MerchantPayment, and Withdrawal services.
    It provides the shared building blocks those services depend on.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = TransactionRepository(session)

    async def create_transaction(
        self,
        *,
        type: TransactionType,
        amount: Decimal,
        initiated_by_user_id: uuid.UUID,
        source_wallet_id: Optional[uuid.UUID] = None,
        destination_wallet_id: Optional[uuid.UUID] = None,
        currency: str = "NGN",
        idempotency_key: Optional[str] = None,
        extra_data: Optional[dict] = None,
    ) -> Transaction:
        """
        Create a new PENDING transaction record.

        Idempotency
        -----------
        When `idempotency_key` is provided and a transaction with that key
        already exists, the existing record is returned without a new insert.
        Callers must handle the case where the returned transaction is not
        in PENDING status (it may already be processing or completed).

        This method does NOT commit.  The caller owns the transaction scope.
        """
        if idempotency_key:
            existing = await self.repo.get_by_idempotency_key(idempotency_key)
            if existing is not None:
                return existing

        reference = f"txn_{uuid.uuid4()}"
        return await self.repo.create(
            reference=reference,
            type=type,
            status=TransactionStatus.PENDING,
            amount=amount,
            currency=currency,
            source_wallet_id=source_wallet_id,
            destination_wallet_id=destination_wallet_id,
            initiated_by_user_id=initiated_by_user_id,
            idempotency_key=idempotency_key,
            extra_data=extra_data,
        )

    async def get_transaction(
        self, reference: str, *, requesting_user_id: uuid.UUID
    ) -> Transaction:
        """
        Fetch a transaction by reference, enforcing ownership.

        Returns the transaction only if `initiated_by_user_id` matches the
        requesting user.  Otherwise behaves identically to a not-found case,
        preventing transaction reference enumeration.

        Raises
        ------
        NotFoundError – transaction not found or belongs to another user
        """
        txn = await self.repo.get_by_reference(reference)
        if txn is None or txn.initiated_by_user_id != requesting_user_id:
            raise NotFoundError("Transaction")
        return txn

    async def list_transactions(
        self,
        user_id: uuid.UUID,
        *,
        limit: int,
        offset: int,
        type_filter: Optional[TransactionType] = None,
        status_filter: Optional[TransactionStatus] = None,
    ) -> PaginatedData:
        items, total = await self.repo.get_by_user(
            user_id,
            limit=limit,
            offset=offset,
            type_filter=type_filter,
            status_filter=status_filter,
        )
        return PaginatedData(
            items=[TransactionOut.model_validate(t) for t in items],
            total=total,
            limit=limit,
            offset=offset,
        )
