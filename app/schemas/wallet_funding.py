import uuid
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class WalletFundingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: Decimal = Field(
        gt=0,
        decimal_places=2,
        description="Amount to fund in NGN. Minimum 100.00 NGN.",
    )
    idempotency_key: Optional[str] = Field(default=None, max_length=255)


class WalletFundingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    transaction_id: uuid.UUID
    reference: str
    payment_url: str
    amount: Decimal
    currency: str
