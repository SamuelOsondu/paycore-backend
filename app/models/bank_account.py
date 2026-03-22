import uuid
from typing import Optional

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, SoftDeleteMixin, TimestampMixin


class BankAccount(Base, TimestampMixin, SoftDeleteMixin):
    """
    A user's registered Nigerian bank account for withdrawals.

    Rules
    -----
    - Soft-deleted (never hard-deleted) — financial audit trail.
    - ``paystack_recipient_code`` is set on first withdrawal with this account
      (created lazily so account addition doesn't require a Paystack call).
    - ``paystack_recipient_code`` is never surfaced in user-facing API responses.
    - A user may have multiple bank accounts; ``is_default`` marks the preferred one.
    - First account added automatically becomes the default.
    """

    __tablename__ = "bank_accounts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    account_name: Mapped[str] = mapped_column(String(255), nullable=False)
    account_number: Mapped[str] = mapped_column(String(20), nullable=False)
    bank_code: Mapped[str] = mapped_column(String(20), nullable=False)
    bank_name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Stored lazily — set when the first withdrawal to this account is initiated.
    # Never exposed in user-facing API responses.
    paystack_recipient_code: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True
    )
