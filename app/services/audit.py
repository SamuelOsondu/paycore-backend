"""
AuditService — fire-and-forget audit trail writer.

The async ``log()`` method is used by all async services (FastAPI request path,
async Celery tasks).  It never raises — any write failure is swallowed after
logging the error so the parent operation is never affected.

The module-level ``log_sync()`` function is used by sync Celery tasks that use
``SyncSessionLocal``.  It follows the same never-raise contract.

Usage (async):
    from app.services.audit import AuditService
    from app.models.audit_log import ActorType
    await AuditService(self.session).log(
        actor_id=user.id,
        actor_type=ActorType.USER,
        action="transfer.completed",
        target_type="transaction",
        target_id=txn.id,
        metadata={"amount": str(amount)},
    )

Usage (sync Celery task):
    from app.services.audit import log_sync
    from app.models.audit_log import ActorType
    log_sync(
        session,
        actor_id=txn.initiated_by_user_id,
        actor_type=ActorType.SYSTEM,
        action="admin.transaction_flagged",
        target_type="transaction",
        target_id=txn.id,
        metadata={"reason": reason},
    )
"""

import logging
import uuid
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import ActorType, AuditLog
from app.repositories.audit_log import AuditLogRepository

logger = logging.getLogger(__name__)


class AuditService:
    """
    Thin async wrapper around AuditLogRepository.
    Always called AFTER session.commit() for the parent operation.
    Manages its own commit and rollback so the parent session is left clean.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._repo = AuditLogRepository(session)

    async def log(
        self,
        *,
        actor_id: Optional[uuid.UUID],
        actor_type: ActorType,
        action: str,
        target_type: Optional[str] = None,
        target_id: Optional[uuid.UUID] = None,
        metadata: Optional[dict] = None,
        ip_address: Optional[str] = None,
    ) -> None:
        """
        Write an audit log entry and commit it.

        Guaranteed to never raise — any exception is caught, logged, and
        the session is rolled back to a clean state.
        """
        try:
            await self._repo.create(
                actor_id=actor_id,
                actor_type=actor_type,
                action=action,
                target_type=target_type,
                target_id=target_id,
                metadata=metadata,
                ip_address=ip_address,
            )
            await self.session.commit()
        except Exception:
            logger.exception(
                "Audit log write failed for action=%s actor_id=%s — "
                "parent operation unaffected",
                action,
                actor_id,
            )
            try:
                await self.session.rollback()
            except Exception:
                logger.exception("Audit session rollback also failed")


# ── Sync helper for Celery tasks using SyncSessionLocal ──────────────────────


def log_sync(
    session,
    *,
    actor_id: Optional[uuid.UUID],
    actor_type: ActorType,
    action: str,
    target_type: Optional[str] = None,
    target_id: Optional[uuid.UUID] = None,
    metadata: Optional[dict] = None,
    ip_address: Optional[str] = None,
) -> None:
    """
    Write an audit log entry from a synchronous Celery task context.

    ``session`` must be a SQLAlchemy synchronous Session (from SyncSessionLocal).

    Guaranteed to never raise — any exception is caught and logged.
    The sync session is rolled back on failure so subsequent DB work is safe.
    """
    try:
        entry = AuditLog(
            actor_id=actor_id,
            actor_type=actor_type,
            action=action,
            target_type=target_type,
            target_id=target_id,
            metadata_=metadata,
            ip_address=ip_address,
        )
        session.add(entry)
        session.flush()
        session.commit()
    except Exception:
        logger.exception(
            "Sync audit log write failed for action=%s actor_id=%s — "
            "parent operation unaffected",
            action,
            actor_id,
        )
        try:
            session.rollback()
        except Exception:
            logger.exception("Sync audit session rollback also failed")
