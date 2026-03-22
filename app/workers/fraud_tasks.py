import logging
import uuid

from app.core.database import SyncSessionLocal
from app.models.transaction import Transaction
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="fraud.flag_transaction_risk",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def flag_transaction_risk(self, transaction_id: str, reason: str) -> None:
    """
    Mark a transaction as risk-flagged for admin review.

    Idempotent — safe to call multiple times on the same transaction.
    Must not be awaited inside the async request path; always called via .delay().
    """
    with SyncSessionLocal() as session:
        txn = session.get(Transaction, uuid.UUID(transaction_id))
        if txn is None:
            logger.warning(
                "flag_transaction_risk: transaction %s not found", transaction_id
            )
            return
        if txn.risk_flagged:
            return  # already flagged — idempotent, skip
        txn.risk_flagged = True
        txn.risk_flag_reason = reason
        session.commit()
        logger.info(
            "Transaction %s flagged for risk review: %s", transaction_id, reason
        )

        # Audit log — fire-and-forget, never raises
        from app.models.audit_log import ActorType
        from app.services.audit import log_sync
        log_sync(
            session,
            actor_id=txn.initiated_by_user_id,
            actor_type=ActorType.SYSTEM,
            action="admin.transaction_flagged",
            target_type="transaction",
            target_id=txn.id,
            metadata={"reason": reason},
        )
