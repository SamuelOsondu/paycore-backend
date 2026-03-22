from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.exceptions import ForbiddenError
from app.core.response import success_response
from app.models.user import User
from app.schemas.common import ApiResponse, PaginatedData
from app.schemas.transaction import TransactionOut
from app.schemas.wallet import WalletOut
from app.schemas.wallet_funding import WalletFundingOut, WalletFundingRequest
from app.services.wallet import WalletService
from app.services.wallet_funding import WalletFundingService

router = APIRouter(prefix="/wallets", tags=["Wallets"])


@router.post(
    "/fund",
    response_model=ApiResponse[WalletFundingOut],
    status_code=201,
    summary="Initiate wallet funding via Paystack",
)
async def fund_wallet(
    body: WalletFundingRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Initialise a Paystack payment to top up the authenticated user's wallet.

    Returns a ``payment_url`` the client should redirect the user to.
    Once the user completes payment on Paystack, a ``charge.success`` webhook
    credits the wallet automatically.

    - Minimum amount: 100 NGN.
    - Supply an ``idempotency_key`` to safely retry without double-creating
      the transaction; the original payment URL is returned.
    - Returns 503 if Paystack is temporarily unavailable.
    """
    service = WalletFundingService(db)
    result = await service.initiate_funding(
        current_user,
        amount=body.amount,
        idempotency_key=body.idempotency_key,
    )
    return success_response(data=result, message="Funding initiated.")


@router.get(
    "/me",
    response_model=ApiResponse[WalletOut],
    summary="Get the authenticated user's wallet",
)
async def get_my_wallet(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Return wallet details (id, currency, balance, is_active) for the
    currently authenticated user.

    A deactivated wallet returns 403 — the user must contact support.
    """
    service = WalletService(db)
    wallet = await service.get_wallet(current_user.id)
    if not wallet.is_active:
        raise ForbiddenError("Wallet is deactivated. Please contact support.")
    return success_response(
        data=WalletOut.model_validate(wallet),
        message="Wallet retrieved.",
    )


@router.get(
    "/me/transactions",
    response_model=ApiResponse[PaginatedData[TransactionOut]],
    summary="Get the authenticated user's transaction history",
)
async def get_my_transactions(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=20, ge=1, le=100, description="Max items per page"),
    offset: int = Query(default=0, ge=0, description="Number of items to skip"),
) -> dict:
    """
    Paginated transaction history for the authenticated user's wallet.

    Returns all transactions where the user's wallet was either the
    source or the destination (i.e. sent and received), ordered by
    `created_at` DESC.
    """
    service = WalletService(db)
    result = await service.get_statement(current_user.id, limit=limit, offset=offset)
    return success_response(data=result, message="Transactions retrieved.")
