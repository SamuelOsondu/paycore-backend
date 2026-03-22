import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.models.user import User
from app.repositories.user import UserRepository


class UserService:

    def __init__(self, session: AsyncSession) -> None:
        self.repo = UserRepository(session)
        self.session = session

    async def get_profile(self, user_id: uuid.UUID) -> User:
        user = await self.repo.get_by_id(user_id)
        if user is None:
            raise NotFoundError("User")
        return user

    async def update_profile(
        self,
        user_id: uuid.UUID,
        *,
        full_name: str | None = None,
        phone: str | None = None,
    ) -> User:
        """
        Update safe profile fields. Raises ConflictError if the phone
        number is already taken by a different user.
        """
        user = await self.repo.get_by_id(user_id)
        if user is None:
            raise NotFoundError("User")

        if phone is not None and phone != user.phone:
            existing = await self.repo.get_by_phone(phone)
            if existing is not None and existing.id != user_id:
                raise ConflictError(
                    "This phone number is already registered.",
                    error_code="PHONE_CONFLICT",
                )

        return await self.repo.update_profile(user, full_name=full_name, phone=phone)

    async def update_kyc_tier(self, user_id: uuid.UUID, tier: int) -> User:
        """Called exclusively by the KYC approval flow — not a user-facing action."""
        user = await self.repo.get_by_id(user_id)
        if user is None:
            raise NotFoundError("User")
        return await self.repo.update_kyc_tier(user, tier)
