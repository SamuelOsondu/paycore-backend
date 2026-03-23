import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Enum, ForeignKey, SmallInteger, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class WebhookDeliveryStatus(str, enum.Enum):
    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"


class WebhookDelivery(Base, TimestampMixin):
    """
    Tracks a single outgoing webhook delivery attempt to a merchant endpoint.

    Immutable audit record — no SoftDeleteMixin.
    Status lifecycle: PENDING → DELIVERED (success) | FAILED (max retries exhausted).
    Retry schedule enforced by the Outgoing Webhooks component.
    """

    __tablename__ = "webhook_deliveries"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("transactions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[WebhookDeliveryStatus] = mapped_column(
        Enum(WebhookDeliveryStatus, name="webhookdeliverystatus", create_type=False, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
        default=WebhookDeliveryStatus.PENDING,
        index=True,
    )
    # Number of delivery attempts made so far
    attempt_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0
    )
    # When the next retry should be attempted; NULL means "enqueue immediately"
    next_retry_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    # HTTP status code from the most recent delivery attempt
    last_response_code: Mapped[Optional[int]] = mapped_column(
        SmallInteger, nullable=True
    )
    # Error message or exception from the most recent failed attempt
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
