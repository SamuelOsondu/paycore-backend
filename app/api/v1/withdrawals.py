"""
Bank account management and withdrawal endpoints.

Bank accounts
-------------
POST   /bank-accounts          — register a new bank account
GET    /bank-accounts          — list the authenticated user's bank accounts
DELETE /bank-accounts/{id}     — soft-delete a bank account

Withdrawals
-----------
POST   /withdrawals            — initiate a withdrawal to a bank account
GET    /withdrawals/{reference} — fetch withdrawal status by platform reference
"""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.response import success_response
from app.models.user import User
from app.schemas.bank_account import AddBankAccountRequest, BankAccountOut
from app.schemas.common import ApiResponse
from app.schemas.transaction import TransactionOut
from app.schemas.withdrawal import WithdrawalRequest
from app.services.withdrawal import WithdrawalService

router = APIRouter(tags=["Withdrawals"])


# ── Bank accounts ──────────────────────────────────────────────────────────────


@router.post(
    "/bank-accounts",
    response_model=ApiResponse[BankAccountOut],
    status_code=201,
    summary="Register a bank account",
)
async def add_bank_account(
    body: AddBankAccountRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Register a Nigerian bank account for withdrawals.

    The Paystack-verified account name is used if available; otherwise the
    client-supplied name is stored.  The first account added becomes the
    default automatically.

    ``paystack_recipient_code`` is created lazily on first withdrawal —
    it is never returned in API responses.
    """
    service = WithdrawalService(db)
    account = await service.add_bank_account(
        current_user,
        account_name=body.account_name,
        account_number=body.account_number,
        bank_code=body.bank_code,
        bank_name=body.bank_name,
    )
    return success_response(
        data=BankAccountOut.model_validate(account),
        message="Bank account added.",
    )


@router.get(
    "/bank-accounts",
    response_model=ApiResponse[list[BankAccountOut]],
    summary="List registered bank accounts",
)
async def list_bank_accounts(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Return all non-deleted bank accounts registered by the authenticated user."""
    service = WithdrawalService(db)
    accounts = await service.list_bank_accounts(current_user)
    return success_response(
        data=[BankAccountOut.model_validate(a) for a in accounts],
        message="Bank accounts retrieved.",
    )


@router.delete(
    "/bank-accounts/{bank_account_id}",
    response_model=ApiResponse[None],
    status_code=200,
    summary="Remove a bank account",
)
async def remove_bank_account(
    bank_account_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Soft-delete a bank account.

    Returns 404 if not found or not owned by the caller.
    Returns 403 if the account has an active withdrawal in progress.
    """
    service = WithdrawalService(db)
    await service.remove_bank_account(current_user, bank_account_id)
    return success_response(message="Bank account removed.")


# ── Withdrawals ────────────────────────────────────────────────────────────────


@router.post(
    "/withdrawals",
    response_model=ApiResponse[TransactionOut],
    status_code=201,
    summary="Initiate a withdrawal",
)
async def initiate_withdrawal(
    body: WithdrawalRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Initiate a withdrawal from the authenticated user's wallet to a registered
    bank account.

    Business rules
    --------------
    - Requires KYC Tier 2.
    - Amount must not exceed current wallet balance.
    - Only one active (PENDING/PROCESSING) withdrawal allowed at a time.
    - Balance is immediately held (deducted) on initiation.
    - Paystack transfer is dispatched asynchronously via Celery.
    - The ledger DEBIT entry is written when the transfer is confirmed by Paystack.
    - If the transfer fails, the held balance is returned automatically.
    """
    service = WithdrawalService(db)
    txn = await service.initiate_withdrawal(
        current_user,
        bank_account_id=body.bank_account_id,
        amount=body.amount,
    )
    return success_response(
        data=TransactionOut.model_validate(txn),
        message="Withdrawal initiated.",
    )


@router.get(
    "/withdrawals/{reference}",
    response_model=ApiResponse[TransactionOut],
    summary="Get withdrawal status",
)
async def get_withdrawal_status(
    reference: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Fetch the current status of a withdrawal by its platform reference.

    Returns 404 if the reference does not exist or does not belong to the
    authenticated user.
    """
    from app.core.exceptions import NotFoundError
    from app.repositories.transaction import TransactionRepository

    txn_repo = TransactionRepository(db)
    txn = await txn_repo.get_by_reference(reference)
    if txn is None or txn.initiated_by_user_id != current_user.id:
        raise NotFoundError("Withdrawal")

    return success_response(
        data=TransactionOut.model_validate(txn),
        message="Withdrawal retrieved.",
    )
