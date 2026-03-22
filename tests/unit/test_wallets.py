"""
Unit tests for WalletService.

Each test runs inside a rolled-back transaction — see conftest.py for the
savepoint-based isolation strategy.
"""

from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.models.user import User
from app.models.wallet import Wallet
from app.schemas.common import PaginatedData
from app.services.wallet import WalletService

pytestmark = pytest.mark.asyncio


# ── create_wallet ─────────────────────────────────────────────────────────────

async def test_create_wallet_success(db_session: AsyncSession, test_user: User) -> None:
    service = WalletService(db_session)
    wallet = await service.create_wallet(test_user.id)

    assert isinstance(wallet, Wallet)
    assert wallet.user_id == test_user.id
    assert wallet.currency == "NGN"
    assert wallet.balance == Decimal("0.00")
    assert wallet.is_active is True


async def test_create_wallet_default_currency_is_ngn(
    db_session: AsyncSession, test_user: User
) -> None:
    service = WalletService(db_session)
    wallet = await service.create_wallet(test_user.id)
    assert wallet.currency == "NGN"


async def test_create_wallet_duplicate_raises_conflict(
    db_session: AsyncSession, test_user: User, test_wallet: Wallet
) -> None:
    """Second wallet creation for the same user must raise ConflictError."""
    service = WalletService(db_session)
    with pytest.raises(ConflictError) as exc_info:
        await service.create_wallet(test_user.id)

    assert exc_info.value.error_code == "WALLET_ALREADY_EXISTS"


# ── get_wallet ────────────────────────────────────────────────────────────────

async def test_get_wallet_success(
    db_session: AsyncSession, test_user: User, test_wallet: Wallet
) -> None:
    service = WalletService(db_session)
    wallet = await service.get_wallet(test_user.id)

    assert wallet.id == test_wallet.id
    assert wallet.user_id == test_user.id


async def test_get_wallet_not_found_raises_not_found_error(
    db_session: AsyncSession, test_user: User
) -> None:
    """User exists but has no wallet — NotFoundError must be raised."""
    service = WalletService(db_session)
    with pytest.raises(NotFoundError):
        await service.get_wallet(test_user.id)


# ── get_balance ───────────────────────────────────────────────────────────────

async def test_get_balance_returns_decimal(
    db_session: AsyncSession, test_user: User, test_wallet: Wallet
) -> None:
    service = WalletService(db_session)
    balance = await service.get_balance(test_user.id)

    assert isinstance(balance, Decimal)
    assert balance == Decimal("0.00")


async def test_get_balance_reflects_current_balance(
    db_session: AsyncSession, test_user: User, test_wallet: Wallet
) -> None:
    """After a direct balance mutation the service reads the updated value."""
    from app.repositories.wallet import WalletRepository

    repo = WalletRepository(db_session)
    await repo.update_balance(test_wallet, Decimal("500.00"))

    service = WalletService(db_session)
    balance = await service.get_balance(test_user.id)
    assert balance == Decimal("500.00")


# ── get_statement ─────────────────────────────────────────────────────────────

async def test_get_statement_returns_paginated_data(
    db_session: AsyncSession, test_user: User, test_wallet: Wallet
) -> None:
    service = WalletService(db_session)
    result = await service.get_statement(test_user.id, limit=20, offset=0)

    assert isinstance(result, PaginatedData)
    assert result.items == []
    assert result.total == 0
    assert result.limit == 20
    assert result.offset == 0


async def test_get_statement_respects_pagination_params(
    db_session: AsyncSession, test_user: User, test_wallet: Wallet
) -> None:
    service = WalletService(db_session)
    result = await service.get_statement(test_user.id, limit=10, offset=5)

    assert result.limit == 10
    assert result.offset == 5


async def test_get_statement_wallet_not_found_raises(
    db_session: AsyncSession, test_user: User
) -> None:
    """Statement call without a wallet must raise NotFoundError."""
    service = WalletService(db_session)
    with pytest.raises(NotFoundError):
        await service.get_statement(test_user.id, limit=20, offset=0)


# ── assert_wallet_active ──────────────────────────────────────────────────────

async def test_assert_wallet_active_passes_when_active(
    db_session: AsyncSession, test_user: User, test_wallet: Wallet
) -> None:
    service = WalletService(db_session)
    wallet = await service.assert_wallet_active(test_user.id)
    assert wallet.id == test_wallet.id


async def test_assert_wallet_active_raises_forbidden_when_inactive(
    db_session: AsyncSession, test_user: User, test_wallet: Wallet
) -> None:
    from app.repositories.wallet import WalletRepository

    # Deactivate the wallet
    repo = WalletRepository(db_session)
    await repo.set_active(test_wallet, active=False)

    service = WalletService(db_session)
    with pytest.raises(ForbiddenError):
        await service.assert_wallet_active(test_user.id)
