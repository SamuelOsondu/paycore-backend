import uuid
from decimal import Decimal
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    ForbiddenError,
    InsufficientBalanceError,
    NotFoundError,
    ValidationError,
)
from app.models.audit_log import ActorType
from app.models.transaction import Transaction, TransactionStatus, TransactionType
from app.models.user import User
from app.repositories.transaction import TransactionRepository
from app.repositories.user import UserRepository
from app.repositories.wallet import WalletRepository
from app.services.fraud import FraudService
from app.services.ledger import LedgerService


class TransferService:

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._wallet_repo = WalletRepository(session)
        self._txn_repo = TransactionRepository(session)
        self._user_repo = UserRepository(session)
        self._ledger = LedgerService(session)
        self._fraud = FraudService(session)

    async def initiate_transfer(
        self,
        sender: User,
        *,
        recipient_user_id: Optional[uuid.UUID] = None,
        recipient_email: Optional[str] = None,
        amount: Decimal,
        idempotency_key: Optional[str] = None,
    ) -> Transaction:
        """
        Execute an atomic wallet-to-wallet transfer.

        Flow
        ----
        Resolve recipient → idempotency check → fraud checks → lock both
        wallets in consistent UUID order (prevents deadlock) → balance check
        → write transaction + ledger entries + update balances → commit →
        post-commit async risk flag.

        Raises
        ------
        ValidationError          – self-transfer
        ForbiddenError           – sender or recipient wallet inactive
        NotFoundError            – recipient user or wallet not found
        InsufficientBalanceError – sender balance < amount
        KYCTierError             – tier 0, or single-transaction limit exceeded
        DailyLimitError          – daily transfer volume exceeded
        DuplicateTransferError   – identical transfer within the duplicate window
        """
        # Resolve sender wallet
        sender_wallet = await self._wallet_repo.get_by_user_id(sender.id)
        if sender_wallet is None or not sender_wallet.is_active:
            raise ForbiddenError("Your wallet is inactive.")

        # Resolve recipient user (by ID or email)
        if recipient_user_id is not None:
            recipient = await self._user_repo.get_by_id(recipient_user_id)
        else:
            recipient = await self._user_repo.get_by_email(recipient_email)  # type: ignore[arg-type]

        if recipient is None or not recipient.is_active:
            raise NotFoundError("Recipient")

        # Self-transfer guard
        if sender.id == recipient.id:
            raise ValidationError(
                "You cannot transfer funds to yourself.",
                error_code="SELF_TRANSFER",
            )

        # Resolve recipient wallet
        recipient_wallet = await self._wallet_repo.get_by_user_id(recipient.id)
        if recipient_wallet is None or not recipient_wallet.is_active:
            raise ForbiddenError("Recipient wallet is inactive.")

        # Idempotency: return the existing transaction if this key was already used
        if idempotency_key:
            existing = await self._txn_repo.get_by_idempotency_key(idempotency_key)
            if existing is not None:
                return existing

        # Fraud checks — synchronous, run before any DB writes
        await self._fraud.check_transfer(
            sender_user=sender,
            sender_wallet=sender_wallet,
            recipient_wallet_id=recipient_wallet.id,
            amount=amount,
        )

        # Lock both wallets in UUID string order to prevent deadlock
        first_id, second_id = sorted(
            [sender_wallet.id, recipient_wallet.id], key=str
        )
        locked_first = await self._wallet_repo.lock_for_update(first_id)
        locked_second = await self._wallet_repo.lock_for_update(second_id)

        if locked_first is None or locked_second is None:
            raise NotFoundError("Wallet")

        # Re-map to named variables
        if first_id == sender_wallet.id:
            locked_sender, locked_recipient = locked_first, locked_second
        else:
            locked_sender, locked_recipient = locked_second, locked_first

        # Balance check against the freshly-locked row
        if locked_sender.balance < amount:
            raise InsufficientBalanceError()

        sender_balance_after = locked_sender.balance - amount
        recipient_balance_after = locked_recipient.balance + amount

        # Write transaction record, ledger entries, and balance updates atomically.
        # The idempotency_key column has a DB-level unique constraint, so if two
        # concurrent requests both slip past the pre-check above, the second flush
        # will raise IntegrityError.  We catch it, rollback, and return the already-
        # committed transaction — giving the caller the correct idempotent response.
        try:
            txn = await self._txn_repo.create(
                reference=f"txn_{uuid.uuid4().hex[:12]}",
                type=TransactionType.TRANSFER,
                status=TransactionStatus.COMPLETED,
                amount=amount,
                currency=locked_sender.currency,
                source_wallet_id=locked_sender.id,
                destination_wallet_id=locked_recipient.id,
                initiated_by_user_id=sender.id,
                idempotency_key=idempotency_key,
            )

            # Write double-entry ledger entries
            await self._ledger.post_double_entry(
                transaction_id=txn.id,
                debit_wallet_id=locked_sender.id,
                credit_wallet_id=locked_recipient.id,
                amount=amount,
                currency=locked_sender.currency,
                debit_balance_after=sender_balance_after,
                credit_balance_after=recipient_balance_after,
            )

            # Update wallet balances
            await self._wallet_repo.update_balance(locked_sender, sender_balance_after)
            await self._wallet_repo.update_balance(locked_recipient, recipient_balance_after)

            await self.session.commit()

        except IntegrityError:
            await self.session.rollback()
            # Another concurrent request with the same idempotency_key committed
            # first.  Fetch and return that transaction.
            if idempotency_key:
                existing = await self._txn_repo.get_by_idempotency_key(idempotency_key)
                if existing is not None:
                    return existing
            raise

        # Post-commit async risk flag — never blocks the response
        await self._fraud.maybe_flag_rapid_transfers(
            transaction_id=txn.id,
            sender_wallet_id=locked_sender.id,
        )

        # Audit log — fire-and-forget after commit
        from app.services.audit import AuditService
        await AuditService(self.session).log(
            actor_id=sender.id,
            actor_type=ActorType.USER,
            action="transfer.completed",
            target_type="transaction",
            target_id=txn.id,
            metadata={
                "amount": str(amount),
                "currency": locked_sender.currency,
                "recipient_wallet_id": str(locked_recipient.id),
            },
        )

        return txn
