import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Enum, ForeignKey, SmallInteger, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class KYCStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class KYCSubmission(Base, TimestampMixin):
    """
    KYC document submission tracking record.

    One PENDING or APPROVED submission per (user, requested_tier) is allowed at a time.
    Rejected submissions may be resubmitted.  Records are never deleted — they form
    the compliance audit trail.  No SoftDeleteMixin.
    """

    __tablename__ = "kyc_submissions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    requested_tier: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    status: Mapped[KYCStatus] = mapped_column(
        Enum(KYCStatus, name="kycstatus", create_type=True),
        nullable=False,
        default=KYCStatus.PENDING,
        index=True,
    )
    # S3 object key — NEVER returned to non-admin users.
    document_key: Mapped[str] = mapped_column(String(500), nullable=False)
    # Populated on review
    reviewer_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    rejection_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
