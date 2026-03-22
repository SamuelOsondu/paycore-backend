"""
Celery task for transaction reconciliation.

check_stale_transactions
    → Celery Beat (every 30 minutes)

    Scans for two categories of stale PENDING transactions and resolves them:

    PENDING FUNDING transactions with a provider_reference older than 30 min:
        The user paid via Paystack but no charge.success webhook arrived (network
        glitch, Paystack delivery failure, etc.).
        → Verify the payment status via Paystack's verify_transaction API.
        → success  : delegate to PaystackWebhookService.process_charge_success
                     (same code path as the inbound webhook — idempotent).
        → non-success : transition PENDING → PROCESSING → FAILED so the user
                        can re-attempt without the record staying stuck forever.
        → any error   : logged; the transaction stays PENDING and is retried
                        on the next beat cycle.

    PENDING WITHDRAWAL transactions older than 30 min:
        The process_withdrawal Celery task exhausted its automatic retries
        (typically because Paystack was temporarily unavailable).
        → Re-enqueue process_withdrawal.  The task is idempotent — if the
           transfer was already dispatched (status == PROCESSING), it exits
           immediately and waits for the Paystack transfer webhook.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models.transaction import Transaction, TransactionStatus, TransactionType
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

# Transactions older than this threshold are considered stale.
STALE_THRESHOLD_MINUTES = 30
# Limit per sweep to bound task duration (pick up remaining ones next cycle).
BATCH_LIMIT = 50


@celery_app.task(name="reconciliation.check_stale_transactions")
def check_stale_transactions() -> None:
    """
    Celery Beat task — runs every 30 minutes.

    Idempotent — safe to call multiple times; downstream handlers guard against
    double-processing (PaystackWebhookService idempotency guard; process_withdrawal
    PROCESSING status check).
    """
    asyncio.run(_reconcile())


async def _reconcile() -> None:
    """Async body of check_stale_transactions."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.database import async_engine

    session_factory = async_sessionmaker(
        bind=async_engine,
        expire_on_commit=False,
        autoflush=False,
    )

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=STALE_THRESHOLD_MINUTES)

    # ── Stale PENDING FUNDING transactions ────────────────────────────────────
    async with session_factory() as session:
        result = await session.execute(
            select(Transaction.id, Transaction.provider_reference).where(
                Transaction.type == TransactionType.FUNDING,
                Transaction.status == TransactionStatus.PENDING,
                Transaction.provider_reference.isnot(None),
                Transaction.created_at <= cutoff,
            ).limit(BATCH_LIMIT)
        )
        stale_funding: list[tuple[uuid.UUID, str]] = [
            (row[0], row[1]) for row in result.all()
        ]

    if stale_funding:
        logger.info(
            "check_stale_transactions: found %d stale FUNDING transactions to reconcile",
            len(stale_funding),
        )
        for txn_id, provider_ref in stale_funding:
            await _reconcile_funding(txn_id, provider_ref)

    # ── Stale PENDING WITHDRAWAL transactions ─────────────────────────────────
    async with session_factory() as session:
        result = await session.execute(
            select(Transaction.id).where(
                Transaction.type == TransactionType.WITHDRAWAL,
                Transaction.status == TransactionStatus.PENDING,
                Transaction.created_at <= cutoff,
            ).limit(BATCH_LIMIT)
        )
        stale_withdrawal_ids: list[uuid.UUID] = [row[0] for row in result.all()]

    from app.workers.withdrawal_tasks import process_withdrawal

    for txn_id in stale_withdrawal_ids:
        process_withdrawal.delay(str(txn_id))

    if stale_withdrawal_ids:
        logger.info(
            "check_stale_transactions: re-enqueued %d stale WITHDRAWAL transactions",
            len(stale_withdrawal_ids),
        )


async def _reconcile_funding(txn_id: uuid.UUID, provider_ref: str) -> None:
    """
    Verify a single stale PENDING FUNDING transaction via Paystack and apply result.

    Reuses the same PaystackWebhookService.process_charge_success handler that
    inbound webhooks use — ensuring consistent business logic and idempotency.

    Any exception is caught and logged so one bad transaction never blocks
    reconciliation of the remaining batch.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.database import async_engine
    from app.integrations.paystack import PaystackClient
    from app.services.paystack_webhook import PaystackWebhookService

    session_factory = async_sessionmaker(
        bind=async_engine,
        expire_on_commit=False,
        autoflush=False,
    )

    try:
        paystack_data = await PaystackClient().verify_transaction(provider_ref)
        paystack_status = paystack_data.get("status", "")

        async with session_factory() as session:
            if paystack_status == "success":
                logger.info(
                    "check_stale_transactions: FUNDING %s verified success — crediting",
                    txn_id,
                )
                await PaystackWebhookService(session).process_charge_success(
                    paystack_data
                )
                # process_charge_success commits internally; this is a no-op commit
                # included for clarity and safety if the implementation ever changes.
                await session.commit()
            else:
                # Payment was never completed — mark FAILED so the user can retry.
                # State machine: PENDING is only allowed → PROCESSING, so we do a
                # two-step transition: PENDING → PROCESSING → FAILED.
                txn: Transaction | None = await session.get(Transaction, txn_id)
                if txn is not None and txn.status == TransactionStatus.PENDING:
                    from app.repositories.transaction import TransactionRepository

                    repo = TransactionRepository(session)
                    await repo.update_status(txn, TransactionStatus.PROCESSING)
                    await repo.update_status(
                        txn,
                        TransactionStatus.FAILED,
                        failure_reason=(
                            f"Paystack reconciliation: status={paystack_status!r}"
                        ),
                    )
                    await session.commit()
                    logger.info(
                        "check_stale_transactions: FUNDING %s → FAILED "
                        "(Paystack status=%r)",
                        txn_id,
                        paystack_status,
                    )
                else:
                    logger.info(
                        "check_stale_transactions: FUNDING %s status changed since batch "
                        "query — skipping",
                        txn_id,
                    )
    except Exception:
        logger.exception(
            "check_stale_transactions: error reconciling FUNDING %s — "
            "will retry on next beat cycle",
            txn_id,
        )
