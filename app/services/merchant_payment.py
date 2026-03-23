import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal

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
from app.repositories.merchant import MerchantRepository
from app.repositories.transaction import TransactionRepository
from app.repositories.wallet import WalletRepository
from app.services.fraud import FraudService
from app.services.ledger import LedgerService
from app.services.webhook_delivery import WebhookDeliveryService

logger = logging.getLogger(__name__)


class MerchantPaymentService:

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._merchant_repo = MerchantRepository(session)
        self._wallet_repo = WalletRepository(session)
        self._txn_repo = TransactionRepository(session)
        self._ledger = LedgerService(session)
        self._fraud = FraudService(session)

    async def initiate_payment(
        self,
        payer: User,
        *,
        merchant_id: uuid.UUID,
        amount: Decimal,
        idempotency_key: str | None = None,
    ) -> Transaction:
        """
        Debit the payer's wallet and credit the merchant's wallet atomically,
        then queue a webhook notification to the merchant.

        Flow
        ----
        Resolve merchant → self-payment guard → resolve wallets → idempotency
        check → fraud checks → lock wallets (consistent UUID order) →
        balance check → write transaction + ledger entries + update balances
        → commit → post-commit: fraud flag + webhook delivery.

        Raises
        ------
        NotFoundError            – merchant not found
        ForbiddenError           – merchant inactive, or a wallet is inactive
        ValidationError          – payer is the merchant's own account
        InsufficientBalanceError – payer balance < amount
        KYCTierError             – tier 0, or single-transaction limit exceeded
        DailyLimitError          – daily payment volume exceeded
        """
        # Resolve and validate merchant
        merchant = await self._merchant_repo.get_by_id(merchant_id)
        if merchant is None:
            raise NotFoundError("Merchant")
        if not merchant.is_active:
            raise ForbiddenError("Merchant account is inactive.")

        # Self-payment guard
        if payer.id == merchant.user_id:
            raise ValidationError(
                "You cannot pay yourself.", error_code="SELF_PAYMENT"
            )

        # Resolve payer wallet
        payer_wallet = await self._wallet_repo.get_by_user_id(payer.id)
        if payer_wallet is None or not payer_wallet.is_active:
            raise ForbiddenError("Your wallet is inactive.")

        # Resolve merchant wallet
        merchant_wallet = await self._wallet_repo.get_by_user_id(merchant.user_id)
        if merchant_wallet is None or not merchant_wallet.is_active:
            raise ForbiddenError("Merchant wallet is inactive.")

        # Idempotency: return existing transaction if key already processed
        if idempotency_key:
            existing = await self._txn_repo.get_by_idempotency_key(idempotency_key)
            if existing is not None:
                return existing

        # Fraud checks — synchronous, run before any DB writes
        await self._fraud.check_merchant_payment(
            user=payer,
            wallet=payer_wallet,
            merchant_wallet_id=merchant_wallet.id,
            amount=amount,
        )

        # Lock both wallets in consistent UUID order to prevent deadlock
        first_id, second_id = sorted(
            [payer_wallet.id, merchant_wallet.id], key=str
        )
        locked_first = await self._wallet_repo.lock_for_update(first_id)
        locked_second = await self._wallet_repo.lock_for_update(second_id)

        if locked_first is None or locked_second is None:
            raise NotFoundError("Wallet")

        if first_id == payer_wallet.id:
            locked_payer, locked_merchant = locked_first, locked_second
        else:
            locked_payer, locked_merchant = locked_second, locked_first

        # Balance check against the freshly-locked row
        if locked_payer.balance < amount:
            raise InsufficientBalanceError()

        payer_balance_after = locked_payer.balance - amount
        merchant_balance_after = locked_merchant.balance + amount

        # Write transaction record, ledger entries, and balance updates atomically.
        # The idempotency_key column has a DB-level unique constraint, so if two
        # concurrent requests both slip past the pre-check above, the second flush
        # will raise IntegrityError.  We catch it, rollback, and return the already-
        # committed transaction — giving the caller the correct idempotent response.
        try:
            txn = await self._txn_repo.create(
                reference=f"txn_{uuid.uuid4().hex[:12]}",
                type=TransactionType.MERCHANT_PAYMENT,
                status=TransactionStatus.COMPLETED,
                amount=amount,
                currency=locked_payer.currency,
                source_wallet_id=locked_payer.id,
                destination_wallet_id=locked_merchant.id,
                initiated_by_user_id=payer.id,
                idempotency_key=idempotency_key,
            )

            # Write double-entry ledger entries
            await self._ledger.post_double_entry(
                transaction_id=txn.id,
                debit_wallet_id=locked_payer.id,
                credit_wallet_id=locked_merchant.id,
                amount=amount,
                currency=locked_payer.currency,
                debit_balance_after=payer_balance_after,
                credit_balance_after=merchant_balance_after,
            )

            # Update wallet balances
            await self._wallet_repo.update_balance(locked_payer, payer_balance_after)
            await self._wallet_repo.update_balance(locked_merchant, merchant_balance_after)

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

        # ── Post-commit operations (non-blocking, best-effort) ──────────────

        # Async risk flag for large Tier 1 payments
        self._fraud.maybe_flag_merchant_payment(txn.id, payer, amount)

        # Build and enqueue webhook delivery
        payload = {
            "event": "payment.received",
            "data": {
                "transaction_reference": txn.reference,
                "amount": str(amount),
                "currency": locked_payer.currency,
                "payer_id": str(payer.id),
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        try:
            await WebhookDeliveryService(self.session).create_and_enqueue(
                merchant=merchant,
                transaction_id=txn.id,
                event_type="payment.received",
                payload=payload,
            )
        except Exception:
            logger.exception(
                "Webhook delivery setup failed for payment txn %s — "
                "payment is still committed",
                txn.id,
            )

        # Audit log — fire-and-forget after commit
        from app.services.audit import AuditService
        await AuditService(self.session).log(
            actor_id=payer.id,
            actor_type=ActorType.USER,
            action="merchant_payment.completed",
            target_type="transaction",
            target_id=txn.id,
            metadata={
                "merchant_id": str(merchant.id),
                "amount": str(amount),
                "currency": locked_payer.currency,
            },
        )

        return txn
