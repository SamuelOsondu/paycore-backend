from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.response import success_response
from app.models.user import User
from app.schemas.common import ApiResponse
from app.schemas.kyc import KYCSubmissionOut
from app.services.kyc import KYCService

router = APIRouter(prefix="/kyc", tags=["KYC"])


@router.post(
    "/submit",
    response_model=ApiResponse[KYCSubmissionOut],
    status_code=201,
    summary="Submit a KYC document for tier upgrade",
)
async def submit_kyc(
    target_tier: int = Form(..., ge=1, le=2, description="KYC tier being applied for."),
    document: UploadFile = File(..., description="Identity document (JPEG, PNG, or PDF, max 5 MB)."),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Submit an identity document to upgrade KYC tier.

    - Must be at Tier N-1 to apply for Tier N.
    - Only one active (PENDING or APPROVED) submission per tier is permitted.
    - Re-submission is allowed after a prior submission is REJECTED.
    - Supported formats: JPEG, PNG, PDF (validated from file bytes, not extension).
    - Maximum file size: 5 MB.
    """
    file_bytes = await document.read()
    service = KYCService(db)
    submission = await service.submit_kyc(
        current_user,
        target_tier=target_tier,
        file_bytes=file_bytes,
        filename=document.filename or "document",
    )
    return success_response(
        data=KYCSubmissionOut.model_validate(submission),
        message="KYC submission received and is under review.",
    )


@router.get(
    "/me",
    response_model=ApiResponse[KYCSubmissionOut],
    summary="Get your most recent KYC submission",
)
async def get_my_kyc(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Return the most recently created KYC submission for the authenticated user.
    Returns 404 if no submission has been made.
    """
    service = KYCService(db)
    submission = await service.get_my_submission(current_user.id)
    return success_response(
        data=KYCSubmissionOut.model_validate(submission),
        message="KYC submission retrieved.",
    )
