from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.response import success_response
from app.models.transaction import TransactionStatus, TransactionType
from app.models.user import User
from app.schemas.common import ApiResponse, PaginatedData
from app.schemas.transaction import TransactionOut
from app.services.transaction import TransactionService

router = APIRouter(prefix="/transactions", tags=["Transactions"])


@router.get(
    "",
    response_model=ApiResponse[PaginatedData[TransactionOut]],
    summary="List the authenticated user's transactions",
)
async def list_transactions(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=20, ge=1, le=100, description="Max items per page"),
    offset: int = Query(default=0, ge=0, description="Number of items to skip"),
    type: Optional[TransactionType] = Query(
        default=None, description="Filter by transaction type"
    ),
    status: Optional[TransactionStatus] = Query(
        default=None, description="Filter by transaction status"
    ),
) -> dict:
    """
    Returns all transactions where `initiated_by_user_id` matches the
    current user, sorted by `created_at` DESC.

    Optional query filters: `type` and `status`.
    """
    service = TransactionService(db)
    result = await service.list_transactions(
        current_user.id,
        limit=limit,
        offset=offset,
        type_filter=type,
        status_filter=status,
    )
    return success_response(data=result, message="Transactions retrieved.")


@router.get(
    "/{reference}",
    response_model=ApiResponse[TransactionOut],
    summary="Get a single transaction by reference",
)
async def get_transaction(
    reference: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Fetch a transaction by its `reference` (e.g. `txn_abc123...`).

    Ownership is enforced: returns 404 if the transaction belongs to a
    different user, preventing reference enumeration.
    """
    service = TransactionService(db)
    txn = await service.get_transaction(
        reference, requesting_user_id=current_user.id
    )
    return success_response(
        data=TransactionOut.model_validate(txn),
        message="Transaction retrieved.",
    )
