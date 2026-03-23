import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Enum, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.models.base import Base


class ActorType(str, enum.Enum):
    USER = "user"
    SYSTEM = "system"
    ADMIN = "admin"


class AuditLog(Base):
    """
    Immutable audit trail entry.

    Never updated or deleted — append-only table.
    No TimestampMixin (only created_at needed; updated_at would imply mutability).
    No SoftDeleteMixin (regulatory compliance record).
    """

    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # The user, admin, or system that triggered the action.
    # NULL for fully automated system events with no human actor.
    actor_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    actor_type: Mapped[ActorType] = mapped_column(
        Enum(ActorType, name="actortype", create_type=False, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
    )
    # Dot-separated action name, e.g. "kyc.approved", "transfer.completed"
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    # The type of the primary resource affected, e.g. "transaction", "kyc_submission"
    target_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    # Primary key of the affected resource
    target_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    # Arbitrary JSON context — amounts, before/after tiers, rejection reasons, etc.
    # Never stores passwords, raw API keys, or other secrets.
    metadata_: Mapped[Optional[dict]] = mapped_column(
        "metadata", JSONB, nullable=True
    )
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )
