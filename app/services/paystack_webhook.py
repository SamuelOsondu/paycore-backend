"""
PaystackWebhookService — signature verification and inbound event processing.

``verify_signature`` is a static method called synchronously in the HTTP layer
before any async DB work.

``process_charge_success`` and ``process_transfer_result`` are async methods
invoked from the Celery task via ``asyncio.run()``, which keeps the business
logic fully testable with the project's async test infrastructure.
"""

import hashlib
import hmac
import logging
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.audit_log import ActorType
from app.models.ledger_entry import EntryType, LedgerEntry
from app.models.transaction import TransactionStatus
from app.repositories.transaction import TransactionRepository
from app.repositories.wallet import WalletRepository

logger = logging.getLogger(__name__)


class PaystackWebhookService:
    """
    Handles inbound Paystack webhook events.

    Signature verification is performed synchronously in the HTTP endpoint
    before any DB interaction so that malformed or unauthenticated requests
    are rejected immediately.

    DB mutations are async so they run inside the project's AsyncSession
    infrastructure, keeping the code testable without a separate sync DB setup.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._txn_repo = TransactionRepository(session)
        self._wallet_repo = WalletRepository(session)

    # ── Signature verification ─────────────────────────────────────────────────

    @staticmethod
    def verify_signature(raw_body: bytes, signature: str) -> bool:
        """
        Verify the HMAC-SHA512 signature supplied by Paystack.

        Paystack computes ``HMAC-SHA512(raw_body, PAYSTACK_WEBHOOK_SECRET)``
        and sends the hex digest as the ``X-Paystack-Signature`` request header.

        Returns False (reject) if:
        - ``PAYSTACK_WEBHOOK_SECRET`` is not configured.
        - The provided signature does not match.
        """
        webhook_secret = settings.PAYSTACK_WEBHOOK_SECRET
        if not webhook_secret:
            logger.warning(
                "PAYSTACK_WEBHOOK_SECRET not configured — rejecting webhook."
            )
            return False
        expected = hmac.new(
            webhook_secret.encode(),
            raw_body,
            hashlib.sha512,
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    # ── Event handlers ─────────────────────────────────────────────────────────

    async def process_charge_success(self, data: dict) -> None:
        """
        Handle a ``charge.success`` event from Paystack.

        Flow
        ----
        1. Resolve the PENDING transaction by ``provider_reference``.
        2. Idempotency guard — skip silently if already COMPLETED.
        3. Transition PENDING → PROCESSING.
        4. Lock destination wallet; credit balance.
        5. Write a single CREDIT ledger entry.
           (Funding is an external inflow — there is no debit wallet to record.)
        6. Transition PROCESSING → COMPLETED.
        7. Commit.
        """
        reference: str = data.get("reference", "")
        if not reference:
            logger.error(
                "charge.success: missing 'reference' field in data: %s", data
            )
            return

        # Amount in kobo → NGN (Decimal to avoid float precision issues)
        amount_kobo: int = data.get("amount", 0)
        amount = Decimal(str(amount_kobo)) / 100

        # 1. Resolve transaction by provider_reference
        txn = await self._txn_repo.get_by_provider_reference(reference)
        if txn is None:
            logger.warning(
                "charge.success: no transaction found for provider_reference=%s",
                reference,
            )
            return

        # 2. Idempotency guard
        if txn.status == TransactionStatus.COMPLETED:
            logger.info(
                "charge.success: txn %s already COMPLETED — idempotent skip", txn.id
            )
            return

        if txn.status != TransactionStatus.PENDING:
            logger.warning(
                "charge.success: txn %s has unexpected status '%s' — skipping",
                txn.id,
                txn.status,
            )
            return

        # 3. PENDING → PROCESSING
        await self._txn_repo.update_status(txn, TransactionStatus.PROCESSING)

        # 4. Lock destination wallet and credit balance
        if txn.destination_wallet_id is None:
            logger.error(
                "charge.success: txn %s has no destination_wallet_id — cannot credit",
                txn.id,
            )
            return

        wallet = await self._wallet_repo.lock_for_update(txn.destination_wallet_id)
        if wallet is None:
            logger.error(
                "charge.success: destination wallet not found for txn %s", txn.id
            )
            return

        new_balance = wallet.balance + amount
        await self._wallet_repo.update_balance(wallet, new_balance)

        # 5. Single CREDIT ledger entry (external funding — no debit wallet)
        entry = LedgerEntry(
            transaction_id=txn.id,
            wallet_id=wallet.id,
            entry_type=EntryType.CREDIT,
            amount=amount,
            currency=txn.currency,
            balance_after=new_balance,
        )
        self.session.add(entry)
        await self.session.flush()

        # 6. PROCESSING → COMPLETED
        await self._txn_repo.update_status(txn, TransactionStatus.COMPLETED)

        # 7. Commit
        await self.session.commit()

        logger.info(
            "charge.success: wallet %s credited %s NGN; txn %s COMPLETED",
            wallet.id,
            amount,
            txn.id,
        )

        # Audit log — fire-and-forget after commit
        from app.services.audit import AuditService
        await AuditService(self.session).log(
            actor_id=txn.initiated_by_user_id,
            actor_type=ActorType.USER,
            action="wallet.funded",
            target_type="transaction",
            target_id=txn.id,
            metadata={"amount": str(amount), "currency": txn.currency},
        )

    async def process_transfer_result(self, event_type: str, data: dict) -> None:
        """
        Handle transfer.success / transfer.failed / transfer.reversed events.

        Delegates to WithdrawalService to update the withdrawal transaction
        and wallet balance as appropriate.
        """
        reference: str = data.get("reference", "")
        transfer_code: str = data.get("transfer_code", "")

        if not reference:
            logger.warning(
                "process_transfer_result: missing 'reference' in data for "
                "event '%s': %s",
                event_type,
                data,
            )
            return

        from app.services.withdrawal import WithdrawalService

        withdrawal_service = WithdrawalService(self.session)

        if event_type == "transfer.success":
            await withdrawal_service.process_payout_success(
                reference=reference, transfer_code=transfer_code
            )
        elif event_type in ("transfer.failed", "transfer.reversed"):
            await withdrawal_service.process_payout_failure(
                reference=reference, transfer_code=transfer_code
            )
        else:
            logger.info(
                "process_transfer_result: unhandled event '%s' — ignoring",
                event_type,
            )
