"""
Unit tests for TransactionService and TransactionRepository state machine.
"""

from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ValidationError
from app.models.transaction import Transaction, TransactionStatus, TransactionType
from app.models.user import User
from app.models.wallet import Wallet
from app.repositories.transaction import TransactionRepository
from app.schemas.common import PaginatedData
from app.services.transaction import TransactionService

pytestmark = pytest.mark.asyncio


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _create(
    db: AsyncSession,
    user: User,
    *,
    wallet: Wallet | None = None,
    idempotency_key: str | None = None,
    type: TransactionType = TransactionType.FUNDING,
) -> Transaction:
    service = TransactionService(db)
    return await service.create_transaction(
        type=type,
        amount=Decimal("500.00"),
        initiated_by_user_id=user.id,
        destination_wallet_id=wallet.id if wallet else None,
        idempotency_key=idempotency_key,
    )


# ── create_transaction ────────────────────────────────────────────────────────

async def test_create_transaction_returns_pending(
    db_session: AsyncSession, test_user: User, test_wallet: Wallet
) -> None:
    txn = await _create(db_session, test_user, wallet=test_wallet)

    assert isinstance(txn, Transaction)
    assert txn.status == TransactionStatus.PENDING
    assert txn.amount == Decimal("500.00")
    assert txn.type == TransactionType.FUNDING


async def test_create_transaction_reference_format(
    db_session: AsyncSession, test_user: User
) -> None:
    txn = await _create(db_session, test_user)
    assert txn.reference.startswith("txn_")
    # reference = "txn_" + UUID4 standard format (36 chars with dashes)
    assert len(txn.reference) == 40


async def test_create_transaction_reference_is_unique(
    db_session: AsyncSession, test_user: User
) -> None:
    t1 = await _create(db_session, test_user)
    t2 = await _create(db_session, test_user)
    assert t1.reference != t2.reference


async def test_create_transaction_idempotency_returns_existing(
    db_session: AsyncSession, test_user: User
) -> None:
    key = "idem-test-001"
    t1 = await _create(db_session, test_user, idempotency_key=key)
    t2 = await _create(db_session, test_user, idempotency_key=key)

    assert t1.id == t2.id
    assert t1.reference == t2.reference


async def test_create_transaction_no_idempotency_key_always_new(
    db_session: AsyncSession, test_user: User
) -> None:
    t1 = await _create(db_session, test_user)
    t2 = await _create(db_session, test_user)
    assert t1.id != t2.id


# ── State machine ─────────────────────────────────────────────────────────────

async def test_state_machine_pending_to_processing(
    db_session: AsyncSession, test_user: User
) -> None:
    txn = await _create(db_session, test_user)
    repo = TransactionRepository(db_session)
    updated = await repo.update_status(txn, TransactionStatus.PROCESSING)
    assert updated.status == TransactionStatus.PROCESSING


async def test_state_machine_processing_to_completed(
    db_session: AsyncSession, test_user: User
) -> None:
    txn = await _create(db_session, test_user)
    repo = TransactionRepository(db_session)
    await repo.update_status(txn, TransactionStatus.PROCESSING)
    updated = await repo.update_status(txn, TransactionStatus.COMPLETED)
    assert updated.status == TransactionStatus.COMPLETED


async def test_state_machine_processing_to_failed_with_reason(
    db_session: AsyncSession, test_user: User
) -> None:
    txn = await _create(db_session, test_user)
    repo = TransactionRepository(db_session)
    await repo.update_status(txn, TransactionStatus.PROCESSING)
    updated = await repo.update_status(
        txn, TransactionStatus.FAILED, failure_reason="Insufficient balance."
    )
    assert updated.status == TransactionStatus.FAILED
    assert updated.failure_reason == "Insufficient balance."


async def test_state_machine_completed_to_reversed(
    db_session: AsyncSession, test_user: User
) -> None:
    txn = await _create(db_session, test_user)
    repo = TransactionRepository(db_session)
    await repo.update_status(txn, TransactionStatus.PROCESSING)
    await repo.update_status(txn, TransactionStatus.COMPLETED)
    updated = await repo.update_status(txn, TransactionStatus.REVERSED)
    assert updated.status == TransactionStatus.REVERSED


async def test_state_machine_invalid_pending_to_completed_raises(
    db_session: AsyncSession, test_user: User
) -> None:
    """pending → completed is not allowed; must go through processing."""
    txn = await _create(db_session, test_user)
    repo = TransactionRepository(db_session)
    with pytest.raises(ValidationError) as exc_info:
        await repo.update_status(txn, TransactionStatus.COMPLETED)
    assert exc_info.value.error_code == "INVALID_STATUS_TRANSITION"


async def test_state_machine_terminal_failed_cannot_transition(
    db_session: AsyncSession, test_user: User
) -> None:
    """failed is a terminal state — no further transitions allowed."""
    txn = await _create(db_session, test_user)
    repo = TransactionRepository(db_session)
    await repo.update_status(txn, TransactionStatus.PROCESSING)
    await repo.update_status(txn, TransactionStatus.FAILED)
    with pytest.raises(ValidationError):
        await repo.update_status(txn, TransactionStatus.PROCESSING)


async def test_state_machine_terminal_reversed_cannot_transition(
    db_session: AsyncSession, test_user: User
) -> None:
    txn = await _create(db_session, test_user)
    repo = TransactionRepository(db_session)
    await repo.update_status(txn, TransactionStatus.PROCESSING)
    await repo.update_status(txn, TransactionStatus.COMPLETED)
    await repo.update_status(txn, TransactionStatus.REVERSED)
    with pytest.raises(ValidationError):
        await repo.update_status(txn, TransactionStatus.PROCESSING)


async def test_state_machine_update_also_accepts_provider_reference(
    db_session: AsyncSession, test_user: User
) -> None:
    txn = await _create(db_session, test_user)
    repo = TransactionRepository(db_session)
    updated = await repo.update_status(
        txn, TransactionStatus.PROCESSING, provider_reference="ps_ref_abc123"
    )
    assert updated.provider_reference == "ps_ref_abc123"


# ── get_transaction (ownership) ───────────────────────────────────────────────

async def test_get_transaction_by_reference_success(
    db_session: AsyncSession, test_user: User, test_transaction: Transaction
) -> None:
    service = TransactionService(db_session)
    txn = await service.get_transaction(
        test_transaction.reference, requesting_user_id=test_user.id
    )
    assert txn.id == test_transaction.id


async def test_get_transaction_wrong_user_raises_not_found(
    db_session: AsyncSession,
    test_transaction: Transaction,
    test_admin: User,
) -> None:
    """Requesting a transaction you don't own must return NotFoundError."""
    service = TransactionService(db_session)
    with pytest.raises(NotFoundError):
        await service.get_transaction(
            test_transaction.reference, requesting_user_id=test_admin.id
        )


async def test_get_transaction_nonexistent_raises_not_found(
    db_session: AsyncSession, test_user: User
) -> None:
    service = TransactionService(db_session)
    with pytest.raises(NotFoundError):
        await service.get_transaction(
            "txn_does_not_exist", requesting_user_id=test_user.id
        )


# ── list_transactions ─────────────────────────────────────────────────────────

async def test_list_transactions_returns_paginated_data(
    db_session: AsyncSession, test_user: User, test_transaction: Transaction
) -> None:
    service = TransactionService(db_session)
    result = await service.list_transactions(test_user.id, limit=20, offset=0)

    assert isinstance(result, PaginatedData)
    assert result.total == 1
    assert len(result.items) == 1
    assert result.items[0].reference == test_transaction.reference


async def test_list_transactions_empty_for_new_user(
    db_session: AsyncSession, test_user: User
) -> None:
    service = TransactionService(db_session)
    result = await service.list_transactions(test_user.id, limit=20, offset=0)
    assert result.total == 0
    assert result.items == []


async def test_list_transactions_type_filter(
    db_session: AsyncSession, test_user: User, test_wallet: Wallet
) -> None:
    # Create a TRANSFER transaction alongside the default FUNDING one
    await _create(
        db_session, test_user, wallet=test_wallet, type=TransactionType.TRANSFER
    )
    await _create(
        db_session, test_user, wallet=test_wallet, type=TransactionType.FUNDING
    )

    service = TransactionService(db_session)
    result = await service.list_transactions(
        test_user.id,
        limit=20,
        offset=0,
        type_filter=TransactionType.TRANSFER,
    )
    assert result.total == 1
    assert result.items[0].type == TransactionType.TRANSFER.value


async def test_list_transactions_status_filter(
    db_session: AsyncSession, test_user: User, test_wallet: Wallet
) -> None:
    pending = await _create(db_session, test_user, wallet=test_wallet)
    completed = await _create(db_session, test_user, wallet=test_wallet)

    repo = TransactionRepository(db_session)
    await repo.update_status(completed, TransactionStatus.PROCESSING)
    await repo.update_status(completed, TransactionStatus.COMPLETED)

    service = TransactionService(db_session)
    result = await service.list_transactions(
        test_user.id,
        limit=20,
        offset=0,
        status_filter=TransactionStatus.COMPLETED,
    )
    assert result.total == 1
    assert result.items[0].status == TransactionStatus.COMPLETED.value


async def test_list_transactions_pagination_offset(
    db_session: AsyncSession, test_user: User, test_wallet: Wallet
) -> None:
    for _ in range(3):
        await _create(db_session, test_user, wallet=test_wallet)

    service = TransactionService(db_session)
    page1 = await service.list_transactions(test_user.id, limit=2, offset=0)
    page2 = await service.list_transactions(test_user.id, limit=2, offset=2)

    assert page1.total == 3
    assert len(page1.items) == 2
    assert len(page2.items) == 1
