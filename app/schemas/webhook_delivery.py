import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict

from app.models.webhook_delivery import WebhookDeliveryStatus


class WebhookDeliveryOut(BaseModel):
    """
    Public representation of a webhook delivery record.
    Used in the admin list endpoint.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    merchant_id: uuid.UUID
    transaction_id: uuid.UUID
    event_type: str
    status: WebhookDeliveryStatus
    attempt_count: int
    next_retry_at: Optional[datetime]
    last_response_code: Optional[int]
    last_error: Optional[str]
    created_at: datetime
