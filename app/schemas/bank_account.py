import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AddBankAccountRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_name: str = Field(min_length=2, max_length=255)
    account_number: str = Field(
        min_length=6,
        max_length=20,
        pattern=r"^\d+$",
        description="Nigerian NUBAN account number (digits only)",
    )
    bank_code: str = Field(min_length=2, max_length=20)
    bank_name: str = Field(min_length=2, max_length=100)


class BankAccountOut(BaseModel):
    """
    Public view of a bank account.

    ``paystack_recipient_code`` is intentionally excluded — it is an internal
    Paystack reference never returned to clients.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    account_name: str
    account_number: str
    bank_code: str
    bank_name: str
    is_default: bool
    created_at: datetime
