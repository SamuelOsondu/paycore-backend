import uuid
from typing import Optional

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, SoftDeleteMixin, TimestampMixin


class Merchant(Base, TimestampMixin, SoftDeleteMixin):
    """
    Merchant profile — one per user (UNIQUE on user_id).

    Wallet
    ------
    The merchant's payment wallet is a standard Wallet record keyed by
    user_id.  Lookup: WalletRepository.get_by_user_id(merchant.user_id).

    API Key
    -------
    The raw key is generated once and shown only in creation / rotation
    responses.  Only the bcrypt hash and a short prefix are persisted here.
    """

    __tablename__ = "merchants"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        unique=True,
        nullable=False,
        index=True,
    )
    business_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # bcrypt hash of the raw API key — never returned to clients
    api_key_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    # First 8 chars of the raw key (always "pk_live_") — for display /
    # pre-filter before the expensive bcrypt comparison
    api_key_prefix: Mapped[str] = mapped_column(
        String(16), nullable=False, index=True
    )
    webhook_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    # Random UUID4 used by the platform to HMAC-sign outgoing webhook payloads;
    # the merchant uses this secret to verify received webhook signatures
    webhook_secret: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
