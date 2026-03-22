"""
Tests for the Outgoing Webhooks component.

Coverage:
- Unit: HMAC-SHA256 signature generation (_sign_payload)
- Task: deliver_merchant_webhook — success, retry, exhaustion, idempotency
- Task: retry_pending_webhooks — beat task enqueuing logic
- Service: WebhookDeliveryService.create_and_enqueue, mark_delivered, mark_failed
- Admin endpoint: GET /api/v1/admin/webhook-deliveries (auth, pagination)

Task tests use unittest.mock to patch SyncSessionLocal and httpx.Client
so they run without a live database or Redis connection.
"""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import generate_api_key, hash_password
from app.models.merchant import Merchant
from app.models.transaction import Transaction, TransactionStatus, TransactionType
from app.models.user import User, UserRole
from app.models.wallet import Wallet
from app.models.webhook_delivery import WebhookDelivery, WebhookDeliveryStatus
from app.workers.webhook_tasks import (
    MAX_ATTEMPTS,
    _RETRY_DELAYS_MINUTES,
    _sign_payload,
    deliver_merchant_webhook,
    retry_pending_webhooks,
)
from tests.conftest import make_auth_headers


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_delivery(
    *,
    delivery_id: uuid.UUID | None = None,
    merchant_id: uuid.UUID | None = None,
    transaction_id: uuid.UUID | None = None,
    status: WebhookDeliveryStatus = WebhookDeliveryStatus.PENDING,
    attempt_count: int = 0,
) -> MagicMock:
    d = MagicMock(spec=WebhookDelivery)
    d.id = delivery_id or uuid.uuid4()
    d.merchant_id = merchant_id or uuid.uuid4()
    d.transaction_id = transaction_id or uuid.uuid4()
    d.status = status
    d.attempt_count = attempt_count
    d.payload = {"event": "payment.received", "data": {"transaction_reference": "txn_abc"}}
    return d


def _make_merchant(
    *,
    webhook_url: str | None = "https://merchant.example.com/webhook",
    webhook_secret: str | None = "supersecret",
) -> MagicMock:
    m = MagicMock(spec=Merchant)
    m.webhook_url = webhook_url
    m.webhook_secret = webhook_secret
    return m


def _mock_sync_session(delivery: MagicMock | None, merchant: MagicMock | None) -> MagicMock:
    """
    Return a mocked SyncSessionLocal context manager whose .get() returns
    `delivery` on the first call and `merchant` on the second.
    """
    session = MagicMock()
    call_returns = iter(x for x in [delivery, merchant] if True)

    def _get(model, pk):
        return next(call_returns)

    session.get.side_effect = _get

    mock_ctx = MagicMock()
    mock_ctx.__enter__ = MagicMock(return_value=session)
    mock_ctx.__exit__ = MagicMock(return_value=False)
    return mock_ctx, session


def _mock_httpx_response(status_code: int, is_success: bool) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.is_success = is_success
    return resp


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def merchant_owner(db_session: AsyncSession) -> User:
    user = User(
        id=uuid.uuid4(),
        email="webhook_merchant@example.com",
        hashed_password=hash_password("Pass1234!"),
        full_name="Webhook Merchant",
        role=UserRole.MERCHANT,
        kyc_tier=0,
        is_active=True,
        is_email_verified=True,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def merchant_wallet(db_session: AsyncSession, merchant_owner: User) -> Wallet:
    wallet = Wallet(
        id=uuid.uuid4(),
        user_id=merchant_owner.id,
        currency="NGN",
        balance=Decimal("0.00"),
        is_active=True,
    )
    db_session.add(wallet)
    await db_session.flush()
    await db_session.refresh(wallet)
    return wallet


@pytest_asyncio.fixture
async def active_merchant(
    db_session: AsyncSession, merchant_owner: User, merchant_wallet: Wallet
) -> Merchant:
    _, prefix, hashed = generate_api_key()
    merchant = Merchant(
        id=uuid.uuid4(),
        user_id=merchant_owner.id,
        business_name="Hook Shop",
        api_key_hash=hashed,
        api_key_prefix=prefix,
        webhook_url="https://merchant.example.com/webhook",
        webhook_secret="test-secret-1234",
        is_active=True,
    )
    db_session.add(merchant)
    await db_session.flush()
    await db_session.refresh(merchant)
    return merchant


@pytest_asyncio.fixture
async def funded_transaction(
    db_session: AsyncSession,
    merchant_owner: User,
    merchant_wallet: Wallet,
) -> Transaction:
    txn = Transaction(
        id=uuid.uuid4(),
        reference=f"txn_{uuid.uuid4().hex[:12]}",
        type=TransactionType.MERCHANT_PAYMENT,
        status=TransactionStatus.COMPLETED,
        amount=Decimal("500.00"),
        currency="NGN",
        destination_wallet_id=merchant_wallet.id,
        initiated_by_user_id=merchant_owner.id,
    )
    db_session.add(txn)
    await db_session.flush()
    await db_session.refresh(txn)
    return txn


@pytest_asyncio.fixture
async def pending_delivery(
    db_session: AsyncSession,
    active_merchant: Merchant,
    funded_transaction: Transaction,
) -> WebhookDelivery:
    delivery = WebhookDelivery(
        id=uuid.uuid4(),
        merchant_id=active_merchant.id,
        transaction_id=funded_transaction.id,
        event_type="payment.received",
        payload={"event": "payment.received", "data": {}},
        status=WebhookDeliveryStatus.PENDING,
        attempt_count=0,
        next_retry_at=None,
    )
    db_session.add(delivery)
    await db_session.flush()
    await db_session.refresh(delivery)
    return delivery


@pytest_asyncio.fixture
async def delivered_delivery(
    db_session: AsyncSession,
    active_merchant: Merchant,
    funded_transaction: Transaction,
) -> WebhookDelivery:
    delivery = WebhookDelivery(
        id=uuid.uuid4(),
        merchant_id=active_merchant.id,
        transaction_id=funded_transaction.id,
        event_type="payment.received",
        payload={"event": "payment.received", "data": {}},
        status=WebhookDeliveryStatus.DELIVERED,
        attempt_count=1,
        last_response_code=200,
        next_retry_at=None,
    )
    db_session.add(delivery)
    await db_session.flush()
    await db_session.refresh(delivery)
    return delivery


# ── Unit: HMAC signature ──────────────────────────────────────────────────────


def test_sign_payload_produces_correct_hmac() -> None:
    import hashlib
    import hmac as hmac_lib

    payload = b'{"event": "payment.received"}'
    secret = "my-webhook-secret"

    result = _sign_payload(payload, secret)
    expected = hmac_lib.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    assert result == expected


def test_sign_payload_different_secrets_produce_different_signatures() -> None:
    payload = b'{"event": "payment.received"}'
    sig1 = _sign_payload(payload, "secret-one")
    sig2 = _sign_payload(payload, "secret-two")
    assert sig1 != sig2


def test_sign_payload_different_payloads_produce_different_signatures() -> None:
    secret = "same-secret"
    sig1 = _sign_payload(b'{"amount": "100"}', secret)
    sig2 = _sign_payload(b'{"amount": "200"}', secret)
    assert sig1 != sig2


def test_sign_payload_empty_secret_still_produces_deterministic_result() -> None:
    payload = b"some-payload"
    # Empty secret is allowed — sign with empty string
    result = _sign_payload(payload, "")
    assert isinstance(result, str)
    assert len(result) == 64  # SHA256 hex = 64 chars


# ── Task: deliver_merchant_webhook — idempotency / guard ─────────────────────


def test_deliver_not_found_is_noop() -> None:
    """If delivery record doesn't exist, task logs and returns."""
    mock_ctx, mock_session = _mock_sync_session(None, None)
    with patch("app.workers.webhook_tasks.SyncSessionLocal", return_value=mock_ctx):
        deliver_merchant_webhook.run(str(uuid.uuid4()))
    # No commit should happen
    mock_session.commit.assert_not_called()


def test_deliver_already_delivered_is_noop() -> None:
    """If delivery is already DELIVERED, task returns without HTTP call or commit."""
    delivery = _make_delivery(status=WebhookDeliveryStatus.DELIVERED)
    mock_ctx, mock_session = _mock_sync_session(delivery, None)

    with patch("app.workers.webhook_tasks.SyncSessionLocal", return_value=mock_ctx):
        with patch("httpx.Client") as mock_client:
            deliver_merchant_webhook.run(str(delivery.id))
            mock_client.assert_not_called()

    mock_session.commit.assert_not_called()


# ── Task: deliver_merchant_webhook — merchant without webhook URL ──────────────


def test_deliver_no_webhook_url_marks_failed_without_http_call() -> None:
    delivery = _make_delivery(status=WebhookDeliveryStatus.PENDING)
    merchant = _make_merchant(webhook_url=None)
    mock_ctx, mock_session = _mock_sync_session(delivery, merchant)

    with patch("app.workers.webhook_tasks.SyncSessionLocal", return_value=mock_ctx):
        with patch("httpx.Client") as mock_client:
            deliver_merchant_webhook.run(str(delivery.id))
            mock_client.assert_not_called()

    assert delivery.status == WebhookDeliveryStatus.FAILED
    mock_session.commit.assert_called_once()


def test_deliver_merchant_not_found_marks_failed() -> None:
    """If merchant row is missing, delivery is marked FAILED immediately."""
    delivery = _make_delivery(status=WebhookDeliveryStatus.PENDING)
    mock_ctx, mock_session = _mock_sync_session(delivery, None)

    with patch("app.workers.webhook_tasks.SyncSessionLocal", return_value=mock_ctx):
        with patch("httpx.Client") as mock_client:
            deliver_merchant_webhook.run(str(delivery.id))
            mock_client.assert_not_called()

    assert delivery.status == WebhookDeliveryStatus.FAILED
    mock_session.commit.assert_called_once()


# ── Task: deliver_merchant_webhook — successful delivery ─────────────────────


def test_deliver_success_marks_delivered_and_increments_attempt_count() -> None:
    delivery = _make_delivery(status=WebhookDeliveryStatus.PENDING, attempt_count=0)
    merchant = _make_merchant()
    mock_ctx, mock_session = _mock_sync_session(delivery, merchant)

    mock_response = _mock_httpx_response(200, is_success=True)
    mock_http = MagicMock()
    mock_http.post.return_value = mock_response
    mock_client_ctx = MagicMock()
    mock_client_ctx.__enter__ = MagicMock(return_value=mock_http)
    mock_client_ctx.__exit__ = MagicMock(return_value=False)

    with patch("app.workers.webhook_tasks.SyncSessionLocal", return_value=mock_ctx):
        with patch("httpx.Client", return_value=mock_client_ctx):
            deliver_merchant_webhook.run(str(delivery.id))

    assert delivery.status == WebhookDeliveryStatus.DELIVERED
    assert delivery.attempt_count == 1
    assert delivery.last_response_code == 200
    assert delivery.next_retry_at is None
    mock_session.commit.assert_called_once()


def test_deliver_success_sends_correct_headers() -> None:
    """Outgoing POST includes Content-Type and X-PayCore-Signature header."""
    delivery = _make_delivery(status=WebhookDeliveryStatus.PENDING)
    merchant = _make_merchant(webhook_secret="my-secret")
    mock_ctx, mock_session = _mock_sync_session(delivery, merchant)

    mock_response = _mock_httpx_response(200, is_success=True)
    mock_http = MagicMock()
    mock_http.post.return_value = mock_response
    mock_client_ctx = MagicMock()
    mock_client_ctx.__enter__ = MagicMock(return_value=mock_http)
    mock_client_ctx.__exit__ = MagicMock(return_value=False)

    with patch("app.workers.webhook_tasks.SyncSessionLocal", return_value=mock_ctx):
        with patch("httpx.Client", return_value=mock_client_ctx):
            deliver_merchant_webhook.run(str(delivery.id))

    _, kwargs = mock_http.post.call_args
    headers = kwargs.get("headers", {})
    assert headers.get("Content-Type") == "application/json"
    signature_header = headers.get("X-PayCore-Signature", "")
    assert signature_header.startswith("sha256=")
    # The signature is 64 hex chars for SHA256
    assert len(signature_header) == len("sha256=") + 64


# ── Task: deliver_merchant_webhook — failures and retries ────────────────────


def test_deliver_http_500_schedules_retry() -> None:
    """A non-2xx response schedules the next retry via next_retry_at."""
    delivery = _make_delivery(status=WebhookDeliveryStatus.PENDING, attempt_count=0)
    merchant = _make_merchant()
    mock_ctx, mock_session = _mock_sync_session(delivery, merchant)

    mock_response = _mock_httpx_response(500, is_success=False)
    mock_http = MagicMock()
    mock_http.post.return_value = mock_response
    mock_client_ctx = MagicMock()
    mock_client_ctx.__enter__ = MagicMock(return_value=mock_http)
    mock_client_ctx.__exit__ = MagicMock(return_value=False)

    now = datetime.now(timezone.utc)
    with patch("app.workers.webhook_tasks.SyncSessionLocal", return_value=mock_ctx):
        with patch("httpx.Client", return_value=mock_client_ctx):
            deliver_merchant_webhook.run(str(delivery.id))

    # Still PENDING, not FAILED — a retry is scheduled
    assert delivery.status == WebhookDeliveryStatus.PENDING
    assert delivery.attempt_count == 1
    assert delivery.last_response_code == 500
    assert delivery.next_retry_at is not None
    # next_retry_at is ~2 minutes from now (_RETRY_DELAYS_MINUTES[1] == 2)
    delta = delivery.next_retry_at - now
    assert timedelta(minutes=1) < delta < timedelta(minutes=4)
    mock_session.commit.assert_called_once()


def test_deliver_network_error_schedules_retry() -> None:
    """httpx.RequestError causes a retry to be scheduled."""
    import httpx

    delivery = _make_delivery(status=WebhookDeliveryStatus.PENDING, attempt_count=0)
    merchant = _make_merchant()
    mock_ctx, mock_session = _mock_sync_session(delivery, merchant)

    mock_http = MagicMock()
    mock_http.post.side_effect = httpx.NetworkError("Connection refused")
    mock_client_ctx = MagicMock()
    mock_client_ctx.__enter__ = MagicMock(return_value=mock_http)
    mock_client_ctx.__exit__ = MagicMock(return_value=False)

    with patch("app.workers.webhook_tasks.SyncSessionLocal", return_value=mock_ctx):
        with patch("httpx.Client", return_value=mock_client_ctx):
            deliver_merchant_webhook.run(str(delivery.id))

    assert delivery.status == WebhookDeliveryStatus.PENDING
    assert delivery.attempt_count == 1
    assert delivery.next_retry_at is not None
    assert "Connection refused" in delivery.last_error


def test_deliver_max_retries_exhausted_marks_failed() -> None:
    """After MAX_ATTEMPTS, status transitions to FAILED with no further retry."""
    # attempt_count = MAX_ATTEMPTS - 1 means this is the last attempt
    delivery = _make_delivery(
        status=WebhookDeliveryStatus.PENDING,
        attempt_count=MAX_ATTEMPTS - 1,
    )
    merchant = _make_merchant()
    mock_ctx, mock_session = _mock_sync_session(delivery, merchant)

    mock_response = _mock_httpx_response(503, is_success=False)
    mock_http = MagicMock()
    mock_http.post.return_value = mock_response
    mock_client_ctx = MagicMock()
    mock_client_ctx.__enter__ = MagicMock(return_value=mock_http)
    mock_client_ctx.__exit__ = MagicMock(return_value=False)

    with patch("app.workers.webhook_tasks.SyncSessionLocal", return_value=mock_ctx):
        with patch("httpx.Client", return_value=mock_client_ctx):
            deliver_merchant_webhook.run(str(delivery.id))

    assert delivery.status == WebhookDeliveryStatus.FAILED
    assert delivery.attempt_count == MAX_ATTEMPTS
    assert delivery.next_retry_at is None
    mock_session.commit.assert_called_once()


# ── Task: retry_pending_webhooks ──────────────────────────────────────────────


def test_retry_sweep_enqueues_overdue_deliveries() -> None:
    """retry_pending_webhooks enqueues deliver tasks for each overdue delivery."""
    overdue_ids = [uuid.uuid4(), uuid.uuid4()]

    mock_result = MagicMock()
    mock_result.all.return_value = [(did,) for did in overdue_ids]

    mock_session = MagicMock()
    mock_session.execute.return_value = mock_result

    mock_ctx = MagicMock()
    mock_ctx.__enter__ = MagicMock(return_value=mock_session)
    mock_ctx.__exit__ = MagicMock(return_value=False)

    with patch("app.workers.webhook_tasks.SyncSessionLocal", return_value=mock_ctx):
        with patch(
            "app.workers.webhook_tasks.deliver_merchant_webhook"
        ) as mock_deliver_task:
            retry_pending_webhooks()

    assert mock_deliver_task.delay.call_count == 2
    called_ids = {c[0][0] for c in mock_deliver_task.delay.call_args_list}
    assert called_ids == {str(did) for did in overdue_ids}


def test_retry_sweep_no_overdue_deliveries_does_nothing() -> None:
    """When there are no overdue deliveries, no tasks are enqueued."""
    mock_result = MagicMock()
    mock_result.all.return_value = []

    mock_session = MagicMock()
    mock_session.execute.return_value = mock_result

    mock_ctx = MagicMock()
    mock_ctx.__enter__ = MagicMock(return_value=mock_session)
    mock_ctx.__exit__ = MagicMock(return_value=False)

    with patch("app.workers.webhook_tasks.SyncSessionLocal", return_value=mock_ctx):
        with patch(
            "app.workers.webhook_tasks.deliver_merchant_webhook"
        ) as mock_deliver_task:
            retry_pending_webhooks()

    mock_deliver_task.delay.assert_not_called()


# ── Service: WebhookDeliveryService ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_and_enqueue_skips_if_no_webhook_url(
    db_session: AsyncSession,
    active_merchant: Merchant,
    funded_transaction: Transaction,
) -> None:
    """create_and_enqueue returns None and does not create a record if no webhook URL."""
    from app.services.webhook_delivery import WebhookDeliveryService
    from sqlalchemy import select

    # Override webhook URL to None
    active_merchant.webhook_url = None
    await db_session.flush()

    service = WebhookDeliveryService(db_session)
    result = await service.create_and_enqueue(
        merchant=active_merchant,
        transaction_id=funded_transaction.id,
        event_type="payment.received",
        payload={"event": "payment.received"},
    )

    assert result is None

    # Verify no record was created
    rows = await db_session.execute(
        select(WebhookDelivery).where(
            WebhookDelivery.transaction_id == funded_transaction.id
        )
    )
    assert rows.scalars().first() is None


@pytest.mark.asyncio
async def test_create_and_enqueue_creates_record_and_enqueues_task(
    db_session: AsyncSession,
    active_merchant: Merchant,
    funded_transaction: Transaction,
) -> None:
    """create_and_enqueue persists a PENDING delivery and dispatches the Celery task.

    The service does `from app.workers.webhook_tasks import deliver_merchant_webhook`
    inside the try block. Patching the module attribute intercepts the lazy import.
    """
    from app.services.webhook_delivery import WebhookDeliveryService

    service = WebhookDeliveryService(db_session)

    # Patch the source module attribute — the lazy `from ... import` in the service
    # looks this up at import time, so the mock is what gets bound locally.
    with patch("app.workers.webhook_tasks.deliver_merchant_webhook") as mock_task:
        result = await service.create_and_enqueue(
            merchant=active_merchant,
            transaction_id=funded_transaction.id,
            event_type="payment.received",
            payload={"event": "payment.received", "data": {}},
        )

    assert result is not None
    assert result.merchant_id == active_merchant.id
    assert result.transaction_id == funded_transaction.id
    assert result.event_type == "payment.received"
    assert result.status == WebhookDeliveryStatus.PENDING
    assert result.attempt_count == 0
    mock_task.delay.assert_called_once_with(str(result.id))


@pytest.mark.asyncio
async def test_mark_delivered_updates_status(
    db_session: AsyncSession,
    pending_delivery: WebhookDelivery,
) -> None:
    from app.services.webhook_delivery import WebhookDeliveryService

    service = WebhookDeliveryService(db_session)
    updated = await service.mark_delivered(pending_delivery, response_code=200)

    assert updated.status == WebhookDeliveryStatus.DELIVERED
    assert updated.last_response_code == 200
    assert updated.last_error is None
    assert updated.next_retry_at is None
    assert updated.attempt_count == 1


@pytest.mark.asyncio
async def test_mark_failed_updates_status(
    db_session: AsyncSession,
    pending_delivery: WebhookDelivery,
) -> None:
    from app.services.webhook_delivery import WebhookDeliveryService

    service = WebhookDeliveryService(db_session)
    updated = await service.mark_failed(
        pending_delivery,
        error="Connection timeout",
        response_code=None,
    )

    assert updated.status == WebhookDeliveryStatus.FAILED
    assert updated.last_error == "Connection timeout"
    assert updated.last_response_code is None
    assert updated.next_retry_at is None


# ── Admin endpoint: GET /api/v1/admin/webhook-deliveries ─────────────────────


@pytest.mark.asyncio
async def test_webhook_deliveries_unauthenticated(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/admin/webhook-deliveries")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_webhook_deliveries_non_admin_forbidden(
    client: AsyncClient,
    test_user: User,
) -> None:
    resp = await client.get(
        "/api/v1/admin/webhook-deliveries",
        headers=make_auth_headers(test_user),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_webhook_deliveries_admin_empty_list(
    client: AsyncClient,
    test_admin: User,
) -> None:
    """Admin can list deliveries; empty list when none exist."""
    resp = await client.get(
        "/api/v1/admin/webhook-deliveries",
        headers=make_auth_headers(test_admin),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    data = body["data"]
    assert data["items"] == []
    assert data["total"] == 0
    assert data["limit"] == 20
    assert data["offset"] == 0


@pytest.mark.asyncio
async def test_webhook_deliveries_admin_returns_records(
    client: AsyncClient,
    db_session: AsyncSession,
    test_admin: User,
    active_merchant: Merchant,
    funded_transaction: Transaction,
    pending_delivery: WebhookDelivery,
    delivered_delivery: WebhookDelivery,
) -> None:
    """Admin endpoint returns all delivery records with correct fields."""
    resp = await client.get(
        "/api/v1/admin/webhook-deliveries",
        headers=make_auth_headers(test_admin),
    )
    assert resp.status_code == 200
    body = resp.json()
    data = body["data"]
    assert data["total"] == 2
    assert len(data["items"]) == 2

    # Validate shape of a record
    item = data["items"][0]
    assert "id" in item
    assert "merchant_id" in item
    assert "transaction_id" in item
    assert "event_type" in item
    assert "status" in item
    assert "attempt_count" in item
    assert "created_at" in item


@pytest.mark.asyncio
async def test_webhook_deliveries_pagination(
    client: AsyncClient,
    db_session: AsyncSession,
    test_admin: User,
    active_merchant: Merchant,
    funded_transaction: Transaction,
    pending_delivery: WebhookDelivery,
    delivered_delivery: WebhookDelivery,
) -> None:
    """Limit and offset query params work correctly."""
    resp = await client.get(
        "/api/v1/admin/webhook-deliveries?limit=1&offset=0",
        headers=make_auth_headers(test_admin),
    )
    assert resp.status_code == 200
    body = resp.json()
    data = body["data"]
    assert data["total"] == 2
    assert len(data["items"]) == 1
    assert data["limit"] == 1
    assert data["offset"] == 0

    # Second page
    resp2 = await client.get(
        "/api/v1/admin/webhook-deliveries?limit=1&offset=1",
        headers=make_auth_headers(test_admin),
    )
    assert resp2.status_code == 200
    body2 = resp2.json()
    data2 = body2["data"]
    assert len(data2["items"]) == 1

    # The two pages have different records
    assert data["items"][0]["id"] != data2["items"][0]["id"]


@pytest.mark.asyncio
async def test_webhook_deliveries_returned_newest_first(
    client: AsyncClient,
    db_session: AsyncSession,
    test_admin: User,
    active_merchant: Merchant,
    funded_transaction: Transaction,
    pending_delivery: WebhookDelivery,
    delivered_delivery: WebhookDelivery,
) -> None:
    """Deliveries are ordered by created_at descending (newest first)."""
    resp = await client.get(
        "/api/v1/admin/webhook-deliveries",
        headers=make_auth_headers(test_admin),
    )
    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    assert len(items) == 2
    # created_at of first item >= second (both are same-session so equal or desc)
    ts0 = items[0]["created_at"]
    ts1 = items[1]["created_at"]
    assert ts0 >= ts1


@pytest.mark.asyncio
async def test_webhook_deliveries_invalid_pagination_rejected(
    client: AsyncClient,
    test_admin: User,
) -> None:
    """Limit=0 or negative offset should be rejected with 422."""
    resp = await client.get(
        "/api/v1/admin/webhook-deliveries?limit=0",
        headers=make_auth_headers(test_admin),
    )
    assert resp.status_code == 422

    resp2 = await client.get(
        "/api/v1/admin/webhook-deliveries?offset=-1",
        headers=make_auth_headers(test_admin),
    )
    assert resp2.status_code == 422


@pytest.mark.asyncio
async def test_webhook_delivery_out_excludes_payload(
    client: AsyncClient,
    db_session: AsyncSession,
    test_admin: User,
    active_merchant: Merchant,
    funded_transaction: Transaction,
    pending_delivery: WebhookDelivery,
) -> None:
    """
    The payload field is not exposed in the admin list endpoint.
    It could be large and is not needed for delivery status monitoring.
    """
    resp = await client.get(
        "/api/v1/admin/webhook-deliveries",
        headers=make_auth_headers(test_admin),
    )
    assert resp.status_code == 200
    item = resp.json()["data"]["items"][0]
    # Payload SHOULD NOT appear in the response schema
    assert "payload" not in item
