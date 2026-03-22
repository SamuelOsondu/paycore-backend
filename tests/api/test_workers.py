"""
Tests for the Workers component.

Coverage:
- Celery configuration: task module registration, beat schedule entries and intervals
- Task: check_stale_transactions registered under correct task name
- Task registration: all required task names present in celery_app.tasks
- Task: _reconcile_funding — Paystack success → process_charge_success called
- Task: _reconcile_funding — Paystack non-success → two-step FAILED transition
- Task: _reconcile_funding — transaction already resolved → no transition
- Task: _reconcile_funding — Paystack API exception → caught, no crash
- Task: _reconcile_funding — session commit exception → caught, no crash
- Task: _reconcile — no stale transactions → no tasks enqueued
- Task: _reconcile — stale WITHDRAWAL transactions → process_withdrawal.delay called
- Task: _reconcile — stale FUNDING transactions → _reconcile_funding called per item

Patching notes:
  Imports inside functions (deferred to avoid circular imports at worker startup)
  are patched at their *source* module: e.g.
  "app.integrations.paystack.PaystackClient" — intercepted when the
  `from app.integrations.paystack import PaystackClient` inside _reconcile_funding
  runs at call time.
  Session factories: patch "sqlalchemy.ext.asyncio.async_sessionmaker" and
  "app.core.database.async_engine".
"""

import uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.transaction import Transaction, TransactionStatus, TransactionType
from app.workers.celery_app import celery_app
from app.workers.reconciliation_tasks import (
    BATCH_LIMIT,
    STALE_THRESHOLD_MINUTES,
    _reconcile,
    _reconcile_funding,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_async_session(rows_per_call: list[list]) -> tuple[MagicMock, list[AsyncMock]]:
    """
    Build a mock async_sessionmaker that returns sessions yielding the given
    row-lists in order (one list per `async with session_factory() as session` call).

    Returns (mock_sessionmaker, list_of_session_mocks).
    """
    sessions = []
    contexts = []
    for rows in rows_per_call:
        mock_result = MagicMock()
        mock_result.all.return_value = rows
        session = AsyncMock()
        session.execute = AsyncMock(return_value=mock_result)
        sessions.append(session)

        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=session)
        ctx.__aexit__ = AsyncMock(return_value=False)
        contexts.append(ctx)

    call_iter = iter(contexts)

    # The session factory is what async_sessionmaker(...) returns.
    mock_sf = MagicMock(side_effect=lambda: next(call_iter))
    # async_sessionmaker(...) must return mock_sf
    mock_asm = MagicMock(return_value=mock_sf)
    return mock_asm, sessions


def _single_session_ctx(session: AsyncMock) -> MagicMock:
    """Wrap a single session mock in an async context-manager mock."""
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


# ── Celery configuration ──────────────────────────────────────────────────────


def test_celery_includes_reconciliation_tasks() -> None:
    """reconciliation_tasks module is listed in celery_app.conf.include."""
    assert "app.workers.reconciliation_tasks" in celery_app.conf.include


def test_celery_beat_schedule_retry_webhooks() -> None:
    """Beat schedule: retry_pending_webhooks runs every 5 minutes."""
    entry = celery_app.conf.beat_schedule.get("retry-pending-webhooks-every-5-minutes")
    assert entry is not None
    assert entry["task"] == "webhooks.retry_pending_webhooks"
    assert entry["schedule"] == timedelta(minutes=5)


def test_celery_beat_schedule_stale_transaction_check() -> None:
    """Beat schedule: check_stale_transactions runs every 30 minutes."""
    entry = celery_app.conf.beat_schedule.get(
        "check-stale-transactions-every-30-minutes"
    )
    assert entry is not None
    assert entry["task"] == "reconciliation.check_stale_transactions"
    assert entry["schedule"] == timedelta(minutes=30)


def test_reconciliation_constants() -> None:
    """Stale threshold and batch limit match the spec requirements."""
    assert STALE_THRESHOLD_MINUTES == 30
    assert BATCH_LIMIT > 0


def test_all_required_tasks_registered() -> None:
    """All required Celery task names are discoverable in celery_app.tasks."""
    registered = set(celery_app.tasks.keys())
    required = {
        "fraud.flag_transaction_risk",
        "webhooks.deliver_merchant_webhook",
        "webhooks.retry_pending_webhooks",
        "paystack.process_webhook",
        "withdrawals.process_withdrawal",
        "reconciliation.check_stale_transactions",
    }
    missing = required - registered
    assert not missing, f"Missing Celery tasks: {missing}"


def test_check_stale_transactions_task_name() -> None:
    """check_stale_transactions is registered under the correct task name."""
    assert "reconciliation.check_stale_transactions" in celery_app.tasks


# ── Task: _reconcile_funding — Paystack success ───────────────────────────────


@pytest.mark.asyncio
async def test_reconcile_funding_paystack_success_delegates_to_webhook_service() -> None:
    """
    When Paystack returns status=success, PaystackWebhookService.process_charge_success
    is called with the full Paystack data dict, and the session is committed.
    """
    txn_id = uuid.uuid4()
    provider_ref = "paystack_charge_abc"
    paystack_response = {
        "status": "success",
        "reference": provider_ref,
        "amount": 50000,
    }

    mock_webhook_service = AsyncMock()

    mock_session = AsyncMock()
    ctx = _single_session_ctx(mock_session)
    mock_sf = MagicMock(return_value=ctx)
    mock_asm = MagicMock(return_value=mock_sf)

    with patch("sqlalchemy.ext.asyncio.async_sessionmaker", mock_asm):
        with patch("app.core.database.async_engine"):
            with patch(
                "app.integrations.paystack.PaystackClient"
            ) as mock_client_cls:
                mock_client_cls.return_value.verify_transaction = AsyncMock(
                    return_value=paystack_response
                )
                with patch(
                    "app.services.paystack_webhook.PaystackWebhookService",
                    return_value=mock_webhook_service,
                ):
                    await _reconcile_funding(txn_id, provider_ref)

    mock_webhook_service.process_charge_success.assert_awaited_once_with(
        paystack_response
    )
    mock_session.commit.assert_awaited_once()


# ── Task: _reconcile_funding — Paystack non-success ──────────────────────────


@pytest.mark.asyncio
async def test_reconcile_funding_paystack_abandoned_transitions_to_failed() -> None:
    """
    When Paystack returns a non-success status, the stale PENDING transaction is
    transitioned PENDING → PROCESSING → FAILED (two-step, respecting state machine).
    The failure_reason includes the Paystack status string.
    """
    txn_id = uuid.uuid4()
    provider_ref = "paystack_charge_abandoned"
    paystack_response = {
        "status": "abandoned",
        "reference": provider_ref,
        "amount": 10000,
    }

    txn_mock = MagicMock(spec=Transaction)
    txn_mock.id = txn_id
    txn_mock.status = TransactionStatus.PENDING

    mock_repo = AsyncMock()
    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=txn_mock)
    ctx = _single_session_ctx(mock_session)
    mock_sf = MagicMock(return_value=ctx)
    mock_asm = MagicMock(return_value=mock_sf)

    with patch("sqlalchemy.ext.asyncio.async_sessionmaker", mock_asm):
        with patch("app.core.database.async_engine"):
            with patch("app.integrations.paystack.PaystackClient") as mock_client_cls:
                mock_client_cls.return_value.verify_transaction = AsyncMock(
                    return_value=paystack_response
                )
                with patch(
                    "app.repositories.transaction.TransactionRepository",
                    return_value=mock_repo,
                ):
                    await _reconcile_funding(txn_id, provider_ref)

    # First call: PENDING → PROCESSING
    # Second call: PROCESSING → FAILED
    assert mock_repo.update_status.await_count == 2
    first_call = mock_repo.update_status.call_args_list[0]
    second_call = mock_repo.update_status.call_args_list[1]
    assert first_call.args[1] == TransactionStatus.PROCESSING
    assert second_call.args[1] == TransactionStatus.FAILED
    # failure_reason captures the Paystack status
    assert "abandoned" in second_call.kwargs.get("failure_reason", "")
    mock_session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_reconcile_funding_already_resolved_skips_transition() -> None:
    """
    If the transaction status changed between the batch query and reconciliation
    (e.g., resolved by a concurrent webhook), no transition is performed.
    """
    txn_id = uuid.uuid4()
    provider_ref = "paystack_charge_already_done"
    paystack_response = {
        "status": "failed",
        "reference": provider_ref,
        "amount": 10000,
    }

    # Transaction was already completed by a concurrent webhook
    txn_mock = MagicMock(spec=Transaction)
    txn_mock.id = txn_id
    txn_mock.status = TransactionStatus.COMPLETED

    mock_repo = AsyncMock()
    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=txn_mock)
    ctx = _single_session_ctx(mock_session)
    mock_sf = MagicMock(return_value=ctx)
    mock_asm = MagicMock(return_value=mock_sf)

    with patch("sqlalchemy.ext.asyncio.async_sessionmaker", mock_asm):
        with patch("app.core.database.async_engine"):
            with patch("app.integrations.paystack.PaystackClient") as mock_client_cls:
                mock_client_cls.return_value.verify_transaction = AsyncMock(
                    return_value=paystack_response
                )
                with patch(
                    "app.repositories.transaction.TransactionRepository",
                    return_value=mock_repo,
                ):
                    await _reconcile_funding(txn_id, provider_ref)

    mock_repo.update_status.assert_not_awaited()
    mock_session.commit.assert_not_awaited()


# ── Task: _reconcile_funding — exception handling ────────────────────────────


@pytest.mark.asyncio
async def test_reconcile_funding_paystack_api_error_does_not_raise() -> None:
    """
    ExternalServiceError from PaystackClient.verify_transaction is caught.
    The function returns normally so that other transactions in the batch continue.
    """
    from app.core.exceptions import ExternalServiceError

    txn_id = uuid.uuid4()
    provider_ref = "paystack_down"

    with patch("sqlalchemy.ext.asyncio.async_sessionmaker"):
        with patch("app.core.database.async_engine"):
            with patch("app.integrations.paystack.PaystackClient") as mock_client_cls:
                mock_client_cls.return_value.verify_transaction = AsyncMock(
                    side_effect=ExternalServiceError("Paystack")
                )
                # Must not raise
                await _reconcile_funding(txn_id, provider_ref)


@pytest.mark.asyncio
async def test_reconcile_funding_session_commit_error_does_not_raise() -> None:
    """
    A DB exception during commit is caught and logged.
    The function returns normally so the next transaction is still processed.
    """
    txn_id = uuid.uuid4()
    provider_ref = "paystack_ref_commit_fail"
    paystack_response = {"status": "success", "reference": provider_ref, "amount": 5000}

    mock_webhook_service = AsyncMock()
    mock_session = AsyncMock()
    mock_session.commit = AsyncMock(side_effect=RuntimeError("DB commit failed"))
    ctx = _single_session_ctx(mock_session)
    mock_sf = MagicMock(return_value=ctx)
    mock_asm = MagicMock(return_value=mock_sf)

    with patch("sqlalchemy.ext.asyncio.async_sessionmaker", mock_asm):
        with patch("app.core.database.async_engine"):
            with patch("app.integrations.paystack.PaystackClient") as mock_client_cls:
                mock_client_cls.return_value.verify_transaction = AsyncMock(
                    return_value=paystack_response
                )
                with patch(
                    "app.services.paystack_webhook.PaystackWebhookService",
                    return_value=mock_webhook_service,
                ):
                    # Must not raise
                    await _reconcile_funding(txn_id, provider_ref)


# ── Task: _reconcile — batch query and dispatch ───────────────────────────────


@pytest.mark.asyncio
async def test_reconcile_no_stale_transactions_is_noop() -> None:
    """When no stale transactions exist, no tasks are enqueued and no errors occur."""
    mock_asm, sessions = _make_async_session(
        rows_per_call=[[], []]  # funding batch: empty, withdrawal batch: empty
    )

    with patch("sqlalchemy.ext.asyncio.async_sessionmaker", mock_asm):
        with patch("app.core.database.async_engine"):
            # process_withdrawal is imported via `from app.workers.withdrawal_tasks import
            # process_withdrawal` inside _reconcile at call time — patch the source module.
            with patch(
                "app.workers.withdrawal_tasks.process_withdrawal"
            ) as mock_pw:
                with patch(
                    "app.workers.reconciliation_tasks._reconcile_funding",
                    new_callable=AsyncMock,
                ) as mock_rf:
                    await _reconcile()

    mock_pw.delay.assert_not_called()
    mock_rf.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconcile_stale_withdrawals_are_re_enqueued() -> None:
    """Stale PENDING WITHDRAWAL transactions are dispatched via process_withdrawal.delay."""
    wid1 = uuid.uuid4()
    wid2 = uuid.uuid4()

    mock_asm, _ = _make_async_session(
        rows_per_call=[
            [],  # funding batch: empty
            [(wid1,), (wid2,)],  # withdrawal batch: two records
        ]
    )

    with patch("sqlalchemy.ext.asyncio.async_sessionmaker", mock_asm):
        with patch("app.core.database.async_engine"):
            with patch(
                "app.workers.withdrawal_tasks.process_withdrawal"
            ) as mock_pw:
                with patch(
                    "app.workers.reconciliation_tasks._reconcile_funding",
                    new_callable=AsyncMock,
                ):
                    await _reconcile()

    assert mock_pw.delay.call_count == 2
    enqueued = {call.args[0] for call in mock_pw.delay.call_args_list}
    assert enqueued == {str(wid1), str(wid2)}


@pytest.mark.asyncio
async def test_reconcile_stale_funding_calls_reconcile_funding_per_item() -> None:
    """_reconcile_funding is called once per stale FUNDING transaction found."""
    txn_id1 = uuid.uuid4()
    txn_id2 = uuid.uuid4()
    ref1, ref2 = "ref_one", "ref_two"

    mock_asm, _ = _make_async_session(
        rows_per_call=[
            [(txn_id1, ref1), (txn_id2, ref2)],  # funding batch: two records
            [],  # withdrawal batch: empty
        ]
    )

    with patch("sqlalchemy.ext.asyncio.async_sessionmaker", mock_asm):
        with patch("app.core.database.async_engine"):
            with patch("app.workers.withdrawal_tasks.process_withdrawal"):
                with patch(
                    "app.workers.reconciliation_tasks._reconcile_funding",
                    new_callable=AsyncMock,
                ) as mock_rf:
                    await _reconcile()

    assert mock_rf.await_count == 2
    called_with = {
        (call.args[0], call.args[1])
        for call in mock_rf.call_args_list
    }
    assert called_with == {(txn_id1, ref1), (txn_id2, ref2)}
