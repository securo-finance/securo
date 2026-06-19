import os
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import AsyncGenerator
from unittest.mock import AsyncMock, patch

# --- Agents test setup (must run BEFORE app.main is imported) ---------------
# Force the optional agents feature on for the test process so the routes
# are mounted and the agent models are reachable. Provide deterministic
# JWT/MCP secrets so test fixtures can mint tokens.
os.environ.setdefault("AGENTS_ENABLED", "true")
os.environ.setdefault("AGENTS_MCP_JWT_SECRET", "test-secret-not-for-production")
os.environ.setdefault("AGENTS_BUILTIN_MCP_URL", "http://test-mcp:8765/mcp")

# pgvector's Vector type only compiles on PostgreSQL. Tests use SQLite, so
# we shim it with JSON before any model module imports it. Production runs
# pgvector unchanged.
import sqlalchemy.types  # noqa: E402
import pgvector.sqlalchemy as _pgv  # noqa: E402


class _VectorJSON(sqlalchemy.types.JSON):
    """JSON-backed stand-in for pgvector's Vector type, for SQLite tests."""

    cache_ok = True

    def __init__(self, dim=None, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def cosine_distance(self, other):  # type: ignore[override]
        # Tests for similarity_search are exercised against the real Postgres;
        # the SQLite path just needs a valid SQL fragment.
        from sqlalchemy import literal
        return literal(0.5)


_pgv.Vector = _VectorJSON  # type: ignore[attr-defined]
# ---------------------------------------------------------------------------

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker  # noqa: E402

from app.core.database import Base, get_async_session  # noqa: E402
from app.main import app  # noqa: E402
from app.models.user import User  # noqa: E402
from app.models.passkey import UserPasskey  # noqa: E402,F401
from app.models.category import Category  # noqa: E402
from app.models.bank_connection import BankConnection  # noqa: E402
from app.models.account import Account  # noqa: E402
from app.models.transaction import Transaction  # noqa: E402
from app.models.rule import Rule  # noqa: E402
from app.models.asset import Asset  # noqa: E402,F401
from app.models.asset_value import AssetValue  # noqa: E402,F401
from app.models.transaction_attachment import TransactionAttachment  # noqa: E402,F401
from app.models.payee import Payee, PayeeMapping  # noqa: E402,F401
from app.models.app_settings import AppSetting  # noqa: E402,F401
from app.models.goal import Goal  # noqa: E402,F401
from app.models.credit_card_bill import CreditCardBill  # noqa: E402,F401
from app.models.group import Group, GroupMember  # noqa: E402,F401
from app.models.transaction_split import TransactionSplit  # noqa: E402,F401
from app.models.group_settlement import GroupSettlement  # noqa: E402,F401
from app.models.workspace import Workspace, WorkspaceMember  # noqa: E402,F401
# Agent models — gated by AGENTS_ENABLED above so tests always cover them.
from app.agents.models import (  # noqa: E402,F401
    Agent,
    AgentTool,
    Conversation,
    Message,
    KnowledgeDoc,
    KnowledgeChunk,
    LlmUsage,
)

# Use SQLite for tests — fast, no external dependency.
# Keep the DB file off the bind-mounted project dir (macOS bind mounts
# have known SQLite locking/journal quirks under aiosqlite) — /tmp is a
# tmpfs inside the container. StaticPool + a single shared connection
# is required so async fixtures and tests running on the shared
# session-scoped event loop see the same in-memory schema state.
from sqlalchemy.pool import StaticPool  # noqa: E402

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

engine = create_async_engine(
    TEST_DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


# SQLite doesn't support PostgreSQL UUID type natively — SQLAlchemy handles the
# mapping automatically when we create tables via Base.metadata (it converts
# PostgreSQL UUID to CHAR(32)). We just need to make sure we use string-based
# UUID comparisons.


@pytest_asyncio.fixture(scope="session", loop_scope="session", autouse=True)
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
        os.remove("/tmp/securo_test.db")
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
    """Create a test user with an auto-created Personal workspace.

    Pre-creates the workspace so the auto-stamp listener (registered on
    the financial models) can resolve `workspace_id` from `user_id` for
    legacy test fixtures that construct rows without setting it.
    """
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
        },
    )
    session.add(user)
    await session.flush()

    workspace = Workspace(
        id=uuid.uuid4(),
        name="Pessoal",
        kind="personal",
        created_by_user_id=user.id,
        default_currency="BRL",
        locale="pt-BR",
    )
    session.add(workspace)
    await session.flush()
    session.add(
        WorkspaceMember(
            id=uuid.uuid4(),
            workspace_id=workspace.id,
            user_id=user.id,
            role="owner",
        )
    )
    await session.commit()
    await session.refresh(user)
    return user


@pytest_asyncio.fixture
async def test_workspace(session: AsyncSession, test_user: User) -> Workspace:
    """Return the test user's default workspace (created by `test_user`)."""
    from sqlalchemy import select as _select
    result = await session.execute(
        _select(Workspace)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == test_user.id)
        .limit(1)
    )
    ws = result.scalar_one()
    return ws


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
async def test_superuser(session: AsyncSession, clean_db) -> User:
    """Create a test superuser (admin)."""
    import bcrypt as _bcrypt

    hashed = _bcrypt.hashpw(b"adminpass123", _bcrypt.gensalt()).decode()
    user = User(
        id=uuid.uuid4(),
        email="admin@example.com",
        hashed_password=hashed,
        is_active=True,
        is_superuser=True,
        is_verified=True,
        preferences={
            "language": "en",
            "date_format": "MM/DD/YYYY",
            "timezone": "UTC",
            "currency_display": "USD",
        },
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


@pytest_asyncio.fixture
async def admin_auth_token(client: AsyncClient, test_superuser: User) -> str:
    """Get an auth token for the superuser."""
    response = await client.post(
        "/api/auth/login",
        data={"username": "admin@example.com", "password": "adminpass123"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 200, f"Admin login failed: {response.text}"
    return response.json()["access_token"]


@pytest_asyncio.fixture
def admin_auth_headers(admin_auth_token: str) -> dict:
    """Auth headers for admin requests."""
    return {"Authorization": f"Bearer {admin_auth_token}"}


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
async def test_account(session: AsyncSession, test_user: User, test_connection: BankConnection) -> Account:
    """Create a test account."""
    account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
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
    session: AsyncSession, test_user: User, test_account: Account, test_categories: list[Category]
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


@pytest_asyncio.fixture
async def test_agent(session: AsyncSession, test_user: User) -> Agent:
    """A baseline agent for tests."""
    agent = Agent(
        id=uuid.uuid4(),
        user_id=test_user.id,
        name="Test Agent",
        description="A test agent",
        system_prompt="You are a helpful assistant.",
        provider="openai",
        model="gpt-4o-mini",
        temperature=0.4,
    )
    session.add(agent)
    await session.commit()
    await session.refresh(agent)
    return agent


@pytest_asyncio.fixture
async def test_conversation(session: AsyncSession, test_user: User, test_agent: Agent) -> Conversation:
    conv = Conversation(
        id=uuid.uuid4(),
        user_id=test_user.id,
        agent_id=test_agent.id,
        channel="web",
    )
    session.add(conv)
    await session.commit()
    await session.refresh(conv)
    return conv


@pytest_asyncio.fixture
async def test_user_with_2fa(session: AsyncSession, clean_db) -> User:
    """Create a test user with 2FA enabled."""
    import bcrypt as _bcrypt
    import pyotp

    hashed = _bcrypt.hashpw(b"testpass123", _bcrypt.gensalt()).decode()
    totp_secret = pyotp.random_base32()
    user = User(
        id=uuid.uuid4(),
        email="2fa@example.com",
        hashed_password=hashed,
        is_active=True,
        is_superuser=False,
        is_verified=True,
        totp_secret=totp_secret,
        is_2fa_enabled=True,
        preferences={
            "language": "en",
            "date_format": "MM/DD/YYYY",
            "timezone": "UTC",
            "currency_display": "USD",
        },
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


@pytest.fixture(autouse=True)
def _mock_redis():
    """Provide a no-op Redis mock so rate limiting never blocks tests."""
    mock = AsyncMock()
    # Pipeline mock that always reports 0 prior requests (never rate-limits)
    pipe_mock = AsyncMock()
    pipe_mock.zremrangebyscore = AsyncMock()
    pipe_mock.zcard = AsyncMock()
    pipe_mock.zadd = AsyncMock()
    pipe_mock.expire = AsyncMock()
    pipe_mock.execute = AsyncMock(return_value=[0, 0, True, True])
    mock.pipeline = lambda: pipe_mock
    # Key-value ops for 2FA temp tokens
    mock.get = AsyncMock(return_value=None)
    mock.set = AsyncMock()
    mock.delete = AsyncMock()

    async def _fake_get_redis():
        return mock

    # Reset the cached singleton so no real Redis connection leaks into tests
    import app.core.redis as redis_mod
    original = redis_mod._redis
    redis_mod._redis = None

    with patch("app.core.redis.get_redis", _fake_get_redis), \
         patch("app.core.rate_limit.get_redis", _fake_get_redis), \
         patch("app.api.custom_auth.get_redis", _fake_get_redis), \
         patch("app.api.two_factor.get_redis", _fake_get_redis):
        yield mock

    redis_mod._redis = original


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
