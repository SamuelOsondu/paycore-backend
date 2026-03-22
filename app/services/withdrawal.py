"""
WithdrawalService — bank account management and withdrawal orchestration.

Flow
----
initiate_withdrawal
  → fraud checks (KYC Tier 2, limits)
  → one-active-withdrawal guard
  → lock wallet → balance check
  → create PENDING WITHDRAWAL transaction
  → deduct balance immediately (hold) — no ledger entry at this stage
  → commit → enqueue process_withdrawal Celery task

process_payout_success  (called from PaystackWebhookService on transfer.success)
  → find PROCESSING withdrawal by reference
  → write single DEBIT ledger entry (formalises the balance hold)
  → PROCESSING → COMPLETED → commit

process_payout_failure  (called from PaystackWebhookService on transfer.failed/reversed)
  → find txn by reference
  → lock wallet → credit held amount back
  → PROCESSING → FAILED → commit

BankAccountVerificationService
  → optionally calls Paystack /bank/resolve to confirm account name
  → fails gracefully if PAYSTACK_SECRET_KEY is not configured
"""

import logging
import uuid
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import (
    ExternalServiceError,
    ForbiddenError,
    InsufficientBalanceError,
    NotFoundError,
    ValidationError,
)
from app.models.audit_log import ActorType
from app.models.bank_account import BankAccount
from app.models.ledger_entry import EntryType, LedgerEntry
from app.models.transaction import Transaction, TransactionStatus, TransactionType
from app.models.user import User
from app.repositories.bank_account import BankAccountRepository
from app.repositories.transaction import TransactionRepository
from app.repositories.wallet import WalletRepository
from app.services.fraud import FraudService

logger = logging.getLogger(__name__)


# ── Bank account verification ──────────────────────────────────────────────────


class BankAccountVerificationService:
    """
    Optionally verifies a Nigerian bank account against Paystack's bank/resolve
    endpoint.  Returns the Paystack-confirmed account name, or None if
    verification is unavailable (PAYSTACK_SECRET_KEY not configured, Paystack
    unreachable, or account not found).
    """

    @staticmethod
    async def verify_account(
        account_number: str, bank_code: str
    ) -> Optional[str]:
        """
        Return the verified account name, or None if unavailable.

        Never raises — errors are logged and None is returned so account
        addition can proceed without the Paystack verification step.
        """
        if not settings.PAYSTACK_SECRET_KEY:
            return None
        try:
            from app.integrations.paystack import PaystackClient

            client = PaystackClient()
            data = await client.resolve_account(
                account_number=account_number, bank_code=bank_code
            )
            return data.get("account_name") or None
        except ExternalServiceError:
            logger.warning(
                "Bank account verification unavailable for %s/%s",
                account_number,
                bank_code,
            )
            return None
        except Exception:
            logger.exception(
                "Unexpected error during bank account verification %s/%s",
                account_number,
                bank_code,
            )
            return None


# ── WithdrawalService ──────────────────────────────────────────────────────────


class WithdrawalService:

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._account_repo = BankAccountRepository(session)
        self._txn_repo = TransactionRepository(session)
        self._wallet_repo = WalletRepository(session)
        self._fraud = FraudService(session)

    # ── Bank account management ────────────────────────────────────────────────

    async def add_bank_account(
        self,
        user: User,
        *,
        account_name: str,
        account_number: str,
        bank_code: str,
        bank_name: str,
    ) -> BankAccount:
        """
        Register a new bank account for the user.

        Optionally verifies the account via Paystack before saving.
        The Paystack-confirmed name takes precedence over the client-supplied
        name if verification succeeds.  The first account added automatically
        becomes the default.
        """
        # Optional Paystack verification — use verified name if available
        verified_name = await BankAccountVerificationService.verify_account(
            account_number, bank_code
        )
        final_name = verified_name if verified_name else account_name

        # First account becomes default automatically
        existing_count = await self._account_repo.count_by_user(user.id)
        is_default = existing_count == 0

        account = await self._account_repo.create(
            user_id=user.id,
            account_name=final_name,
            account_number=account_number,
            bank_code=bank_code,
            bank_name=bank_name,
            is_default=is_default,
        )
        await self.session.commit()
        return account

    async def list_bank_accounts(self, user: User) -> list[BankAccount]:
        return await self._account_repo.get_by_user_id(user.id)

    async def remove_bank_account(
        self, user: User, bank_account_id: uuid.UUID
    ) -> None:
        """
        Soft-delete a bank account.

        Raises
        ------
        NotFoundError  – account not found or doesn't belong to the user.
        ForbiddenError – account has an active withdrawal in progress.
        """
        account = await self._account_repo.get_by_id(bank_account_id)
        if account is None or account.user_id != user.id:
            raise NotFoundError("Bank account")

        # Block removal if an active withdrawal references this account
        active = await self._get_active_withdrawal(user.id)
        if active is not None:
            extra = active.extra_data or {}
            if str(extra.get("bank_account_id")) == str(bank_account_id):
                raise ForbiddenError(
                    "Cannot remove a bank account with an active withdrawal in progress."
                )

        await self._account_repo.soft_delete(account)
        await self.session.commit()

    # ── Withdrawal initiation ──────────────────────────────────────────────────

    async def initiate_withdrawal(
        self,
        user: User,
        *,
        bank_account_id: uuid.UUID,
        amount: Decimal,
    ) -> Transaction:
        """
        Initiate a withdrawal request.

        Flow
        ----
        1. Wallet active guard.
        2. Fraud checks: KYC Tier 2, single-transaction limit, daily limit.
        3. Guard: only one PENDING/PROCESSING withdrawal at a time per user.
        4. Resolve bank account (must belong to user, not deleted).
        5. Lock wallet; check balance.
        6. Create PENDING WITHDRAWAL transaction.
        7. Deduct amount from wallet (balance hold — no ledger entry yet).
        8. Commit.
        9. Enqueue process_withdrawal Celery task (post-commit, non-blocking).

        Raises
        ------
        ForbiddenError           – wallet inactive / bank account not owned
        KYCTierError             – user is below Tier 2
        InsufficientBalanceError – balance too low
        DailyLimitError          – daily withdrawal limit exceeded
        ValidationError          – duplicate active withdrawal (WITHDRAWAL_ALREADY_PENDING)
        NotFoundError            – bank account not found
        """
        # 1. Wallet guard
        wallet = await self._wallet_repo.get_by_user_id(user.id)
        if wallet is None or not wallet.is_active:
            raise ForbiddenError("Wallet is inactive or does not exist.")

        # 2. Fraud checks
        await self._fraud.check_withdrawal(user, wallet, amount)

        # 3. Only one active withdrawal at a time
        active = await self._get_active_withdrawal(user.id)
        if active is not None:
            raise ValidationError(
                "You already have a withdrawal in progress. "
                "Please wait for it to complete before initiating another.",
                error_code="WITHDRAWAL_ALREADY_PENDING",
            )

        # 4. Resolve bank account (ownership check)
        bank_account = await self._account_repo.get_by_id(bank_account_id)
        if bank_account is None or bank_account.user_id != user.id:
            raise NotFoundError("Bank account")

        # 5. Lock wallet; check balance
        locked_wallet = await self._wallet_repo.lock_for_update(wallet.id)
        if locked_wallet is None:
            raise ForbiddenError("Wallet not found.")
        if locked_wallet.balance < amount:
            raise InsufficientBalanceError()

        # 6. Create PENDING WITHDRAWAL transaction
        reference = f"txn_{uuid.uuid4().hex}"
        txn = await self._txn_repo.create(
            reference=reference,
            type=TransactionType.WITHDRAWAL,
            status=TransactionStatus.PENDING,
            amount=amount,
            currency=locked_wallet.currency,
            source_wallet_id=locked_wallet.id,
            initiated_by_user_id=user.id,
            extra_data={"bank_account_id": str(bank_account_id)},
        )

        # 7. Deduct wallet balance (hold) — ledger entry written on payout success
        new_balance = locked_wallet.balance - amount
        await self._wallet_repo.update_balance(locked_wallet, new_balance)

        # 8. Commit
        await self.session.commit()

        # 9. Enqueue Celery task post-commit
        try:
            from app.workers.withdrawal_tasks import process_withdrawal

            process_withdrawal.delay(str(txn.id))
        except Exception:
            logger.exception(
                "Failed to enqueue process_withdrawal for txn %s — "
                "reconciliation will retry.",
                txn.id,
            )

        logger.info(
            "Withdrawal initiated: user=%s amount=%s NGN ref=%s",
            user.id,
            amount,
            reference,
        )

        # Audit log — fire-and-forget after commit
        from app.services.audit import AuditService
        await AuditService(self.session).log(
            actor_id=user.id,
            actor_type=ActorType.USER,
            action="withdrawal.initiated",
            target_type="transaction",
            target_id=txn.id,
            metadata={"amount": str(amount), "currency": txn.currency},
        )

        return txn

    # ── Payout result handlers (called from PaystackWebhookService) ────────────

    async def process_payout_success(
        self, *, reference: str, transfer_code: str
    ) -> None:
        """
        Finalise a successful payout.

        Writes the DEBIT ledger entry that formalises the balance deduction
        made at initiation, then transitions the transaction to COMPLETED.

        Idempotent — safe to call multiple times for the same reference.
        """
        txn = await self._txn_repo.get_by_reference(reference)
        if txn is None:
            logger.warning(
                "process_payout_success: no transaction for reference=%s", reference
            )
            return

        if txn.status == TransactionStatus.COMPLETED:
            logger.info(
                "process_payout_success: txn %s already COMPLETED — idempotent skip",
                txn.id,
            )
            return

        if txn.status != TransactionStatus.PROCESSING:
            logger.warning(
                "process_payout_success: txn %s has unexpected status '%s' — skipping",
                txn.id,
                txn.status,
            )
            return

        if txn.source_wallet_id is None:
            logger.error(
                "process_payout_success: txn %s has no source_wallet_id", txn.id
            )
            return

        # Lock wallet for balance_after snapshot (balance unchanged at this point)
        wallet = await self._wallet_repo.lock_for_update(txn.source_wallet_id)
        if wallet is None:
            logger.error(
                "process_payout_success: source wallet not found for txn %s", txn.id
            )
            return

        # Write DEBIT ledger entry — formalises the balance hold made at initiation
        entry = LedgerEntry(
            transaction_id=txn.id,
            wallet_id=wallet.id,
            entry_type=EntryType.DEBIT,
            amount=txn.amount,
            currency=txn.currency,
            balance_after=wallet.balance,  # already reduced at initiation
        )
        self.session.add(entry)
        await self.session.flush()

        # PROCESSING → COMPLETED
        await self._txn_repo.update_status(txn, TransactionStatus.COMPLETED)
        await self.session.commit()

        logger.info(
            "process_payout_success: txn %s COMPLETED, DEBIT ledger entry written",
            txn.id,
        )

        # Audit log — fire-and-forget after commit
        from app.services.audit import AuditService
        await AuditService(self.session).log(
            actor_id=txn.initiated_by_user_id,
            actor_type=ActorType.SYSTEM,
            action="withdrawal.completed",
            target_type="transaction",
            target_id=txn.id,
            metadata={"amount": str(txn.amount), "transfer_code": transfer_code},
        )

    async def process_payout_failure(
        self, *, reference: str, transfer_code: str
    ) -> None:
        """
        Reverse a failed payout: credit the held balance back to the user's wallet.

        Idempotent — safe to call multiple times for the same reference.
        """
        txn = await self._txn_repo.get_by_reference(reference)
        if txn is None:
            logger.warning(
                "process_payout_failure: no transaction for reference=%s", reference
            )
            return

        if txn.status == TransactionStatus.FAILED:
            logger.info(
                "process_payout_failure: txn %s already FAILED — idempotent skip",
                txn.id,
            )
            return

        if txn.status != TransactionStatus.PROCESSING:
            logger.warning(
                "process_payout_failure: txn %s has unexpected status '%s' — skipping",
                txn.id,
                txn.status,
            )
            return

        if txn.source_wallet_id is None:
            logger.error(
                "process_payout_failure: txn %s has no source_wallet_id", txn.id
            )
            return

        # Lock wallet and credit the held amount back
        wallet = await self._wallet_repo.lock_for_update(txn.source_wallet_id)
        if wallet is None:
            logger.error(
                "process_payout_failure: source wallet not found for txn %s", txn.id
            )
            return

        restored_balance = wallet.balance + txn.amount
        await self._wallet_repo.update_balance(wallet, restored_balance)

        # PROCESSING → FAILED
        await self._txn_repo.update_status(
            txn,
            TransactionStatus.FAILED,
            failure_reason=f"Paystack transfer failed or reversed: {transfer_code}",
        )
        await self.session.commit()

        logger.info(
            "process_payout_failure: txn %s FAILED, %s NGN returned to wallet %s",
            txn.id,
            txn.amount,
            wallet.id,
        )

        # Audit log — fire-and-forget after commit
        from app.services.audit import AuditService
        await AuditService(self.session).log(
            actor_id=txn.initiated_by_user_id,
            actor_type=ActorType.SYSTEM,
            action="withdrawal.failed",
            target_type="transaction",
            target_id=txn.id,
            metadata={
                "amount": str(txn.amount),
                "transfer_code": transfer_code,
                "reason": f"Paystack transfer failed: {transfer_code}",
            },
        )

    # ── Payout execution (called from Celery task) ─────────────────────────────

    async def execute_payout(self, transaction_id: uuid.UUID) -> None:
        """
        Execute the Paystack transfer for a PENDING withdrawal.

        Flow
        ----
        1. Fetch transaction — skip if not PENDING/PROCESSING.
        2. If PROCESSING and provider_reference already set → transfer already
           dispatched; wait for webhook (idempotent return).
        3. Fetch bank account from extra_data.
        4. Create Paystack transfer recipient if paystack_recipient_code not cached.
        5. Transition PENDING → PROCESSING.
        6. Call PaystackClient.initiate_transfer (uses txn.reference for idempotency).
        7. Store transfer_code as provider_reference directly on the transaction.
        8. Commit.
        """
        from app.integrations.paystack import PaystackClient

        txn = await self._txn_repo.get_by_id(transaction_id)
        if txn is None:
            logger.error("execute_payout: transaction %s not found", transaction_id)
            return

        if txn.status in (TransactionStatus.COMPLETED, TransactionStatus.FAILED):
            logger.info(
                "execute_payout: txn %s already %s — skip", txn.id, txn.status
            )
            return

        # Already dispatched in a previous attempt — wait for webhook
        if (
            txn.status == TransactionStatus.PROCESSING
            and txn.provider_reference is not None
        ):
            logger.info(
                "execute_payout: txn %s PROCESSING (transfer_code=%s) — "
                "waiting for webhook",
                txn.id,
                txn.provider_reference,
            )
            return

        # Resolve bank account from extra_data
        extra = txn.extra_data or {}
        bank_account_id_str = extra.get("bank_account_id")
        if not bank_account_id_str:
            logger.error(
                "execute_payout: txn %s missing bank_account_id in extra_data", txn.id
            )
            return
        bank_account = await self._account_repo.get_by_id(
            uuid.UUID(bank_account_id_str)
        )
        if bank_account is None:
            logger.error(
                "execute_payout: bank account %s not found for txn %s",
                bank_account_id_str,
                txn.id,
            )
            return

        client = PaystackClient()

        # Create Paystack transfer recipient if not already cached on the account
        recipient_code = bank_account.paystack_recipient_code
        if not recipient_code:
            recipient_data = await client.create_transfer_recipient(
                name=bank_account.account_name,
                account_number=bank_account.account_number,
                bank_code=bank_account.bank_code,
            )
            recipient_code = recipient_data.get("recipient_code", "")
            if not recipient_code:
                raise ExternalServiceError("Paystack")
            await self._account_repo.set_recipient_code(bank_account, recipient_code)
            # Flush the recipient_code update before proceeding
            await self.session.flush()

        # Transition PENDING → PROCESSING
        if txn.status == TransactionStatus.PENDING:
            await self._txn_repo.update_status(txn, TransactionStatus.PROCESSING)

        # Initiate Paystack transfer.
        # Uses txn.reference as the Paystack transfer reference for idempotency:
        # if the task runs twice, Paystack returns the same transfer — no double send.
        amount_kobo = int(txn.amount * 100)
        transfer_data = await client.initiate_transfer(
            amount_kobo=amount_kobo,
            recipient_code=recipient_code,
            reference=txn.reference,
            reason=f"PayCore withdrawal {txn.reference}",
        )
        transfer_code_value = transfer_data.get("transfer_code", "")

        # Set provider_reference directly — no status change needed (already PROCESSING)
        txn.provider_reference = transfer_code_value
        await self.session.flush()
        await self.session.commit()

        logger.info(
            "execute_payout: transfer dispatched for txn %s, transfer_code=%s",
            txn.id,
            transfer_code_value,
        )

    # ── Internal helpers ───────────────────────────────────────────────────────

    async def _get_active_withdrawal(
        self, user_id: uuid.UUID
    ) -> Optional[Transaction]:
        """Return the user's active (PENDING or PROCESSING) WITHDRAWAL, or None."""
        result = await self.session.execute(
            select(Transaction).where(
                Transaction.initiated_by_user_id == user_id,
                Transaction.type == TransactionType.WITHDRAWAL,
                Transaction.status.in_(
                    [TransactionStatus.PENDING, TransactionStatus.PROCESSING]
                ),
            )
        )
        return result.scalar_one_or_none()
