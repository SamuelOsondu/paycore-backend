import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import require_role
from app.core.exceptions import NotFoundError
from app.core.response import success_response
from app.models.audit_log import ActorType
from app.models.kyc_submission import KYCStatus
from app.models.transaction import TransactionStatus, TransactionType
from app.models.user import User, UserRole
from app.repositories.audit_log import AuditLogRepository
from app.repositories.ledger import LedgerRepository
from app.repositories.transaction import TransactionRepository
from app.repositories.user import UserRepository
from app.repositories.webhook_delivery import WebhookDeliveryRepository
from app.schemas.audit_log import AuditLogOut
from app.schemas.common import ApiResponse, PaginatedData
from app.schemas.kyc import KYCRejectRequest, KYCSubmissionAdminOut, KYCSubmissionOut
from app.schemas.ledger import LedgerEntryOut
from app.schemas.transaction import TransactionAdminOut, TransactionDetailAdminOut
from app.schemas.user import UserOut
from app.schemas.webhook_delivery import WebhookDeliveryOut
from app.services.kyc import KYCService

router = APIRouter(prefix="/admin", tags=["Admin"])


# ── Audit log admin endpoints ─────────────────────────────────────────────────


@router.get(
    "/audit-logs",
    response_model=ApiResponse[PaginatedData[AuditLogOut]],
    summary="List audit log entries (admin)",
)
async def list_audit_logs(
    actor_id: Optional[uuid.UUID] = Query(
        default=None, description="Filter by actor UUID."
    ),
    action: Optional[str] = Query(
        default=None, description="Filter by exact action string, e.g. 'kyc.approved'."
    ),
    from_date: Optional[datetime] = Query(
        default=None, description="Inclusive lower bound on created_at (ISO 8601)."
    ),
    to_date: Optional[datetime] = Query(
        default=None, description="Inclusive upper bound on created_at (ISO 8601)."
    ),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    _admin: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Paginated audit log, newest first.
    Optionally filter by actor_id, action name, and date range.
    """
    repo = AuditLogRepository(db)
    items = await repo.list_all(
        limit=limit,
        offset=offset,
        actor_id=actor_id,
        action=action,
        from_date=from_date,
        to_date=to_date,
    )
    total = await repo.count_all(
        actor_id=actor_id,
        action=action,
        from_date=from_date,
        to_date=to_date,
    )
    return success_response(
        data=PaginatedData(
            items=[AuditLogOut.model_validate(e) for e in items],
            total=total,
            limit=limit,
            offset=offset,
        ),
        message="Audit logs retrieved.",
    )


# ── Webhook delivery admin endpoints ─────────────────────────────────────────


@router.get(
    "/webhook-deliveries",
    response_model=ApiResponse[PaginatedData[WebhookDeliveryOut]],
    summary="List webhook delivery records (admin)",
)
async def list_webhook_deliveries(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    _admin: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Paginated list of all outgoing webhook delivery records, newest first.
    Useful for debugging failed or retrying deliveries.
    """
    repo = WebhookDeliveryRepository(db)
    items = await repo.list_all(limit=limit, offset=offset)
    total = await repo.count_all()
    return success_response(
        data=PaginatedData(
            items=[WebhookDeliveryOut.model_validate(d) for d in items],
            total=total,
            limit=limit,
            offset=offset,
        ),
        message="Webhook deliveries retrieved.",
    )


# ── KYC admin endpoints ───────────────────────────────────────────────────────


@router.get(
    "/kyc",
    response_model=ApiResponse[PaginatedData[KYCSubmissionOut]],
    summary="List KYC submissions (admin)",
)
async def list_kyc_submissions(
    status: Optional[KYCStatus] = Query(
        default=KYCStatus.PENDING,
        description="Filter by status. Defaults to 'pending'.",
    ),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    _admin: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Paginated list of KYC submissions, defaulting to PENDING ones."""
    service = KYCService(db)
    result = await service.list_submissions(status=status, limit=limit, offset=offset)
    return success_response(data=result, message="KYC submissions retrieved.")


@router.get(
    "/kyc/{submission_id}",
    response_model=ApiResponse[KYCSubmissionAdminOut],
    summary="Get a KYC submission with presigned document URL (admin)",
)
async def get_kyc_submission(
    submission_id: str,
    _admin: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Return full KYC submission details including a 1-hour presigned S3 URL
    for the uploaded identity document.
    """
    service = KYCService(db)
    sub = await service.get_submission(uuid.UUID(submission_id))
    doc_url = await service.get_presigned_doc_url(sub)

    out = KYCSubmissionAdminOut.model_validate(sub)
    out.document_url = doc_url
    return success_response(data=out, message="KYC submission retrieved.")


@router.post(
    "/kyc/{submission_id}/approve",
    response_model=ApiResponse[KYCSubmissionAdminOut],
    summary="Approve a KYC submission (admin)",
)
async def approve_kyc(
    submission_id: str,
    admin: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Approve a PENDING KYC submission. Promotes the user's KYC tier automatically.
    Idempotent guard: re-approving an already-approved submission returns 409.
    Admin cannot approve their own submission.
    """
    service = KYCService(db)
    sub = await service.approve_kyc(uuid.UUID(submission_id), reviewer=admin)
    return success_response(
        data=KYCSubmissionAdminOut.model_validate(sub),
        message="KYC submission approved. User tier upgraded.",
    )


@router.post(
    "/kyc/{submission_id}/reject",
    response_model=ApiResponse[KYCSubmissionAdminOut],
    summary="Reject a KYC submission (admin)",
)
async def reject_kyc(
    submission_id: str,
    body: KYCRejectRequest,
    admin: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Reject a PENDING KYC submission with a reason.
    The user may resubmit after rejection.
    Admin cannot reject their own submission.
    """
    service = KYCService(db)
    sub = await service.reject_kyc(
        uuid.UUID(submission_id), reviewer=admin, reason=body.reason
    )
    return success_response(
        data=KYCSubmissionAdminOut.model_validate(sub),
        message="KYC submission rejected.",
    )


# ── Transaction admin endpoints ───────────────────────────────────────────────


@router.get(
    "/transactions",
    response_model=ApiResponse[PaginatedData[TransactionAdminOut]],
    summary="List all transactions (admin)",
)
async def list_transactions(
    status: Optional[TransactionStatus] = Query(
        default=None, description="Filter by transaction status."
    ),
    type: Optional[TransactionType] = Query(
        default=None, description="Filter by transaction type."
    ),
    risk_flagged: Optional[bool] = Query(
        default=None, description="Filter to risk-flagged (true) or clean (false) transactions."
    ),
    from_date: Optional[datetime] = Query(
        default=None, description="Inclusive lower bound on created_at (ISO 8601)."
    ),
    to_date: Optional[datetime] = Query(
        default=None, description="Inclusive upper bound on created_at (ISO 8601)."
    ),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    _admin: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Paginated list of all transactions across the platform, newest first.
    Supports filtering by status, type, fraud flag, and date range.
    Includes internal fields (provider_reference, risk_flagged) not available
    to end users.
    """
    repo = TransactionRepository(db)
    items = await repo.list_admin(
        limit=limit,
        offset=offset,
        status=status,
        type=type,
        risk_flagged=risk_flagged,
        from_date=from_date,
        to_date=to_date,
    )
    total = await repo.count_admin(
        status=status,
        type=type,
        risk_flagged=risk_flagged,
        from_date=from_date,
        to_date=to_date,
    )
    return success_response(
        data=PaginatedData(
            items=[TransactionAdminOut.model_validate(t) for t in items],
            total=total,
            limit=limit,
            offset=offset,
        ),
        message="Transactions retrieved.",
    )


@router.get(
    "/transactions/{reference}",
    response_model=ApiResponse[TransactionDetailAdminOut],
    summary="Get a transaction with ledger entries (admin)",
)
async def get_transaction(
    reference: str,
    _admin: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Return full transaction details including the associated double-entry ledger
    records. Useful for financial auditing and dispute resolution.
    """
    txn_repo = TransactionRepository(db)
    txn = await txn_repo.get_by_reference(reference)
    if txn is None:
        raise NotFoundError("Transaction")

    ledger_repo = LedgerRepository(db)
    entries = await ledger_repo.get_by_transaction(txn.id)

    out = TransactionDetailAdminOut.model_validate(txn)
    out.ledger_entries = [LedgerEntryOut.model_validate(e) for e in entries]
    return success_response(data=out, message="Transaction retrieved.")


# ── Reconciliation admin endpoint ─────────────────────────────────────────────


@router.post(
    "/reconciliation/run",
    response_model=ApiResponse[None],
    summary="Manually trigger stale transaction reconciliation (admin)",
)
async def run_reconciliation(
    admin: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Enqueue a one-off run of the stale transaction reconciliation Celery task.

    The task is idempotent — if no stale transactions exist, it is a no-op.
    The reconciliation runs asynchronously; this endpoint returns immediately
    once the task has been enqueued.
    """
    from app.workers.reconciliation_tasks import check_stale_transactions

    check_stale_transactions.delay()

    # Audit log — admin-initiated reconciliation trigger
    from app.services.audit import AuditService

    await AuditService(db).log(
        actor_id=admin.id,
        actor_type=ActorType.ADMIN,
        action="admin.reconciliation_triggered",
        target_type=None,
        target_id=None,
    )

    return success_response(data=None, message="Reconciliation task enqueued.")


# ── User admin endpoints ──────────────────────────────────────────────────────


@router.get(
    "/users",
    response_model=ApiResponse[PaginatedData[UserOut]],
    summary="List all users (admin)",
)
async def list_users(
    role: Optional[UserRole] = Query(
        default=None, description="Filter by user role."
    ),
    kyc_tier: Optional[int] = Query(
        default=None, ge=0, le=2, description="Filter by KYC tier (0, 1, or 2)."
    ),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    _admin: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Paginated list of all platform users, newest first.
    Optionally filter by role and/or KYC tier.
    Soft-deleted users are excluded.
    """
    repo = UserRepository(db)
    items = await repo.list_all(
        limit=limit,
        offset=offset,
        role=role,
        kyc_tier=kyc_tier,
    )
    total = await repo.count_all(role=role, kyc_tier=kyc_tier)
    return success_response(
        data=PaginatedData(
            items=[UserOut.model_validate(u) for u in items],
            total=total,
            limit=limit,
            offset=offset,
        ),
        message="Users retrieved.",
    )


@router.get(
    "/users/{user_id}",
    response_model=ApiResponse[UserOut],
    summary="Get a user by ID (admin)",
)
async def get_user(
    user_id: uuid.UUID,
    _admin: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Return the full profile of a single user. Raises 404 if not found or
    soft-deleted.
    """
    repo = UserRepository(db)
    user = await repo.get_by_id(user_id)
    if user is None:
        raise NotFoundError("User")
    return success_response(data=UserOut.model_validate(user), message="User retrieved.")
