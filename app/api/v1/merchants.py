import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.response import success_response
from app.models.user import User
from app.schemas.common import ApiResponse
from app.schemas.merchant import (
    CreateMerchantRequest,
    MerchantCreatedOut,
    MerchantOut,
    UpdateWebhookRequest,
)
from app.schemas.merchant_payment import MerchantPaymentRequest
from app.schemas.transaction import TransactionOut
from app.services.merchant import MerchantService
from app.services.merchant_payment import MerchantPaymentService

router = APIRouter(prefix="/merchants", tags=["Merchants"])


@router.post(
    "",
    response_model=ApiResponse[MerchantCreatedOut],
    status_code=status.HTTP_201_CREATED,
    summary="Create merchant profile",
)
async def create_merchant(
    body: CreateMerchantRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Promote the authenticated user to a merchant.  Creates a merchant profile
    and ensures a wallet exists for the merchant.  The raw API key is returned
    once — store it immediately as it cannot be retrieved again.
    """
    service = MerchantService(db)
    merchant, raw_key = await service.create_merchant(
        current_user, business_name=body.business_name
    )
    out_data = MerchantOut.model_validate(merchant).model_dump()
    out = MerchantCreatedOut(**out_data, api_key=raw_key)
    return success_response(data=out, message="Merchant profile created.")


@router.get(
    "/me",
    response_model=ApiResponse[MerchantOut],
    summary="Get own merchant profile",
)
async def get_merchant_profile(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    service = MerchantService(db)
    merchant = await service.get_merchant_profile(current_user.id)
    return success_response(
        data=MerchantOut.model_validate(merchant),
        message="Merchant profile retrieved.",
    )


@router.post(
    "/me/api-key",
    response_model=ApiResponse[MerchantCreatedOut],
    summary="Rotate API key",
)
async def rotate_api_key(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Invalidate the current API key and issue a new one.  The new raw key is
    returned once — the old key is invalid immediately after this call commits.
    """
    service = MerchantService(db)
    merchant, raw_key = await service.rotate_api_key(current_user.id)
    out_data = MerchantOut.model_validate(merchant).model_dump()
    out = MerchantCreatedOut(**out_data, api_key=raw_key)
    return success_response(data=out, message="API key rotated successfully.")


@router.patch(
    "/me/webhook",
    response_model=ApiResponse[MerchantOut],
    summary="Update webhook configuration",
)
async def update_webhook(
    body: UpdateWebhookRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Update the webhook delivery URL and/or regenerate the HMAC signing secret.
    Send `regenerate_secret: true` to issue a new webhook secret.
    """
    service = MerchantService(db)
    merchant = await service.update_webhook_config(
        current_user.id,
        webhook_url=body.webhook_url,
        regenerate_secret=body.regenerate_secret,
    )
    return success_response(
        data=MerchantOut.model_validate(merchant),
        message="Webhook configuration updated.",
    )


@router.post(
    "/{merchant_id}/pay",
    response_model=ApiResponse[TransactionOut],
    status_code=status.HTTP_201_CREATED,
    summary="Pay a merchant from wallet",
)
async def pay_merchant(
    merchant_id: uuid.UUID,
    body: MerchantPaymentRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Debit the authenticated user's wallet and credit the merchant's wallet.
    A webhook notification is queued to the merchant after the payment commits.
    The payer is resolved from the JWT — cannot be spoofed via the request body.
    """
    service = MerchantPaymentService(db)
    txn = await service.initiate_payment(
        payer=current_user,
        merchant_id=merchant_id,
        amount=body.amount,
        idempotency_key=body.idempotency_key,
    )
    return success_response(
        data=TransactionOut.model_validate(txn),
        message="Payment completed successfully.",
    )
