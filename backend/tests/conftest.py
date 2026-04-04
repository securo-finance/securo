import asyncio
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import AsyncGenerator
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.core.database import Base, get_async_session
from app.main import app
from app.models.user import User
from app.models.category import Category
from app.models.bank_connection import BankConnection
from app.models.account import Account
from app.models.monthly_period import MonthlyPeriod
from app.models.monthly_snapshot import MonthlySnapshot  # noqa: F401
from app.models.transaction import Transaction
from app.models.rule import Rule
from app.models.asset import Asset  # noqa: F401
from app.models.asset_value import AssetValue  # noqa: F401
from app.models.transaction_attachment import TransactionAttachment  # noqa: F401
from app.models.payee import Payee, PayeeMapping  # noqa: F401

# Use SQLite for tests — fast, no external dependency
TEST_DATABASE_URL = "sqlite+aiosqlite:///./test.db"

engine = create_async_engine(TEST_DATABASE_URL, echo=False)
TestSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


# SQLite doesn't support PostgreSQL UUID type natively — SQLAlchemy handles the
# mapping automatically when we create tables via Base.metadata (it converts
# PostgreSQL UUID to CHAR(32)). We just need to make sure we use string-based
# UUID comparisons.


@pytest_asyncio.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_database():
    """Create all tables once for the test session."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    # Clean up test db file
    import os
    try:
        os.remove("./test.db")
    except FileNotFoundError:
        pass


@pytest_asyncio.fixture
async def session() -> AsyncGenerator[AsyncSession, None]:
    """Provide a transactional test session that rolls back after each test."""
    async with TestSessionLocal() as session:
        yield session
        # Roll back any uncommitted changes
        await session.rollback()


@pytest_asyncio.fixture
async def clean_db(session: AsyncSession):
    """Clean all data between tests."""
    for table in reversed(Base.metadata.sorted_tables):
        await session.execute(table.delete())
    await session.commit()


async def override_get_async_session() -> AsyncGenerator[AsyncSession, None]:
    async with TestSessionLocal() as session:
        yield session


# Override the dependency
app.dependency_overrides[get_async_session] = override_get_async_session


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """Provide an async test client."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def test_user(session: AsyncSession, clean_db) -> User:
    """Create a test user."""
    import bcrypt as _bcrypt

    hashed = _bcrypt.hashpw(b"testpass123", _bcrypt.gensalt()).decode()
    user = User(
        id=uuid.uuid4(),
        email="test@example.com",
        hashed_password=hashed,
        is_active=True,
        is_superuser=False,
        is_verified=True,
        preferences={
            "language": "pt-BR",
            "date_format": "DD/MM/YYYY",
            "timezone": "America/Sao_Paulo",
            "currency_display": "BRL",
            "current_month_period": date.today().replace(day=1).isoformat()[:7],
        },
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


@pytest_asyncio.fixture
async def auth_token(client: AsyncClient, test_user: User) -> str:
    """Get an auth token for the test user."""
    response = await client.post(
        "/api/auth/login",
        data={"username": "test@example.com", "password": "testpass123"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 200, f"Login failed: {response.text}"
    return response.json()["access_token"]


@pytest_asyncio.fixture
def auth_headers(auth_token: str) -> dict:
    """Auth headers for authenticated requests."""
    return {"Authorization": f"Bearer {auth_token}"}


@pytest_asyncio.fixture
async def test_categories(session: AsyncSession, test_user: User) -> list[Category]:
    """Create test categories."""
    categories = []
    for name, icon, color in [
        ("Alimentação", "🍔", "#F59E0B"),
        ("Transporte", "🚗", "#3B82F6"),
        ("Receita", "💼", "#22C55E"),
    ]:
        cat = Category(
            id=uuid.uuid4(),
            user_id=test_user.id,
            name=name,
            icon=icon,
            color=color,
            is_system=True,
        )
        session.add(cat)
        categories.append(cat)
    await session.commit()
    for cat in categories:
        await session.refresh(cat)
    return categories


@pytest_asyncio.fixture
async def test_connection(session: AsyncSession, test_user: User) -> BankConnection:
    """Create a test bank connection."""
    conn = BankConnection(
        id=uuid.uuid4(),
        user_id=test_user.id,
        provider="test",
        external_id="ext-123",
        institution_name="Banco Teste",
        credentials={"token": "fake"},
        status="active",
        last_sync_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )
    session.add(conn)
    await session.commit()
    await session.refresh(conn)
    return conn


@pytest_asyncio.fixture
async def test_monthly_period(session: AsyncSession, test_user: User) -> MonthlyPeriod:
    period = (test_user.preferences or {}).get("current_month_period")
    monthly_period = MonthlyPeriod(
        id=uuid.uuid4(),
        user_id=test_user.id,
        period=period,
    )
    session.add(monthly_period)
    await session.commit()
    await session.refresh(monthly_period)
    return monthly_period


@pytest_asyncio.fixture
async def test_account(
    session: AsyncSession,
    test_user: User,
    test_connection: BankConnection,
    test_monthly_period: MonthlyPeriod,
) -> Account:
    """Create a test account."""
    account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        monthly_period_id=test_monthly_period.id,
        connection_id=test_connection.id,
        external_id="acc-ext-123",
        name="Conta Corrente",
        type="checking",
        balance=Decimal("1500.00"),
        currency="BRL",
    )
    session.add(account)
    await session.commit()
    await session.refresh(account)
    return account


@pytest_asyncio.fixture
async def test_transactions(
    session: AsyncSession,
    test_user: User,
    test_account: Account,
    test_categories: list[Category],
    test_monthly_period: MonthlyPeriod,
) -> list[Transaction]:
    """Create test transactions in the current month so tests don't break as time passes."""
    today = date.today()
    transactions = []
    data = [
        ("UBER TRIP", Decimal("25.50"), today.replace(day=min(3, today.day)), "debit", test_categories[1].id),
        ("IFOOD RESTAURANTE", Decimal("45.00"), today.replace(day=min(4, today.day)), "debit", test_categories[0].id),
        ("SALARIO FEV", Decimal("8000.00"), today.replace(day=min(2, today.day)), "credit", test_categories[2].id),
        ("PIX RECEBIDO", Decimal("150.00"), today.replace(day=min(5, today.day)), "credit", None),
        ("NETFLIX", Decimal("39.90"), today.replace(day=1), "debit", None),
    ]
    for desc, amount, dt, typ, cat_id in data:
        txn = Transaction(
            id=uuid.uuid4(),
            user_id=test_user.id,
            account_id=test_account.id,
            monthly_period_id=test_monthly_period.id,
            category_id=cat_id,
            description=desc,
            amount=amount,
            date=dt,
            type=typ,
            source="manual",
            created_at=datetime.now(timezone.utc),
        )
        session.add(txn)
        transactions.append(txn)
    await session.commit()
    for txn in transactions:
        await session.refresh(txn)
    return transactions


@pytest_asyncio.fixture
async def test_rules(
    session: AsyncSession, test_user: User, test_categories: list[Category]
) -> list[Rule]:
    """Create test categorization rules using the new Rule model."""
    rules = []
    rule_data = [
        (
            "UBER rule",
            "or",
            [{"field": "description", "op": "starts_with", "value": "UBER"}],
            [{"op": "set_category", "value": str(test_categories[1].id)}],
            10,
        ),
        (
            "IFOOD rule",
            "or",
            [{"field": "description", "op": "starts_with", "value": "IFOOD"}],
            [{"op": "set_category", "value": str(test_categories[0].id)}],
            10,
        ),
        (
            "SALARIO rule",
            "and",
            [{"field": "description", "op": "contains", "value": "SALARIO"}],
            [{"op": "set_category", "value": str(test_categories[2].id)}],
            10,
        ),
    ]
    for name, conditions_op, conditions, actions, priority in rule_data:
        rule = Rule(
            id=uuid.uuid4(),
            user_id=test_user.id,
            name=name,
            conditions_op=conditions_op,
            conditions=conditions,
            actions=actions,
            priority=priority,
            is_active=True,
        )
        session.add(rule)
        rules.append(rule)
    await session.commit()
    for rule in rules:
        await session.refresh(rule)
    return rules


@pytest.fixture(autouse=True)
def _no_external_fx_sync():
    """Prevent tests from hitting the real OpenExchangeRates API."""
    with patch("app.services.fx_rate_service._provider") as mock_provider:
        mock_provider.name = "test"
        mock_provider.fetch_latest = AsyncMock(return_value={})
        mock_provider.fetch_historical = AsyncMock(return_value={})
        yield


@pytest.fixture(autouse=True)
def _reset_provider_registry():
    """Reset the provider registry so local env config doesn't leak into tests."""
    from app.providers import _PROVIDERS

    original = dict(_PROVIDERS)
    _PROVIDERS.clear()
    yield
    _PROVIDERS.clear()
    _PROVIDERS.update(original)
