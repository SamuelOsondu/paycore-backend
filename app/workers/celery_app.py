from datetime import timedelta

from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "paycore",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=[
        "app.workers.fraud_tasks",
        "app.workers.webhook_tasks",
        "app.workers.paystack_tasks",
        "app.workers.withdrawal_tasks",
        "app.workers.reconciliation_tasks",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    worker_concurrency=settings.CELERY_WORKER_CONCURRENCY,
    # Ack only after task completes — safe to retry on worker crash.
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    # ── Celery Beat periodic tasks ────────────────────────────────────────────
    beat_schedule={
        "retry-pending-webhooks-every-5-minutes": {
            "task": "webhooks.retry_pending_webhooks",
            "schedule": timedelta(minutes=5),
        },
        "check-stale-transactions-every-30-minutes": {
            "task": "reconciliation.check_stale_transactions",
            "schedule": timedelta(minutes=30),
        },
    },
)
