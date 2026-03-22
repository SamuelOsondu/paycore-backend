"""
Shared test fixtures for PayCore.

Isolation strategy
------------------
AuthService (and other write services) call ``session.commit()`` to persist
data.  A simple ``session.rollback()`` after the test would be a no-op for
already-committed rows and would leave stale data in the DB.

Instead we use SQLAlchemy 2.0's ``join_transaction_mode="create_savepoint"``:

1. Each test opens a *real* connection and begins an outer transaction.
2. The session joins that connection with ``create_savepoint`` mode, so every
   ``session.commit()`` inside production code becomes a *savepoint* release
   rather than a true COMMIT.
3. After the test we call ``conn.rollback()`` on the outer transaction, which
   rolls back all savepoints and leaves the database completely clean.

This means tests can exercise commit-based service code with full isolation.
"""

import uuid
from collections.abc import AsyncGenerator
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, create_async_engine

from app.core.config import settings
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.base import Base
from app.models.transaction import Transaction, TransactionStatus, TransactionType
from app.models.user import User, UserRole
from app.models.wallet import Wallet

# ── Engine ────────────────────────────────────────────────────────────────────

TEST_DATABASE_URL = settings.TEST_DATABASE_URL or settings.DATABASE_URL

test_engine = create_async_engine(TEST_DATABASE_URL, echo=False)


# ── Schema setup (session-scoped — runs once per test session) ────────────────

@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_database() -> AsyncGenerator[None, None]:
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


# ── Per-test DB session with savepoint isolation ──────────────────────────────

@pytest_asyncio.fixture
async def db_connection() -> AsyncGenerator[AsyncConnection, None]:
    """
    Open a connection and start an outer transaction that is rolled back after
    the test regardless of any commits the SUT makes inside.
    """
    async with test_engine.connect() as conn:
        await conn.begin()
        yield conn
        await conn.rollback()


@pytest_asyncio.fixture
async def db_session(db_connection: AsyncConnection) -> AsyncGenerator[AsyncSession, None]:
    """
    Session bound to the outer-transaction connection.
    ``join_transaction_mode="create_savepoint"`` means every ``session.commit()``
    inside production code translates to a savepoint release — not a real COMMIT.
    The outer ``conn.rollback()`` in db_connection cleans everything up.
    """
    session = AsyncSession(
        bind=db_connection,
        expire_on_commit=False,
        autoflush=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        yield session
    finally:
        await session.close()


# ── App HTTP client ───────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    from app.core.database import get_db

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.clear()


# ── Reusable user fixtures ────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def test_user(db_session: AsyncSession) -> User:
    user = User(
        id=uuid.uuid4(),
        email="testuser@example.com",
        hashed_password=hash_password("Password1!"),
        full_name="Test User",
        role=UserRole.USER,
        kyc_tier=0,
        is_active=True,
        is_email_verified=True,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def test_admin(db_session: AsyncSession) -> User:
    user = User(
        id=uuid.uuid4(),
        email="admin@example.com",
        hashed_password=hash_password("AdminPass1!"),
        full_name="Admin User",
        role=UserRole.ADMIN,
        kyc_tier=2,
        is_active=True,
        is_email_verified=True,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    return user


# ── Wallet fixture ────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def test_wallet(db_session: AsyncSession, test_user: User) -> Wallet:
    """A default NGN wallet pre-linked to test_user with a zero balance."""
    wallet = Wallet(
        id=uuid.uuid4(),
        user_id=test_user.id,
        currency="NGN",
        balance=Decimal("0.00"),
        is_active=True,
    )
    db_session.add(wallet)
    await db_session.flush()
    await db_session.refresh(wallet)
    return wallet


# ── Transaction fixture ───────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def test_transaction(
    db_session: AsyncSession, test_user: User, test_wallet: Wallet
) -> Transaction:
    """
    A COMPLETED funding transaction for test_user's wallet.
    Provides a real transaction row for tests that need existing data.
    """
    txn = Transaction(
        reference=f"txn_{uuid.uuid4()}",
        type=TransactionType.FUNDING,
        status=TransactionStatus.COMPLETED,
        amount=Decimal("1000.00"),
        currency="NGN",
        destination_wallet_id=test_wallet.id,
        initiated_by_user_id=test_user.id,
    )
    db_session.add(txn)
    await db_session.flush()
    await db_session.refresh(txn)
    return txn


# ── Auth helpers ──────────────────────────────────────────────────────────────

def make_auth_headers(user: User) -> dict:
    token = create_access_token(user.id, user.role.value)
    return {"Authorization": f"Bearer {token}"}
