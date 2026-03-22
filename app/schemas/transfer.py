import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.transaction import TransactionStatus, TransactionType


class TransferRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recipient_user_id: Optional[uuid.UUID] = None
    recipient_email: Optional[str] = None
    amount: Decimal = Field(gt=0, decimal_places=2)
    idempotency_key: Optional[str] = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def check_exactly_one_recipient(self) -> "TransferRequest":
        has_id = self.recipient_user_id is not None
        has_email = self.recipient_email is not None
        if has_id == has_email:
            raise ValueError(
                "Provide exactly one of recipient_user_id or recipient_email."
            )
        return self


class TransferOut(BaseModel):
    """Public representation of a completed transfer."""

    model_config = ConfigDict(from_attributes=True, use_enum_values=True)

    id: uuid.UUID
    reference: str
    type: TransactionType
    status: TransactionStatus
    amount: Decimal
    currency: str
    source_wallet_id: Optional[uuid.UUID]
    destination_wallet_id: Optional[uuid.UUID]
    initiated_by_user_id: uuid.UUID
    idempotency_key: Optional[str]
    created_at: datetime
    updated_at: datetime
