import uuid
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ledger_entry import EntryType, LedgerEntry
from app.repositories.ledger import LedgerRepository


class LedgerService:
    """
    Double-entry accounting engine.

    This service writes paired DEBIT + CREDIT entries for every completed
    money movement.  It is NOT a transaction manager — it never calls
    ``session.begin()`` or ``session.commit()``.  Callers (Transfer,
    Paystack, Withdrawal services, etc.) are responsible for:

    1. Locking the wallet rows before calling ``post_double_entry`` (SELECT
       FOR UPDATE via WalletRepository.lock_for_update).
    2. Updating wallet.balance in the same transaction.
    3. Providing the post-update balance snapshots as ``debit_balance_after``
       and ``credit_balance_after``.
    4. Committing (or rolling back) the outer transaction.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._repo = LedgerRepository(session)

    async def post_double_entry(
        self,
        *,
        transaction_id: uuid.UUID,
        debit_wallet_id: uuid.UUID,
        credit_wallet_id: uuid.UUID,
        amount: Decimal,
        currency: str,
        debit_balance_after: Decimal,
        credit_balance_after: Decimal,
    ) -> tuple[LedgerEntry, LedgerEntry]:
        """
        Write exactly one DEBIT and one CREDIT entry for a money movement.

        Parameters
        ----------
        transaction_id:
            The platform transaction this posting belongs to.
        debit_wallet_id:
            Wallet that is being debited (funds leave).
        credit_wallet_id:
            Wallet that is being credited (funds arrive).
        amount:
            Positive value representing the amount moved.
        currency:
            ISO-4217 currency code (e.g. "NGN").
        debit_balance_after:
            Balance of the debit wallet after deduction — caller's
            responsibility to compute from the locked row.
        credit_balance_after:
            Balance of the credit wallet after addition — caller's
            responsibility to compute from the locked row.

        Returns
        -------
        (debit_entry, credit_entry) — both flushed into the session; the
        caller's outer transaction commit will persist them.
        """
        debit = await self._repo.create_entry(
            transaction_id=transaction_id,
            wallet_id=debit_wallet_id,
            entry_type=EntryType.DEBIT,
            amount=amount,
            currency=currency,
            balance_after=debit_balance_after,
        )
        credit = await self._repo.create_entry(
            transaction_id=transaction_id,
            wallet_id=credit_wallet_id,
            entry_type=EntryType.CREDIT,
            amount=amount,
            currency=currency,
            balance_after=credit_balance_after,
        )
        return debit, credit
