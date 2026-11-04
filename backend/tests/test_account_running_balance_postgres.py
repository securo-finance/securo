"""Running balance (#1202) on real PostgreSQL: the window and the daily series."""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.database import get_async_session
from app.main import app
from app.models.account import Account
from app.models.category import Category
from app.models.transaction import Transaction

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def session(postgres_sessions):
    async with postgres_sessions() as session:
        yield session
        await session.rollback()


@pytest.fixture
def clean_db(postgres_sessions):
    """The reused user/data fixtures start in a fresh per-test PostgreSQL schema."""


@pytest_asyncio.fixture
async def client(postgres_sessions, monkeypatch):
    async def pg_session():
        async with postgres_sessions() as session:
            yield session

    monkeypatch.setitem(app.dependency_overrides, get_async_session, pg_session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def test_running_balance_list_and_series_agree(client, auth_headers, session, test_user):
    account = Account(
        id=uuid.uuid4(), user_id=test_user.id, name="Running PG", type="checking",
        balance=Decimal("0"), currency="BRL",
    )
    ignored = Category(id=uuid.uuid4(), user_id=test_user.id, name="Ignored", is_ignored=True)
    session.add_all([account, ignored])
    await session.commit()
    created = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for offset, (amount, txn_type, day, extra) in enumerate([
        ("1000", "credit", date(2026, 1, 20), {"source": "opening_balance"}),
        ("200", "credit", date(2026, 2, 2), {}),
        ("50", "debit", date(2026, 2, 5), {"status": "pending"}),
        ("30", "debit", date(2026, 2, 5), {"category_id": ignored.id}),
        ("400", "debit", date(2026, 2, 20), {}),
    ]):
        session.add(Transaction(
            id=uuid.uuid4(), user_id=test_user.id, account_id=account.id,
            description=f"{txn_type} {amount}", amount=Decimal(amount), currency="BRL",
            type=txn_type, date=day, source=extra.pop("source", "manual"),
            created_at=created + timedelta(seconds=offset), **extra,
        ))
    await session.commit()
    range_params = {"from": "2026-02-01", "to": "2026-02-28"}

    pages = []
    for page in (1, 2):
        response = await client.get("/api/transactions", params={
            "account_id": str(account.id), "include_running_balance": "true",
            "include_opening_balance": "true", "limit": 2, "page": page, **range_params,
        }, headers=auth_headers)
        assert response.status_code == 200, response.text
        pages += response.json()["items"]
    series = await client.get(
        f"/api/accounts/{account.id}/running-balance", params=range_params, headers=auth_headers,
    )

    assert [tx["running_balance"] for tx in pages] == [750, 1150, 1150, 1200]
    assert series.status_code == 200, series.text
    points = series.json()
    assert len(points) == 28
    assert points[0]["balance"] == 1000
    assert points[4]["balance"] == 1150
    assert points[-1]["balance"] == pages[0]["running_balance"]
