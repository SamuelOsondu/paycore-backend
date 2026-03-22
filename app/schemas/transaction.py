import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict

from app.models.transaction import TransactionStatus, TransactionType
from app.schemas.ledger import LedgerEntryOut


class TransactionOut(BaseModel):
    """
    Public representation of a transaction returned to the owning user.

    Notes
    -----
    - `provider_reference` is intentionally excluded — it is an internal
      Paystack reference never surfaced to end users.
    - `extra_data` corresponds to the `metadata` JSONB column on the ORM model.
      The attribute was renamed to avoid shadowing SQLAlchemy's Base.metadata.
    """

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
    extra_data: Optional[dict]
    failure_reason: Optional[str]
    created_at: datetime
    updated_at: datetime


class TransactionAdminOut(BaseModel):
    """
    Admin view of a transaction — includes internal fields not safe for end users.

    Extends the user-facing schema with:
    - ``provider_reference``  — Paystack's reference for this transaction
    - ``risk_flagged``        — whether the fraud engine flagged this transaction
    - ``risk_flag_reason``    — human-readable reason for the flag
    """

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
    provider_reference: Optional[str]
    idempotency_key: Optional[str]
    extra_data: Optional[dict]
    failure_reason: Optional[str]
    risk_flagged: bool
    risk_flag_reason: Optional[str]
    created_at: datetime
    updated_at: datetime


class TransactionDetailAdminOut(TransactionAdminOut):
    """
    Full admin transaction detail — TransactionAdminOut plus associated
    double-entry ledger records for complete financial audit.
    """

    ledger_entries: list[LedgerEntryOut] = []
