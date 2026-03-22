"""
WalletFundingService — initiates wallet funding via Paystack.

Flow
----
1. Assert wallet exists and is active.
2. Validate amount >= 100 NGN.
3. Idempotency early-return if key already used.
4. Call Paystack ``initialize_transaction`` FIRST.
   Spec: no transaction record is created if Paystack is unavailable.
5. Create a PENDING FUNDING transaction, storing the Paystack payment URL
   in ``extra_data`` so idempotent re-requests can return it.
6. Commit and return the payment URL to the caller.
"""

import logging
import uuid
from decimal import Decimal
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ExternalServiceError, ForbiddenError, ValidationError
from app.integrations.paystack import PaystackClient
from app.models.transaction import TransactionStatus, TransactionType
from app.models.user import User
from app.repositories.transaction import TransactionRepository
from app.repositories.wallet import WalletRepository
from app.schemas.wallet_funding import WalletFundingOut

logger = logging.getLogger(__name__)

MINIMUM_FUNDING_NGN = Decimal("100.00")


class WalletFundingService:

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._txn_repo = TransactionRepository(session)
        self._wallet_repo = WalletRepository(session)

    async def initiate_funding(
        self,
        user: User,
        *,
        amount: Decimal,
        idempotency_key: Optional[str] = None,
    ) -> WalletFundingOut:
        """
        Initialise a Paystack payment and create a PENDING FUNDING transaction.

        Parameters
        ----------
        user:
            The authenticated user requesting the funding.
        amount:
            Amount in NGN (minimum 100.00).
        idempotency_key:
            Optional client-supplied key. If a previous request with the same
            key succeeded, the original transaction and payment URL are returned
            without calling Paystack again.

        Returns
        -------
        WalletFundingOut with transaction_id, reference, payment_url, amount, currency.

        Raises
        ------
        ForbiddenError         – wallet missing or deactivated.
        ValidationError        – amount below minimum (BELOW_MINIMUM_AMOUNT).
        ExternalServiceError   – Paystack unreachable (503); no DB record written.
        """
        # 1. Wallet must exist and be active
        wallet = await self._wallet_repo.get_by_user_id(user.id)
        if wallet is None:
            raise ForbiddenError("No wallet found for this user.")
        if not wallet.is_active:
            raise ForbiddenError("Wallet is deactivated. Please contact support.")

        # 2. Minimum amount guard
        if amount < MINIMUM_FUNDING_NGN:
            raise ValidationError(
                f"Minimum funding amount is {MINIMUM_FUNDING_NGN} NGN.",
                error_code="BELOW_MINIMUM_AMOUNT",
            )

        # 3. Idempotency early-return
        if idempotency_key:
            existing = await self._txn_repo.get_by_idempotency_key(idempotency_key)
            if existing is not None:
                payment_url = (
                    existing.extra_data.get("payment_url", "")
                    if existing.extra_data
                    else ""
                )
                logger.info(
                    "initiate_funding: idempotent return for key=%s, txn=%s",
                    idempotency_key,
                    existing.id,
                )
                return WalletFundingOut(
                    transaction_id=existing.id,
                    reference=existing.reference,
                    payment_url=payment_url,
                    amount=existing.amount,
                    currency=existing.currency,
                )

        # 4. Generate reference and call Paystack FIRST.
        #    If Paystack is down, ExternalServiceError propagates and no DB row
        #    is written (spec requirement).
        reference = f"txn_{uuid.uuid4().hex}"
        client = PaystackClient()
        amount_kobo = int(amount * 100)
        paystack_data = await client.initialize_transaction(
            email=user.email,
            amount_kobo=amount_kobo,
            reference=reference,
        )
        payment_url: str = paystack_data.get("authorization_url", "")
        paystack_reference: str = paystack_data.get("reference", reference)

        # 5. Create PENDING FUNDING transaction
        txn = await self._txn_repo.create(
            reference=reference,
            type=TransactionType.FUNDING,
            amount=amount,
            initiated_by_user_id=user.id,
            status=TransactionStatus.PENDING,
            destination_wallet_id=wallet.id,
            provider_reference=paystack_reference,
            idempotency_key=idempotency_key,
            extra_data={"payment_url": payment_url},
        )
        await self.session.commit()

        logger.info(
            "Wallet funding initiated: user=%s amount=%s NGN ref=%s",
            user.id,
            amount,
            reference,
        )

        return WalletFundingOut(
            transaction_id=txn.id,
            reference=txn.reference,
            payment_url=payment_url,
            amount=txn.amount,
            currency=txn.currency,
        )
