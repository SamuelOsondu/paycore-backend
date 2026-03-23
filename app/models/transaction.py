import enum
import uuid
from decimal import Decimal
from typing import Optional

from sqlalchemy import Boolean, Enum, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class TransactionType(str, enum.Enum):
    FUNDING = "funding"
    TRANSFER = "transfer"
    MERCHANT_PAYMENT = "merchant_payment"
    WITHDRAWAL = "withdrawal"
    REVERSAL = "reversal"


class TransactionStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    REVERSED = "reversed"


# Valid state transitions.  Used by TransactionRepository.update_status.
VALID_TRANSITIONS: dict[TransactionStatus, frozenset[TransactionStatus]] = {
    TransactionStatus.PENDING: frozenset({TransactionStatus.PROCESSING}),
    TransactionStatus.PROCESSING: frozenset(
        {TransactionStatus.COMPLETED, TransactionStatus.FAILED}
    ),
    TransactionStatus.COMPLETED: frozenset({TransactionStatus.REVERSED}),
    TransactionStatus.FAILED: frozenset(),
    TransactionStatus.REVERSED: frozenset(),
}


class Transaction(Base, TimestampMixin):
    """
    Immutable financial event record.

    Rules
    -----
    - Never soft-deleted.  Financial records are permanent.
    - `reference` is platform-generated, unique, prefixed `txn_`.
    - `idempotency_key` prevents double-submission from callers.
    - `status` follows a strict state machine (see VALID_TRANSITIONS).
    - `extra_data` maps to the `metadata` DB column (avoiding shadowing
      SQLAlchemy's own Base.metadata class attribute).
    - `provider_reference` is internal — never returned in API responses.
    """

    __tablename__ = "transactions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    reference: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False, index=True
    )
    type: Mapped[TransactionType] = mapped_column(
        Enum(TransactionType, name="transactiontype", create_type=False, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
        index=True,
    )
    status: Mapped[TransactionStatus] = mapped_column(
        Enum(TransactionStatus, name="transactionstatus", create_type=False, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
        default=TransactionStatus.PENDING,
        index=True,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="NGN"
    )
    source_wallet_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("wallets.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    destination_wallet_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("wallets.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    initiated_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    # Internal Paystack reference — not exposed in user-facing API responses.
    provider_reference: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True, index=True
    )
    idempotency_key: Mapped[Optional[str]] = mapped_column(
        String(255), unique=True, nullable=True, index=True
    )
    # Mapped to `metadata` DB column; attribute renamed to avoid shadowing
    # SQLAlchemy's Base.metadata class attribute.
    extra_data: Mapped[Optional[dict]] = mapped_column(
        "metadata", JSONB, nullable=True, default=None
    )
    failure_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    risk_flagged: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    risk_flag_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
