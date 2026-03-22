from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class MerchantPaymentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: Decimal = Field(gt=0, decimal_places=2)
    idempotency_key: Optional[str] = Field(default=None, max_length=255)
