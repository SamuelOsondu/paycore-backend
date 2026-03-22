import uuid
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.kyc_submission import KYCStatus, KYCSubmission
from app.repositories.base import BaseRepository


class KYCRepository(BaseRepository[KYCSubmission]):

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def create(
        self,
        *,
        submission_id: uuid.UUID,
        user_id: uuid.UUID,
        requested_tier: int,
        document_key: str,
    ) -> KYCSubmission:
        sub = KYCSubmission(
            id=submission_id,
            user_id=user_id,
            requested_tier=requested_tier,
            document_key=document_key,
            status=KYCStatus.PENDING,
        )
        self.session.add(sub)
        await self.session.flush()
        await self.session.refresh(sub)
        return sub

    async def get_by_id(self, submission_id: uuid.UUID) -> Optional[KYCSubmission]:
        result = await self.session.execute(
            select(KYCSubmission).where(KYCSubmission.id == submission_id)
        )
        return result.scalar_one_or_none()

    async def get_latest_for_user(self, user_id: uuid.UUID) -> Optional[KYCSubmission]:
        """Return the most recently created submission for a user, regardless of status."""
        result = await self.session.execute(
            select(KYCSubmission)
            .where(KYCSubmission.user_id == user_id)
            .order_by(KYCSubmission.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_active_for_tier(
        self, user_id: uuid.UUID, requested_tier: int
    ) -> Optional[KYCSubmission]:
        """
        Return a non-rejected submission for this user+tier, if one exists.
        Used to prevent duplicate active submissions.
        LIMIT 1 guards against accidental duplicates reaching the DB.
        """
        result = await self.session.execute(
            select(KYCSubmission)
            .where(
                KYCSubmission.user_id == user_id,
                KYCSubmission.requested_tier == requested_tier,
                KYCSubmission.status != KYCStatus.REJECTED,
            )
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list_by_status(
        self,
        status: Optional[KYCStatus],
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[KYCSubmission], int]:
        """Paginated submissions, optionally filtered by status."""
        base = select(KYCSubmission)
        count_base = select(func.count()).select_from(KYCSubmission)

        if status is not None:
            base = base.where(KYCSubmission.status == status)
            count_base = count_base.where(KYCSubmission.status == status)

        total: int = (await self.session.execute(count_base)).scalar_one()
        rows = (
            await self.session.execute(
                base.order_by(KYCSubmission.created_at.asc()).limit(limit).offset(offset)
            )
        ).scalars().all()

        return list(rows), total
