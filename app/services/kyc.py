import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError
from app.integrations.storage import MAX_FILE_SIZE, StorageService, detect_mime_type
from app.models.audit_log import ActorType
from app.models.kyc_submission import KYCStatus, KYCSubmission
from app.models.user import User
from app.repositories.kyc import KYCRepository
from app.schemas.common import PaginatedData
from app.schemas.kyc import KYCSubmissionAdminOut, KYCSubmissionOut

logger = logging.getLogger(__name__)

_ALLOWED_MIME = {"image/jpeg", "image/png", "application/pdf"}


class KYCService:

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._repo = KYCRepository(session)
        self._storage = StorageService()

    async def submit_kyc(
        self,
        user: User,
        target_tier: int,
        file_bytes: bytes,
        filename: str,
    ) -> KYCSubmission:
        """
        Validate the document, upload to S3, and create a PENDING submission record.

        Rules enforced
        --------------
        - File must be ≤ 5 MB and a valid JPEG / PNG / PDF (magic byte check).
        - User must currently hold Tier N-1 to apply for Tier N.
        - Only one PENDING or APPROVED submission per tier is allowed at a time.
          Re-submission is permitted only after a prior submission is REJECTED.
        - S3 upload happens before the DB record is created; if S3 fails the DB
          remains clean.
        """
        # File validation
        if len(file_bytes) > MAX_FILE_SIZE:
            raise ValidationError(
                "Document must not exceed 5 MB.", error_code="FILE_TOO_LARGE"
            )
        mime = detect_mime_type(file_bytes)
        if mime not in _ALLOWED_MIME:
            raise ValidationError(
                "Unsupported file type. Allowed formats: JPEG, PNG, PDF.",
                error_code="INVALID_FILE_TYPE",
            )

        # Tier progression check
        if user.kyc_tier != target_tier - 1:
            raise ValidationError(
                f"You must complete Tier {target_tier - 1} before applying for "
                f"Tier {target_tier}.",
                error_code="KYC_TIER_SKIP",
            )

        # Active-submission uniqueness check
        existing = await self._repo.get_active_for_tier(user.id, target_tier)
        if existing is not None:
            if existing.status == KYCStatus.PENDING:
                raise ConflictError(
                    "A KYC submission for this tier is already under review.",
                    error_code="KYC_SUBMISSION_PENDING",
                )
            # APPROVED — user is already at this tier
            raise ConflictError(
                f"KYC Tier {target_tier} has already been approved for this account.",
                error_code="KYC_ALREADY_APPROVED",
            )

        # Upload to S3 before creating the DB record.
        # If upload fails, ExternalServiceError is raised and no DB record is created.
        submission_id = uuid.uuid4()
        safe_name = filename.replace("/", "_").replace("..", "_")
        key = f"kyc/{user.id}/{submission_id}/{safe_name}"
        await self._storage.upload_file(file_bytes, key)

        # Persist the submission record and commit
        submission = await self._repo.create(
            submission_id=submission_id,
            user_id=user.id,
            requested_tier=target_tier,
            document_key=key,
        )
        await self.session.commit()

        # Audit log — fire-and-forget after commit
        from app.services.audit import AuditService
        await AuditService(self.session).log(
            actor_id=user.id,
            actor_type=ActorType.USER,
            action="kyc.submitted",
            target_type="kyc_submission",
            target_id=submission.id,
            metadata={"tier_requested": target_tier},
        )

        return submission

    async def get_my_submission(self, user_id: uuid.UUID) -> KYCSubmission:
        """Return the most recent KYC submission for the user, or raise NotFoundError."""
        sub = await self._repo.get_latest_for_user(user_id)
        if sub is None:
            raise NotFoundError("KYC submission")
        return sub

    async def get_submission(self, submission_id: uuid.UUID) -> KYCSubmission:
        """Load any submission by ID. Raises NotFoundError if missing."""
        sub = await self._repo.get_by_id(submission_id)
        if sub is None:
            raise NotFoundError("KYC submission")
        return sub

    async def approve_kyc(
        self,
        submission_id: uuid.UUID,
        reviewer: User,
    ) -> KYCSubmission:
        """
        Approve a PENDING submission, promote the user's KYC tier, and record
        the reviewer.  Idempotent guard: re-approving an already-approved
        submission raises ConflictError.
        """
        from app.services.user import UserService

        sub = await self.get_submission(submission_id)

        if sub.status == KYCStatus.APPROVED:
            raise ConflictError(
                "This submission has already been approved.",
                error_code="KYC_ALREADY_APPROVED",
            )
        if sub.status == KYCStatus.REJECTED:
            raise ConflictError(
                "Cannot approve a rejected submission.",
                error_code="KYC_ALREADY_REJECTED",
            )

        # Admin cannot review their own submission
        if reviewer.id == sub.user_id:
            raise ForbiddenError("Admins cannot review their own KYC submissions.")

        sub.status = KYCStatus.APPROVED
        sub.reviewer_id = reviewer.id
        sub.reviewed_at = datetime.now(timezone.utc)
        await self.session.flush()
        await self.session.refresh(sub)

        # Tier promotion in the same session / transaction, then commit both together
        await UserService(self.session).update_kyc_tier(sub.user_id, sub.requested_tier)
        await self.session.commit()

        # Audit log — fire-and-forget after commit
        from app.services.audit import AuditService
        await AuditService(self.session).log(
            actor_id=reviewer.id,
            actor_type=ActorType.ADMIN,
            action="kyc.approved",
            target_type="kyc_submission",
            target_id=sub.id,
            metadata={
                "user_id": str(sub.user_id),
                "tier_approved": sub.requested_tier,
            },
        )

        return sub

    async def reject_kyc(
        self,
        submission_id: uuid.UUID,
        reviewer: User,
        reason: str,
    ) -> KYCSubmission:
        """
        Reject a PENDING submission with a reason.  The user may resubmit afterward.
        """
        sub = await self.get_submission(submission_id)

        if sub.status != KYCStatus.PENDING:
            raise ConflictError(
                f"Cannot reject a submission with status '{sub.status.value}'.",
                error_code="KYC_INVALID_STATUS_FOR_REJECTION",
            )
        if reviewer.id == sub.user_id:
            raise ForbiddenError("Admins cannot review their own KYC submissions.")

        sub.status = KYCStatus.REJECTED
        sub.reviewer_id = reviewer.id
        sub.rejection_reason = reason
        sub.reviewed_at = datetime.now(timezone.utc)
        await self.session.flush()
        await self.session.refresh(sub)
        await self.session.commit()

        # Audit log — fire-and-forget after commit
        from app.services.audit import AuditService
        await AuditService(self.session).log(
            actor_id=reviewer.id,
            actor_type=ActorType.ADMIN,
            action="kyc.rejected",
            target_type="kyc_submission",
            target_id=sub.id,
            metadata={
                "user_id": str(sub.user_id),
                "reason": reason,
            },
        )

        return sub

    async def get_presigned_doc_url(self, submission: KYCSubmission) -> str:
        """Generate a 1-hour presigned S3 URL for the submission's document."""
        return await self._storage.get_presigned_url(submission.document_key)

    async def list_submissions(
        self,
        *,
        status: Optional[KYCStatus] = KYCStatus.PENDING,
        limit: int,
        offset: int,
    ) -> PaginatedData[KYCSubmissionOut]:
        rows, total = await self._repo.list_by_status(status, limit=limit, offset=offset)
        return PaginatedData(
            items=[KYCSubmissionOut.model_validate(r) for r in rows],
            total=total,
            limit=limit,
            offset=offset,
        )
