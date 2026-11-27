"""Disconnecting a bank (#1018) on real PostgreSQL, which enforces the foreign keys."""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.database import get_async_session
from app.main import app
from app.models.account import Account
from app.models.bank_connection import BankConnection
from app.models.invoice import Invoice
from app.models.payee import Payee
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


async def test_disconnect_keeps_a_payee_an_invoice_names(
    client, auth_headers, session, test_user, test_workspace,
):
    """The payee of an invoice is held by a RESTRICT foreign key, so purging
    it with the connection's other orphaned payees failed the disconnect."""
    connection = BankConnection(
        id=uuid.uuid4(), user_id=test_user.id, provider="test", external_id="ext-pg",
        institution_name="PG Bank", credentials={"token": "fake"}, status="active",
    )
    account = Account(
        id=uuid.uuid4(), user_id=test_user.id, connection_id=connection.id,
        name="Connected", type="checking", balance=Decimal("0"), currency="BRL",
    )
    payee = Payee(id=uuid.uuid4(), user_id=test_user.id, name="Client")
    session.add_all([connection, account, payee])
    await session.flush()
    session.add_all([
        Transaction(
            id=uuid.uuid4(), user_id=test_user.id, account_id=account.id,
            description="Payment", amount=Decimal("100"), date=date(2026, 9, 1),
            type="credit", source="sync", payee_id=payee.id,
            created_at=datetime.now(timezone.utc),
        ),
        Invoice(
            workspace_id=test_workspace.id, user_id=test_user.id, payee_id=payee.id,
            issue_date=date(2026, 9, 1), due_date=date(2026, 9, 30), currency="BRL",
        ),
    ])
    await session.commit()

    response = await client.delete(f"/api/connections/{connection.id}", headers=auth_headers)

    assert response.status_code == 204, response.text
    assert (await session.execute(select(Payee.id).where(Payee.id == payee.id))).scalar_one()
