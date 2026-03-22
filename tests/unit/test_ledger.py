"""
Unit tests for LedgerService and LedgerRepository.

post_double_entry
-----------------
- Creates exactly 2 entries
- Debit entry has EntryType.DEBIT, credit entry has EntryType.CREDIT
- Both entries reference the correct transaction_id
- amount field matches the supplied amount
- balance_after fields reflect the supplied snapshots
- Both entries reference the correct wallet IDs
- currency is propagated to both entries

get_by_transaction
------------------
- Returns both entries (debit + credit)
- Returns an empty list when no entries exist for a transaction
- Result is ordered by created_at ASC

get_by_wallet
-------------
- Returns entries belonging to the given wallet
- Does NOT return entries for a different wallet
- Pagination (limit/offset) works correctly
- Returns correct total count

Immutability
------------
- LedgerRepository exposes no update or delete methods
"""

from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ledger_entry import EntryType, LedgerEntry
from app.models.transaction import Transaction
from app.models.user import User
from app.models.wallet import Wallet
from app.repositories.ledger import LedgerRepository
from app.services.ledger import LedgerService

pytestmark = pytest.mark.asyncio


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _post(
    db: AsyncSession,
    transaction: Transaction,
    debit_wallet: Wallet,
    credit_wallet: Wallet,
    *,
    amount: Decimal = Decimal("500.00"),
    debit_balance_after: Decimal = Decimal("500.00"),
    credit_balance_after: Decimal = Decimal("1500.00"),
) -> tuple[LedgerEntry, LedgerEntry]:
    service = LedgerService(db)
    return await service.post_double_entry(
        transaction_id=transaction.id,
        debit_wallet_id=debit_wallet.id,
        credit_wallet_id=credit_wallet.id,
        amount=amount,
        currency="NGN",
        debit_balance_after=debit_balance_after,
        credit_balance_after=credit_balance_after,
    )


# ── post_double_entry ─────────────────────────────────────────────────────────

async def test_post_double_entry_returns_two_entries(
    db_session: AsyncSession,
    test_transaction: Transaction,
    test_wallet: Wallet,
    test_admin: User,
) -> None:
    """post_double_entry must return exactly a (debit, credit) 2-tuple."""
    import uuid

    admin_wallet = Wallet(
        id=uuid.uuid4(),
        user_id=test_admin.id,
        currency="NGN",
        balance=Decimal("2000.00"),
        is_active=True,
    )
    db_session.add(admin_wallet)
    await db_session.flush()

    debit, credit = await _post(
        db_session, test_transaction, admin_wallet, test_wallet
    )

    assert isinstance(debit, LedgerEntry)
    assert isinstance(credit, LedgerEntry)


async def test_post_double_entry_correct_entry_types(
    db_session: AsyncSession,
    test_transaction: Transaction,
    test_wallet: Wallet,
    test_admin: User,
) -> None:
    import uuid

    admin_wallet = Wallet(
        id=uuid.uuid4(),
        user_id=test_admin.id,
        currency="NGN",
        balance=Decimal("2000.00"),
        is_active=True,
    )
    db_session.add(admin_wallet)
    await db_session.flush()

    debit, credit = await _post(
        db_session, test_transaction, admin_wallet, test_wallet
    )

    assert debit.entry_type == EntryType.DEBIT
    assert credit.entry_type == EntryType.CREDIT


async def test_post_double_entry_correct_wallet_ids(
    db_session: AsyncSession,
    test_transaction: Transaction,
    test_wallet: Wallet,
    test_admin: User,
) -> None:
    import uuid

    admin_wallet = Wallet(
        id=uuid.uuid4(),
        user_id=test_admin.id,
        currency="NGN",
        balance=Decimal("2000.00"),
        is_active=True,
    )
    db_session.add(admin_wallet)
    await db_session.flush()

    debit, credit = await _post(
        db_session, test_transaction, admin_wallet, test_wallet
    )

    assert debit.wallet_id == admin_wallet.id
    assert credit.wallet_id == test_wallet.id


async def test_post_double_entry_correct_transaction_id(
    db_session: AsyncSession,
    test_transaction: Transaction,
    test_wallet: Wallet,
    test_admin: User,
) -> None:
    import uuid

    admin_wallet = Wallet(
        id=uuid.uuid4(),
        user_id=test_admin.id,
        currency="NGN",
        balance=Decimal("2000.00"),
        is_active=True,
    )
    db_session.add(admin_wallet)
    await db_session.flush()

    debit, credit = await _post(
        db_session, test_transaction, admin_wallet, test_wallet
    )

    assert debit.transaction_id == test_transaction.id
    assert credit.transaction_id == test_transaction.id


async def test_post_double_entry_amount_matches(
    db_session: AsyncSession,
    test_transaction: Transaction,
    test_wallet: Wallet,
    test_admin: User,
) -> None:
    import uuid

    admin_wallet = Wallet(
        id=uuid.uuid4(),
        user_id=test_admin.id,
        currency="NGN",
        balance=Decimal("2000.00"),
        is_active=True,
    )
    db_session.add(admin_wallet)
    await db_session.flush()

    posted_amount = Decimal("750.00")
    debit, credit = await _post(
        db_session,
        test_transaction,
        admin_wallet,
        test_wallet,
        amount=posted_amount,
    )

    assert debit.amount == posted_amount
    assert credit.amount == posted_amount


async def test_post_double_entry_balance_after_snapshots(
    db_session: AsyncSession,
    test_transaction: Transaction,
    test_wallet: Wallet,
    test_admin: User,
) -> None:
    import uuid

    admin_wallet = Wallet(
        id=uuid.uuid4(),
        user_id=test_admin.id,
        currency="NGN",
        balance=Decimal("2000.00"),
        is_active=True,
    )
    db_session.add(admin_wallet)
    await db_session.flush()

    debit_snap = Decimal("1250.00")
    credit_snap = Decimal("750.00")

    debit, credit = await _post(
        db_session,
        test_transaction,
        admin_wallet,
        test_wallet,
        amount=Decimal("750.00"),
        debit_balance_after=debit_snap,
        credit_balance_after=credit_snap,
    )

    assert debit.balance_after == debit_snap
    assert credit.balance_after == credit_snap


async def test_post_double_entry_currency_propagated(
    db_session: AsyncSession,
    test_transaction: Transaction,
    test_wallet: Wallet,
    test_admin: User,
) -> None:
    import uuid

    admin_wallet = Wallet(
        id=uuid.uuid4(),
        user_id=test_admin.id,
        currency="NGN",
        balance=Decimal("2000.00"),
        is_active=True,
    )
    db_session.add(admin_wallet)
    await db_session.flush()

    debit, credit = await _post(
        db_session, test_transaction, admin_wallet, test_wallet
    )

    assert debit.currency == "NGN"
    assert credit.currency == "NGN"


# ── get_by_transaction ────────────────────────────────────────────────────────

async def test_get_by_transaction_returns_both_entries(
    db_session: AsyncSession,
    test_transaction: Transaction,
    test_wallet: Wallet,
    test_admin: User,
) -> None:
    import uuid

    admin_wallet = Wallet(
        id=uuid.uuid4(),
        user_id=test_admin.id,
        currency="NGN",
        balance=Decimal("2000.00"),
        is_active=True,
    )
    db_session.add(admin_wallet)
    await db_session.flush()

    await _post(db_session, test_transaction, admin_wallet, test_wallet)

    repo = LedgerRepository(db_session)
    entries = await repo.get_by_transaction(test_transaction.id)

    assert len(entries) == 2
    types = {e.entry_type for e in entries}
    assert types == {EntryType.DEBIT, EntryType.CREDIT}


async def test_get_by_transaction_empty_when_no_entries(
    db_session: AsyncSession,
    test_transaction: Transaction,
) -> None:
    repo = LedgerRepository(db_session)
    entries = await repo.get_by_transaction(test_transaction.id)
    assert entries == []


async def test_get_by_transaction_ordered_asc(
    db_session: AsyncSession,
    test_transaction: Transaction,
    test_wallet: Wallet,
    test_admin: User,
) -> None:
    """get_by_transaction should return entries ordered by created_at ASC."""
    import uuid

    admin_wallet = Wallet(
        id=uuid.uuid4(),
        user_id=test_admin.id,
        currency="NGN",
        balance=Decimal("2000.00"),
        is_active=True,
    )
    db_session.add(admin_wallet)
    await db_session.flush()

    await _post(db_session, test_transaction, admin_wallet, test_wallet)

    repo = LedgerRepository(db_session)
    entries = await repo.get_by_transaction(test_transaction.id)

    assert len(entries) == 2
    assert entries[0].created_at <= entries[1].created_at


# ── get_by_wallet ─────────────────────────────────────────────────────────────

async def test_get_by_wallet_returns_own_entries(
    db_session: AsyncSession,
    test_transaction: Transaction,
    test_wallet: Wallet,
    test_admin: User,
) -> None:
    import uuid

    admin_wallet = Wallet(
        id=uuid.uuid4(),
        user_id=test_admin.id,
        currency="NGN",
        balance=Decimal("2000.00"),
        is_active=True,
    )
    db_session.add(admin_wallet)
    await db_session.flush()

    await _post(db_session, test_transaction, admin_wallet, test_wallet)

    repo = LedgerRepository(db_session)
    entries, total = await repo.get_by_wallet(test_wallet.id, limit=20, offset=0)

    assert total == 1
    assert len(entries) == 1
    assert entries[0].wallet_id == test_wallet.id
    assert entries[0].entry_type == EntryType.CREDIT


async def test_get_by_wallet_excludes_other_wallets(
    db_session: AsyncSession,
    test_transaction: Transaction,
    test_wallet: Wallet,
    test_admin: User,
) -> None:
    import uuid

    admin_wallet = Wallet(
        id=uuid.uuid4(),
        user_id=test_admin.id,
        currency="NGN",
        balance=Decimal("2000.00"),
        is_active=True,
    )
    db_session.add(admin_wallet)
    await db_session.flush()

    await _post(db_session, test_transaction, admin_wallet, test_wallet)

    repo = LedgerRepository(db_session)
    # admin_wallet is the debit side — credit entry belongs to test_wallet
    entries, total = await repo.get_by_wallet(admin_wallet.id, limit=20, offset=0)

    assert total == 1
    assert entries[0].entry_type == EntryType.DEBIT
    for e in entries:
        assert e.wallet_id == admin_wallet.id


async def test_get_by_wallet_empty_for_no_entries(
    db_session: AsyncSession,
    test_wallet: Wallet,
) -> None:
    repo = LedgerRepository(db_session)
    entries, total = await repo.get_by_wallet(test_wallet.id, limit=20, offset=0)
    assert total == 0
    assert entries == []


async def test_get_by_wallet_pagination(
    db_session: AsyncSession,
    test_wallet: Wallet,
    test_admin: User,
) -> None:
    """
    Create 3 entries against test_wallet (as credit side), then verify
    that limit/offset slicing and total count are correct.
    """
    import uuid
    from app.models.transaction import Transaction as TxnModel, TransactionType, TransactionStatus

    admin_wallet = Wallet(
        id=uuid.uuid4(),
        user_id=test_admin.id,
        currency="NGN",
        balance=Decimal("5000.00"),
        is_active=True,
    )
    db_session.add(admin_wallet)
    await db_session.flush()

    service = LedgerService(db_session)
    for i in range(3):
        txn = TxnModel(
            reference=f"txn_ledger_pg_{i}_{uuid.uuid4().hex[:8]}",
            type=TransactionType.FUNDING,
            status=TransactionStatus.COMPLETED,
            amount=Decimal("100.00"),
            currency="NGN",
            destination_wallet_id=test_wallet.id,
            initiated_by_user_id=test_admin.id,
        )
        db_session.add(txn)
        await db_session.flush()

        await service.post_double_entry(
            transaction_id=txn.id,
            debit_wallet_id=admin_wallet.id,
            credit_wallet_id=test_wallet.id,
            amount=Decimal("100.00"),
            currency="NGN",
            debit_balance_after=Decimal("5000.00") - Decimal("100.00") * (i + 1),
            credit_balance_after=Decimal("100.00") * (i + 1),
        )

    repo = LedgerRepository(db_session)

    page1, total = await repo.get_by_wallet(test_wallet.id, limit=2, offset=0)
    assert total == 3
    assert len(page1) == 2

    page2, total2 = await repo.get_by_wallet(test_wallet.id, limit=2, offset=2)
    assert total2 == 3
    assert len(page2) == 1

    # No page overlap
    ids1 = {e.id for e in page1}
    ids2 = {e.id for e in page2}
    assert ids1.isdisjoint(ids2)


# ── Immutability contract ─────────────────────────────────────────────────────

def test_ledger_repository_has_no_update_method() -> None:
    """LedgerRepository must not expose any update or delete method."""
    assert not hasattr(LedgerRepository, "update")
    assert not hasattr(LedgerRepository, "update_entry")
    assert not hasattr(LedgerRepository, "delete")
    assert not hasattr(LedgerRepository, "delete_entry")
    assert not hasattr(LedgerRepository, "soft_delete")
