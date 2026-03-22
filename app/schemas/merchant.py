import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class CreateMerchantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    business_name: str = Field(min_length=2, max_length=255)


class UpdateWebhookRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # None means "leave the current URL unchanged"
    webhook_url: Optional[str] = Field(default=None, max_length=500)
    regenerate_secret: bool = False


class MerchantOut(BaseModel):
    """Safe public representation — never includes the API key hash."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    business_name: str
    api_key_prefix: str
    webhook_url: Optional[str]
    # Included so merchants can configure their receiving system to verify
    # the HMAC signature on incoming webhook deliveries
    webhook_secret: Optional[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime


class MerchantCreatedOut(MerchantOut):
    """
    Returned on merchant creation and API key rotation only.
    api_key is the raw key — shown once and never retrievable again.
    """

    api_key: str
