import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User, UserRole
from app.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def create(
        self,
        *,
        email: str,
        hashed_password: str,
        full_name: str,
        phone: str | None = None,
        role: UserRole = UserRole.USER,
    ) -> User:
        user = User(
            email=email.lower().strip(),
            hashed_password=hashed_password,
            full_name=full_name,
            phone=phone,
            role=role,
        )
        self.session.add(user)
        await self.session.flush()
        await self.session.refresh(user)
        return user

    async def get_by_id(self, user_id: uuid.UUID) -> Optional[User]:
        result = await self.session.execute(
            select(User).where(User.id == user_id, User.deleted_at.is_(None))
        )
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> Optional[User]:
        result = await self.session.execute(
            select(User).where(
                User.email == email.lower().strip(),
                User.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def get_by_phone(self, phone: str) -> Optional[User]:
        result = await self.session.execute(
            select(User).where(User.phone == phone, User.deleted_at.is_(None))
        )
        return result.scalar_one_or_none()

    async def update_profile(
        self,
        user: User,
        *,
        full_name: str | None = None,
        phone: str | None = None,
    ) -> User:
        """Update only safe profile fields. Role and kyc_tier are never touched here."""
        if full_name is not None:
            user.full_name = full_name
        if phone is not None:
            user.phone = phone
        await self.session.flush()
        await self.session.refresh(user)
        return user

    async def update_kyc_tier(self, user: User, tier: int) -> User:
        """Called only by the KYC approval flow. Not exposed via profile update."""
        user.kyc_tier = tier
        await self.session.flush()
        await self.session.refresh(user)
        return user

    async def set_active(self, user: User, *, active: bool) -> User:
        user.is_active = active
        await self.session.flush()
        await self.session.refresh(user)
        return user

    async def soft_delete(self, user: User) -> None:
        user.soft_delete()
        await self.session.flush()

    async def list_all(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        role: Optional[UserRole] = None,
        kyc_tier: Optional[int] = None,
    ) -> list[User]:
        """
        Paginated list of all non-deleted users, newest first.
        Optionally filtered by role and/or kyc_tier.
        """
        stmt = select(User).where(User.deleted_at.is_(None))
        if role is not None:
            stmt = stmt.where(User.role == role)
        if kyc_tier is not None:
            stmt = stmt.where(User.kyc_tier == kyc_tier)
        stmt = stmt.order_by(User.created_at.desc()).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_all(
        self,
        *,
        role: Optional[UserRole] = None,
        kyc_tier: Optional[int] = None,
    ) -> int:
        """Return total count matching the given filters."""
        stmt = (
            select(func.count())
            .select_from(User)
            .where(User.deleted_at.is_(None))
        )
        if role is not None:
            stmt = stmt.where(User.role == role)
        if kyc_tier is not None:
            stmt = stmt.where(User.kyc_tier == kyc_tier)
        result = await self.session.execute(stmt)
        return result.scalar_one()
