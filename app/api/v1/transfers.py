from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.response import success_response
from app.models.user import User
from app.schemas.common import ApiResponse
from app.schemas.transfer import TransferOut, TransferRequest
from app.services.transfer import TransferService

router = APIRouter(prefix="/transfers", tags=["Transfers"])


@router.post(
    "",
    response_model=ApiResponse[TransferOut],
    status_code=status.HTTP_201_CREATED,
    summary="Initiate a wallet-to-wallet transfer",
)
async def initiate_transfer(
    body: TransferRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Transfer funds from the authenticated user's wallet to another user's wallet.
    The sender is resolved from the JWT — the source wallet cannot be spoofed.
    """
    service = TransferService(db)
    txn = await service.initiate_transfer(
        sender=current_user,
        recipient_user_id=body.recipient_user_id,
        recipient_email=body.recipient_email,
        amount=body.amount,
        idempotency_key=body.idempotency_key,
    )
    return success_response(
        data=TransferOut.model_validate(txn),
        message="Transfer completed successfully.",
    )
