from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.response import success_response
from app.models.user import User
from app.schemas.common import ApiResponse
from app.schemas.user import UserOut, UserUpdateRequest
from app.services.user import UserService

router = APIRouter(prefix="/users", tags=["Users"])


@router.get("/me", response_model=ApiResponse[UserOut])
async def get_my_profile(
    current_user: User = Depends(get_current_user),
) -> dict:
    return success_response(
        data=UserOut.model_validate(current_user),
        message="Profile retrieved.",
    )


@router.patch("/me", response_model=ApiResponse[UserOut])
async def update_my_profile(
    body: UserUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    service = UserService(db)
    updated = await service.update_profile(
        current_user.id,
        full_name=body.full_name,
        phone=body.phone,
    )
    return success_response(
        data=UserOut.model_validate(updated),
        message="Profile updated.",
    )
