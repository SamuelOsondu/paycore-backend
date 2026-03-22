"""
Paystack API client.

All amounts sent to Paystack must be in kobo (1 NGN = 100 kobo).
This client is a thin async HTTP wrapper — all business logic lives in the
service layer.
"""

import logging

import httpx

from app.core.config import settings
from app.core.exceptions import ExternalServiceError

logger = logging.getLogger(__name__)

PAYSTACK_BASE_URL = "https://api.paystack.co"
HTTP_TIMEOUT_SECONDS = 10


class PaystackClient:
    """
    Async HTTP wrapper for the Paystack REST API.

    Each public method maps 1-to-1 to a Paystack endpoint and returns the
    ``data`` sub-dict from the Paystack response envelope on success.

    Raises
    ------
    ExternalServiceError
        If Paystack responds with a non-2xx status or is unreachable.
    """

    def __init__(self) -> None:
        self._headers = {
            "Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}",
            "Content-Type": "application/json",
        }

    async def initialize_transaction(
        self,
        *,
        email: str,
        amount_kobo: int,
        reference: str,
    ) -> dict:
        """
        Initialize a Paystack payment transaction.

        Returns the Paystack ``data`` dict containing ``authorization_url``,
        ``access_code``, and ``reference``.
        """
        return await self._post(
            "/transaction/initialize",
            {"email": email, "amount": amount_kobo, "reference": reference},
        )

    async def verify_transaction(self, reference: str) -> dict:
        """
        Verify a transaction by its platform reference.

        Returns Paystack transaction data (status, amount, customer, etc.).
        Used by the reconciliation job and on-demand verification.
        """
        return await self._get(f"/transaction/verify/{reference}")

    async def create_transfer_recipient(
        self,
        *,
        name: str,
        account_number: str,
        bank_code: str,
        currency: str = "NGN",
    ) -> dict:
        """
        Register a Nigerian bank account (NUBAN) as a transfer recipient.

        Returns Paystack recipient data including ``recipient_code``.
        """
        return await self._post(
            "/transferrecipient",
            {
                "type": "nuban",
                "name": name,
                "account_number": account_number,
                "bank_code": bank_code,
                "currency": currency,
            },
        )

    async def initiate_transfer(
        self,
        *,
        amount_kobo: int,
        recipient_code: str,
        reference: str,
        reason: str = "",
    ) -> dict:
        """
        Initiate a payout transfer to a registered recipient.

        Returns Paystack transfer data including ``transfer_code``.
        """
        return await self._post(
            "/transfer",
            {
                "source": "balance",
                "amount": amount_kobo,
                "recipient": recipient_code,
                "reference": reference,
                "reason": reason,
            },
        )

    async def verify_transfer(self, transfer_code: str) -> dict:
        """Verify a transfer by its transfer_code."""
        return await self._get(f"/transfer/{transfer_code}")

    async def resolve_account(
        self,
        *,
        account_number: str,
        bank_code: str,
    ) -> dict:
        """
        Verify a Nigerian bank account (NUBAN) and resolve its registered name.

        Returns Paystack data dict containing ``account_name`` and
        ``account_number`` if the account is valid.

        Raises ExternalServiceError if Paystack is unavailable or the account
        cannot be resolved.
        """
        return await self._get(
            f"/bank/resolve?account_number={account_number}&bank_code={bank_code}"
        )

    # ── Internal HTTP helpers ──────────────────────────────────────────────────

    async def _post(self, path: str, payload: dict) -> dict:
        try:
            async with httpx.AsyncClient(
                base_url=PAYSTACK_BASE_URL,
                headers=self._headers,
                timeout=HTTP_TIMEOUT_SECONDS,
                follow_redirects=False,
            ) as client:
                resp = await client.post(path, json=payload)
            if not resp.is_success:
                logger.error(
                    "Paystack POST %s failed: HTTP %s — %s",
                    path,
                    resp.status_code,
                    resp.text[:200],
                )
                raise ExternalServiceError("Paystack")
            return resp.json().get("data", {})
        except httpx.HTTPError as exc:
            logger.exception("Paystack POST %s network error: %s", path, exc)
            raise ExternalServiceError("Paystack")

    async def _get(self, path: str) -> dict:
        try:
            async with httpx.AsyncClient(
                base_url=PAYSTACK_BASE_URL,
                headers=self._headers,
                timeout=HTTP_TIMEOUT_SECONDS,
                follow_redirects=False,
            ) as client:
                resp = await client.get(path)
            if not resp.is_success:
                logger.error(
                    "Paystack GET %s failed: HTTP %s — %s",
                    path,
                    resp.status_code,
                    resp.text[:200],
                )
                raise ExternalServiceError("Paystack")
            return resp.json().get("data", {})
        except httpx.HTTPError as exc:
            logger.exception("Paystack GET %s network error: %s", path, exc)
            raise ExternalServiceError("Paystack")
