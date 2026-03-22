"""
Celery tasks for withdrawal processing.

process_withdrawal
  → calls WithdrawalService.execute_payout via asyncio.run()
  → autoretry on Paystack network errors (max 3, 60-second delay)
  → on exhausted retries: Paystack is down; reconciliation job will re-trigger
"""

import asyncio
import logging
import uuid

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="withdrawals.process_withdrawal",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=60,
    retry_kwargs={"max_retries": 3},
)
def process_withdrawal(self, transaction_id: str) -> None:
    """
    Execute the Paystack bank transfer for a PENDING withdrawal transaction.

    Idempotent — if the transfer was already dispatched in a prior attempt
    (PROCESSING state with provider_reference set), the task exits immediately
    and waits for the Paystack transfer webhook.

    Uses the platform transaction reference as the Paystack transfer reference
    so that Paystack deduplicates repeated calls — no double transfer possible.
    """
    asyncio.run(_dispatch(uuid.UUID(transaction_id)))


async def _dispatch(transaction_id: uuid.UUID) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.database import async_engine
    from app.services.withdrawal import WithdrawalService

    session_factory = async_sessionmaker(
        bind=async_engine,
        expire_on_commit=False,
        autoflush=False,
    )

    async with session_factory() as session:
        service = WithdrawalService(session)
        await service.execute_payout(transaction_id)
