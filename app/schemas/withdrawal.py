import uuid
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class WithdrawalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bank_account_id: uuid.UUID
    amount: Decimal = Field(
        gt=0,
        decimal_places=2,
        description="Amount to withdraw in NGN.",
    )
