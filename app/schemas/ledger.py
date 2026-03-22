import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.models.ledger_entry import EntryType


class LedgerEntryOut(BaseModel):
    """
    Serialisable view of a single ledger entry.
    Intended for admin inspection; not exposed to regular users.
    """

    model_config = ConfigDict(from_attributes=True, use_enum_values=True)

    id: uuid.UUID
    transaction_id: uuid.UUID
    wallet_id: uuid.UUID
    entry_type: EntryType
    amount: Decimal
    currency: str
    balance_after: Decimal
    created_at: datetime
