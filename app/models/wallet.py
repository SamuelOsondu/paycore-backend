import uuid
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, SoftDeleteMixin, TimestampMixin


class Wallet(Base, TimestampMixin, SoftDeleteMixin):
    """
    Financial container for every user (and merchant) on the platform.

    Rules
    -----
    - One wallet per user (UNIQUE on user_id).
    - balance is a maintained field — never computed from ledger on the hot path.
    - balance >= 0 enforced at DB level via CHECK constraint.
    - Balance updates must always be accompanied by ledger entry writes in the
      same transaction.  Use WalletRepository.lock_for_update before mutating.
    """

    __tablename__ = "wallets"
    __table_args__ = (
        CheckConstraint("balance >= 0", name="ck_wallets_balance_non_negative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="NGN"
    )
    balance: Mapped[Decimal] = mapped_column(
        Numeric(20, 2), nullable=False, default=Decimal("0.00")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
