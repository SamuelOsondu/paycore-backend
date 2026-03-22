import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.kyc_submission import KYCStatus


class KYCSubmitRequest(BaseModel):
    """User-submitted KYC request body (paired with a file upload in multipart form)."""

    model_config = ConfigDict(extra="forbid")

    target_tier: int = Field(
        ge=1, le=2, description="KYC tier being applied for (1 or 2)."
    )


class KYCRejectRequest(BaseModel):
    """Admin rejection request body."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(
        min_length=10,
        max_length=500,
        description="Reason for rejection shown to the user.",
    )


class KYCSubmissionOut(BaseModel):
    """User-facing view of a KYC submission. Document S3 key is never included."""

    model_config = ConfigDict(from_attributes=True, use_enum_values=True)

    id: uuid.UUID
    user_id: uuid.UUID
    requested_tier: int
    status: KYCStatus
    rejection_reason: Optional[str]
    reviewed_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime


class KYCSubmissionAdminOut(KYCSubmissionOut):
    """
    Admin-facing view of a KYC submission.
    Includes reviewer info and a short-lived presigned document URL.
    `document_url` is populated by the service at request time — not stored in DB.
    """

    reviewer_id: Optional[uuid.UUID]
    document_url: Optional[str] = None
