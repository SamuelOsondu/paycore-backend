import uuid
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.models.wallet import Wallet
from app.repositories.transaction import TransactionRepository
from app.repositories.wallet import WalletRepository
from app.schemas.common import PaginatedData
from app.schemas.transaction import TransactionOut


class WalletService:

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = WalletRepository(session)

    async def create_wallet(
        self, user_id: uuid.UUID, *, currency: str = "NGN"
    ) -> Wallet:
        """
        Create the default wallet for a newly registered user.

        Must be called inside the same DB transaction as user creation so that
        both rows are committed (or rolled back) atomically.

        Raises
        ------
        ConflictError – user already has a wallet (duplicate registration guard)
        """
        existing = await self.repo.get_by_user_id(user_id)
        if existing is not None:
            raise ConflictError(
                "A wallet already exists for this user.",
                error_code="WALLET_ALREADY_EXISTS",
            )
        return await self.repo.create(user_id=user_id, currency=currency)

    async def get_wallet(self, user_id: uuid.UUID) -> Wallet:
        """
        Return the active wallet for a user.

        Raises
        ------
        NotFoundError – no wallet exists for the user
        """
        wallet = await self.repo.get_by_user_id(user_id)
        if wallet is None:
            raise NotFoundError("Wallet")
        return wallet

    async def get_balance(self, user_id: uuid.UUID) -> Decimal:
        wallet = await self.get_wallet(user_id)
        return wallet.balance

    async def get_statement(
        self, user_id: uuid.UUID, *, limit: int, offset: int
    ) -> PaginatedData:
        """
        Return the user's paginated wallet statement — all transactions where
        the wallet was either the source or the destination, ordered by
        created_at DESC.

        Raises
        ------
        NotFoundError – no wallet exists for the user
        """
        wallet = await self.get_wallet(user_id)
        txn_repo = TransactionRepository(self.session)
        items, total = await txn_repo.get_by_wallet(
            wallet.id, limit=limit, offset=offset
        )
        return PaginatedData(
            items=[TransactionOut.model_validate(t) for t in items],
            total=total,
            limit=limit,
            offset=offset,
        )

    async def assert_wallet_active(self, user_id: uuid.UUID) -> Wallet:
        """
        Convenience guard used by Transfers, Payments, and Withdrawals.
        Returns the wallet or raises ForbiddenError if deactivated.
        """
        wallet = await self.get_wallet(user_id)
        if not wallet.is_active:
            raise ForbiddenError(
                "Wallet is deactivated. Please contact support.",
            )
        return wallet
