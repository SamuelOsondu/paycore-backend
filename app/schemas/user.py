import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, field_validator

from app.models.user import UserRole


class UserOut(BaseModel):
    """Public user representation. Never includes password or deleted_at."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    phone: str | None
    full_name: str
    role: UserRole
    kyc_tier: int
    is_active: bool
    is_email_verified: bool
    created_at: datetime


class UserUpdateRequest(BaseModel):
    """Fields a user may update on their own profile."""

    model_config = ConfigDict(extra="forbid")

    full_name: str | None = None
    phone: str | None = None

    @field_validator("full_name")
    @classmethod
    def full_name_not_empty(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("full_name must not be blank.")
        return v.strip() if v else v

    @field_validator("phone")
    @classmethod
    def phone_format(cls, v: str | None) -> str | None:
        if v is None:
            return v
        cleaned = v.strip()
        # Accept E.164 format: +2348012345678 or local formats
        if not cleaned.replace("+", "").replace(" ", "").isdigit():
            raise ValueError("Invalid phone number format.")
        if len(cleaned) < 7 or len(cleaned) > 20:
            raise ValueError("Phone number must be between 7 and 20 characters.")
        return cleaned
