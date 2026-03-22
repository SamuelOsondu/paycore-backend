"""
Celery tasks for Paystack webhook processing.

The task delegates to ``PaystackWebhookService`` async methods via
``asyncio.run()``, which creates a fresh event loop per task invocation.
This is safe in Celery worker threads because they have no running event loop.

Using the async service (rather than inline SyncSessionLocal code) keeps all
charge-success and transfer-result logic testable under the project's async
test infrastructure.
"""

import asyncio
import logging

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="paystack.process_webhook",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def process_paystack_webhook(self, event_type: str, data: dict) -> None:
    """
    Process a verified Paystack inbound webhook event.

    Supported events
    ----------------
    charge.success
        Credit the destination wallet, complete the PENDING FUNDING transaction.
    transfer.success / transfer.failed / transfer.reversed
        Logged and deferred — full handling in the Withdrawals component.
    """
    asyncio.run(_dispatch(event_type, data))


async def _dispatch(event_type: str, data: dict) -> None:
    """
    Create an AsyncSession and route the event to the appropriate handler.

    Importing inside the function avoids circular-import issues at Celery
    worker startup time.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.database import async_engine
    from app.services.paystack_webhook import PaystackWebhookService

    session_factory = async_sessionmaker(
        bind=async_engine,
        expire_on_commit=False,
        autoflush=False,
    )

    async with session_factory() as session:
        service = PaystackWebhookService(session)
        if event_type == "charge.success":
            await service.process_charge_success(data)
        elif event_type in (
            "transfer.success",
            "transfer.failed",
            "transfer.reversed",
        ):
            await service.process_transfer_result(event_type, data)
        else:
            logger.info(
                "paystack: no handler for webhook event '%s' — ignoring", event_type
            )
