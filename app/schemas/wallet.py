import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class WalletOut(BaseModel):
    """Public wallet representation returned to authenticated users."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    currency: str
    balance: Decimal
    is_active: bool
    created_at: datetime
