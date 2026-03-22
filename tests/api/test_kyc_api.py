"""
API tests for the KYC component.

User endpoints
--------------
POST /api/v1/kyc/submit    – submit KYC document
GET  /api/v1/kyc/me        – get own submission status

Admin endpoints
---------------
GET  /api/v1/admin/kyc                           – list submissions
GET  /api/v1/admin/kyc/{id}                      – detail + presigned URL
POST /api/v1/admin/kyc/{id}/approve              – approve
POST /api/v1/admin/kyc/{id}/reject               – reject
"""

import io
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.kyc_submission import KYCStatus, KYCSubmission
from app.models.user import User
from app.models.wallet import Wallet
from tests.conftest import make_auth_headers

pytestmark = pytest.mark.asyncio

# ── Helpers ───────────────────────────────────────────────────────────────────

# Minimal valid JPEG (1×1 white pixel magic bytes + padding)
_JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 16
_PDF_BYTES = b"%PDF-1.4\n" + b"\x00" * 16
_INVALID_BYTES = b"GIF89a" + b"\x00" * 16  # GIF — not allowed

_FAKE_S3_KEY = "kyc/test-user/test-sub/doc.jpg"
_FAKE_PRESIGNED_URL = "https://s3.example.com/kyc/test-user/test-sub/doc.jpg?X-Amz-Signature=abc"


def _storage_mock(upload_return: str = _FAKE_S3_KEY, url_return: str = _FAKE_PRESIGNED_URL):
    """Patch StorageService inside KYCService so no real S3 calls are made."""
    mock = AsyncMock()
    mock.upload_file = AsyncMock(return_value=upload_return)
    mock.get_presigned_url = AsyncMock(return_value=url_return)
    return mock


async def _make_submission(
    db: AsyncSession,
    user: User,
    *,
    requested_tier: int = 1,
    status: KYCStatus = KYCStatus.PENDING,
    document_key: str = _FAKE_S3_KEY,
) -> KYCSubmission:
    sub = KYCSubmission(
        id=uuid.uuid4(),
        user_id=user.id,
        requested_tier=requested_tier,
        status=status,
        document_key=document_key,
    )
    db.add(sub)
    await db.flush()
    await db.refresh(sub)
    return sub


# ── POST /kyc/submit ──────────────────────────────────────────────────────────


async def test_submit_kyc_unauthenticated_returns_401(client: AsyncClient) -> None:
    response = await client.post("/api/v1/kyc/submit")
    assert response.status_code == 401


async def test_submit_kyc_creates_pending_submission(
    client: AsyncClient, test_user: User
) -> None:
    with patch("app.services.kyc.StorageService") as MockStorage:
        MockStorage.return_value = _storage_mock()
        response = await client.post(
            "/api/v1/kyc/submit",
            headers=make_auth_headers(test_user),
            files={"document": ("id.jpg", io.BytesIO(_JPEG_BYTES), "image/jpeg")},
            data={"target_tier": "1"},
        )

    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    assert body["data"]["status"] == "pending"
    assert body["data"]["requested_tier"] == 1
    assert "document_key" not in body["data"]  # never exposed to users


async def test_submit_kyc_invalid_file_type_returns_422(
    client: AsyncClient, test_user: User
) -> None:
    with patch("app.services.kyc.StorageService") as MockStorage:
        MockStorage.return_value = _storage_mock()
        response = await client.post(
            "/api/v1/kyc/submit",
            headers=make_auth_headers(test_user),
            files={"document": ("image.gif", io.BytesIO(_INVALID_BYTES), "image/gif")},
            data={"target_tier": "1"},
        )

    assert response.status_code == 422
    assert response.json()["error"] == "INVALID_FILE_TYPE"


async def test_submit_kyc_file_too_large_returns_422(
    client: AsyncClient, test_user: User
) -> None:
    oversized = b"\xff\xd8\xff" + b"\x00" * (5 * 1024 * 1024 + 1)  # 5MB + 1 byte
    with patch("app.services.kyc.StorageService") as MockStorage:
        MockStorage.return_value = _storage_mock()
        response = await client.post(
            "/api/v1/kyc/submit",
            headers=make_auth_headers(test_user),
            files={"document": ("large.jpg", io.BytesIO(oversized), "image/jpeg")},
            data={"target_tier": "1"},
        )

    assert response.status_code == 422
    assert response.json()["error"] == "FILE_TOO_LARGE"


async def test_submit_kyc_cannot_skip_tier(
    client: AsyncClient, test_user: User
) -> None:
    # test_user is kyc_tier=0; applying for Tier 2 (skip Tier 1) must fail
    with patch("app.services.kyc.StorageService") as MockStorage:
        MockStorage.return_value = _storage_mock()
        response = await client.post(
            "/api/v1/kyc/submit",
            headers=make_auth_headers(test_user),
            files={"document": ("id.jpg", io.BytesIO(_JPEG_BYTES), "image/jpeg")},
            data={"target_tier": "2"},
        )

    assert response.status_code == 422
    assert response.json()["error"] == "KYC_TIER_SKIP"


async def test_submit_kyc_double_pending_returns_409(
    client: AsyncClient,
    db_session: AsyncSession,
    test_user: User,
) -> None:
    # Insert an existing PENDING submission for Tier 1
    await _make_submission(db_session, test_user, requested_tier=1, status=KYCStatus.PENDING)

    with patch("app.services.kyc.StorageService") as MockStorage:
        MockStorage.return_value = _storage_mock()
        response = await client.post(
            "/api/v1/kyc/submit",
            headers=make_auth_headers(test_user),
            files={"document": ("id.jpg", io.BytesIO(_JPEG_BYTES), "image/jpeg")},
            data={"target_tier": "1"},
        )

    assert response.status_code == 409
    assert response.json()["error"] == "KYC_SUBMISSION_PENDING"


async def test_submit_kyc_can_resubmit_after_rejection(
    client: AsyncClient,
    db_session: AsyncSession,
    test_user: User,
) -> None:
    # Prior REJECTED submission — should allow a fresh submission
    await _make_submission(db_session, test_user, requested_tier=1, status=KYCStatus.REJECTED)

    with patch("app.services.kyc.StorageService") as MockStorage:
        MockStorage.return_value = _storage_mock()
        response = await client.post(
            "/api/v1/kyc/submit",
            headers=make_auth_headers(test_user),
            files={"document": ("id.jpg", io.BytesIO(_JPEG_BYTES), "image/jpeg")},
            data={"target_tier": "1"},
        )

    assert response.status_code == 201
    assert response.json()["data"]["status"] == "pending"


async def test_submit_kyc_s3_failure_returns_503_no_db_record(
    client: AsyncClient,
    db_session: AsyncSession,
    test_user: User,
) -> None:
    from app.core.exceptions import ExternalServiceError

    with patch("app.services.kyc.StorageService") as MockStorage:
        mock_instance = MockStorage.return_value
        mock_instance.upload_file = AsyncMock(
            side_effect=ExternalServiceError("Document storage")
        )
        response = await client.post(
            "/api/v1/kyc/submit",
            headers=make_auth_headers(test_user),
            files={"document": ("id.jpg", io.BytesIO(_JPEG_BYTES), "image/jpeg")},
            data={"target_tier": "1"},
        )

    assert response.status_code == 503
    # No DB record created after S3 failure
    from sqlalchemy import select
    from app.models.kyc_submission import KYCSubmission as KYCSub
    count = (
        await db_session.execute(
            select(KYCSub).where(KYCSub.user_id == test_user.id)
        )
    ).scalars().all()
    assert len(count) == 0


# ── GET /kyc/me ───────────────────────────────────────────────────────────────


async def test_get_my_kyc_unauthenticated_returns_401(client: AsyncClient) -> None:
    response = await client.get("/api/v1/kyc/me")
    assert response.status_code == 401


async def test_get_my_kyc_not_found_returns_404(
    client: AsyncClient, test_user: User
) -> None:
    response = await client.get(
        "/api/v1/kyc/me", headers=make_auth_headers(test_user)
    )
    assert response.status_code == 404


async def test_get_my_kyc_returns_latest_submission(
    client: AsyncClient,
    db_session: AsyncSession,
    test_user: User,
) -> None:
    sub = await _make_submission(db_session, test_user, requested_tier=1)
    response = await client.get(
        "/api/v1/kyc/me", headers=make_auth_headers(test_user)
    )
    body = response.json()
    assert response.status_code == 200
    assert body["data"]["id"] == str(sub.id)
    assert body["data"]["status"] == "pending"
    assert "document_key" not in body["data"]


# ── Admin: GET /admin/kyc ─────────────────────────────────────────────────────


async def test_admin_list_kyc_non_admin_returns_403(
    client: AsyncClient, test_user: User
) -> None:
    response = await client.get(
        "/api/v1/admin/kyc", headers=make_auth_headers(test_user)
    )
    assert response.status_code == 403


async def test_admin_list_kyc_unauthenticated_returns_401(
    client: AsyncClient,
) -> None:
    response = await client.get("/api/v1/admin/kyc")
    assert response.status_code == 401


async def test_admin_list_kyc_returns_pending_by_default(
    client: AsyncClient,
    db_session: AsyncSession,
    test_user: User,
    test_admin: User,
) -> None:
    pending = await _make_submission(db_session, test_user, status=KYCStatus.PENDING)
    await _make_submission(db_session, test_user, status=KYCStatus.REJECTED)

    response = await client.get(
        "/api/v1/admin/kyc", headers=make_auth_headers(test_admin)
    )
    body = response.json()
    assert response.status_code == 200
    assert body["data"]["total"] == 1
    assert body["data"]["items"][0]["id"] == str(pending.id)


async def test_admin_list_kyc_filter_by_status(
    client: AsyncClient,
    db_session: AsyncSession,
    test_user: User,
    test_admin: User,
) -> None:
    await _make_submission(db_session, test_user, status=KYCStatus.PENDING)
    approved = await _make_submission(db_session, test_admin, requested_tier=1, status=KYCStatus.APPROVED)

    response = await client.get(
        "/api/v1/admin/kyc?status=approved",
        headers=make_auth_headers(test_admin),
    )
    body = response.json()
    assert body["data"]["total"] == 1
    assert body["data"]["items"][0]["id"] == str(approved.id)


# ── Admin: GET /admin/kyc/{id} ────────────────────────────────────────────────


async def test_admin_get_kyc_submission_includes_presigned_url(
    client: AsyncClient,
    db_session: AsyncSession,
    test_user: User,
    test_admin: User,
) -> None:
    sub = await _make_submission(db_session, test_user)

    with patch("app.services.kyc.StorageService") as MockStorage:
        MockStorage.return_value = _storage_mock(url_return=_FAKE_PRESIGNED_URL)
        response = await client.get(
            f"/api/v1/admin/kyc/{sub.id}",
            headers=make_auth_headers(test_admin),
        )

    body = response.json()
    assert response.status_code == 200
    assert body["data"]["document_url"] == _FAKE_PRESIGNED_URL
    assert "document_key" not in body["data"]


async def test_admin_get_kyc_not_found_returns_404(
    client: AsyncClient, test_admin: User
) -> None:
    response = await client.get(
        f"/api/v1/admin/kyc/{uuid.uuid4()}",
        headers=make_auth_headers(test_admin),
    )
    assert response.status_code == 404


# ── Admin: POST /admin/kyc/{id}/approve ──────────────────────────────────────


async def test_admin_approve_upgrades_user_tier(
    client: AsyncClient,
    db_session: AsyncSession,
    test_user: User,
    test_admin: User,
) -> None:
    sub = await _make_submission(db_session, test_user, requested_tier=1)

    with patch("app.services.kyc.StorageService"):
        response = await client.post(
            f"/api/v1/admin/kyc/{sub.id}/approve",
            headers=make_auth_headers(test_admin),
        )

    body = response.json()
    assert response.status_code == 200
    assert body["data"]["status"] == "approved"

    # Confirm user's kyc_tier was promoted
    await db_session.refresh(test_user)
    assert test_user.kyc_tier == 1


async def test_admin_approve_already_approved_returns_409(
    client: AsyncClient,
    db_session: AsyncSession,
    test_user: User,
    test_admin: User,
) -> None:
    sub = await _make_submission(db_session, test_user, status=KYCStatus.APPROVED)

    with patch("app.services.kyc.StorageService"):
        response = await client.post(
            f"/api/v1/admin/kyc/{sub.id}/approve",
            headers=make_auth_headers(test_admin),
        )

    assert response.status_code == 409
    assert response.json()["error"] == "KYC_ALREADY_APPROVED"


async def test_admin_cannot_approve_own_submission(
    client: AsyncClient,
    db_session: AsyncSession,
    test_admin: User,
) -> None:
    # Admin submitted their own KYC — they cannot approve it themselves
    sub = await _make_submission(db_session, test_admin, requested_tier=1)

    with patch("app.services.kyc.StorageService"):
        response = await client.post(
            f"/api/v1/admin/kyc/{sub.id}/approve",
            headers=make_auth_headers(test_admin),
        )

    assert response.status_code == 403


# ── Admin: POST /admin/kyc/{id}/reject ───────────────────────────────────────


async def test_admin_reject_sets_status_and_reason(
    client: AsyncClient,
    db_session: AsyncSession,
    test_user: User,
    test_admin: User,
) -> None:
    sub = await _make_submission(db_session, test_user, requested_tier=1)
    reason = "Document image is too blurry to verify identity."

    with patch("app.services.kyc.StorageService"):
        response = await client.post(
            f"/api/v1/admin/kyc/{sub.id}/reject",
            headers=make_auth_headers(test_admin),
            json={"reason": reason},
        )

    body = response.json()
    assert response.status_code == 200
    assert body["data"]["status"] == "rejected"
    assert body["data"]["rejection_reason"] == reason


async def test_admin_reject_non_pending_returns_409(
    client: AsyncClient,
    db_session: AsyncSession,
    test_user: User,
    test_admin: User,
) -> None:
    sub = await _make_submission(db_session, test_user, status=KYCStatus.APPROVED)

    with patch("app.services.kyc.StorageService"):
        response = await client.post(
            f"/api/v1/admin/kyc/{sub.id}/reject",
            headers=make_auth_headers(test_admin),
            json={"reason": "Rejected for testing purposes, enough chars."},
        )

    assert response.status_code == 409


async def test_admin_reject_reason_too_short_returns_422(
    client: AsyncClient,
    db_session: AsyncSession,
    test_user: User,
    test_admin: User,
) -> None:
    sub = await _make_submission(db_session, test_user)

    response = await client.post(
        f"/api/v1/admin/kyc/{sub.id}/reject",
        headers=make_auth_headers(test_admin),
        json={"reason": "short"},  # < 10 chars
    )
    assert response.status_code == 422


async def test_admin_cannot_reject_own_submission(
    client: AsyncClient,
    db_session: AsyncSession,
    test_admin: User,
) -> None:
    sub = await _make_submission(db_session, test_admin, requested_tier=1)

    with patch("app.services.kyc.StorageService"):
        response = await client.post(
            f"/api/v1/admin/kyc/{sub.id}/reject",
            headers=make_auth_headers(test_admin),
            json={"reason": "This is a test rejection reason, long enough."},
        )

    assert response.status_code == 403


# ── Full approval flow ────────────────────────────────────────────────────────


async def test_full_submission_approve_tier_upgrade_flow(
    client: AsyncClient,
    db_session: AsyncSession,
    test_user: User,
    test_admin: User,
) -> None:
    """End-to-end: user submits → admin approves → user's tier becomes 1."""
    assert test_user.kyc_tier == 0

    # 1. User submits
    with patch("app.services.kyc.StorageService") as MockStorage:
        MockStorage.return_value = _storage_mock()
        submit_resp = await client.post(
            "/api/v1/kyc/submit",
            headers=make_auth_headers(test_user),
            files={"document": ("passport.jpg", io.BytesIO(_JPEG_BYTES), "image/jpeg")},
            data={"target_tier": "1"},
        )
    assert submit_resp.status_code == 201
    submission_id = submit_resp.json()["data"]["id"]

    # 2. Admin approves
    with patch("app.services.kyc.StorageService"):
        approve_resp = await client.post(
            f"/api/v1/admin/kyc/{submission_id}/approve",
            headers=make_auth_headers(test_admin),
        )
    assert approve_resp.status_code == 200
    assert approve_resp.json()["data"]["status"] == "approved"

    # 3. User's tier was upgraded
    await db_session.refresh(test_user)
    assert test_user.kyc_tier == 1


async def test_full_rejection_and_resubmit_flow(
    client: AsyncClient,
    db_session: AsyncSession,
    test_user: User,
    test_admin: User,
) -> None:
    """End-to-end: submit → reject → resubmit is allowed."""
    # 1. Submit
    with patch("app.services.kyc.StorageService") as MockStorage:
        MockStorage.return_value = _storage_mock()
        resp1 = await client.post(
            "/api/v1/kyc/submit",
            headers=make_auth_headers(test_user),
            files={"document": ("id.pdf", io.BytesIO(_PDF_BYTES), "application/pdf")},
            data={"target_tier": "1"},
        )
    assert resp1.status_code == 201
    submission_id = resp1.json()["data"]["id"]

    # 2. Admin rejects
    with patch("app.services.kyc.StorageService"):
        reject_resp = await client.post(
            f"/api/v1/admin/kyc/{submission_id}/reject",
            headers=make_auth_headers(test_admin),
            json={"reason": "Document is expired. Please submit a valid ID."},
        )
    assert reject_resp.status_code == 200

    # 3. User resubmits — must succeed, not conflict
    with patch("app.services.kyc.StorageService") as MockStorage:
        MockStorage.return_value = _storage_mock()
        resp2 = await client.post(
            "/api/v1/kyc/submit",
            headers=make_auth_headers(test_user),
            files={"document": ("new_id.jpg", io.BytesIO(_JPEG_BYTES), "image/jpeg")},
            data={"target_tier": "1"},
        )
    assert resp2.status_code == 201
    assert resp2.json()["data"]["id"] != submission_id  # new submission
