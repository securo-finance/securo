"""Stored primary amounts follow the owner's display currency (#1381).

`amount_primary` is a cache of each row's value in its owner's primary
currency. A display currency change used to leave the cache in the old
currency, so totals summing it kept the old numbers under the new symbol.
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.asset import Asset
from app.models.fx_rate import FxRate
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.models.user import User
from app.models.workspace import Workspace
from app.services.primary_restamp_service import restamp_user_primary_amounts

D1 = date(2025, 3, 10)
D2 = date(2025, 9, 20)


@pytest_asyncio.fixture
async def rates(session: AsyncSession, clean_db) -> None:
    # USD-based: 1 USD = 5 BRL in March, 6 BRL in September.
    for quote, rate, on in (
        ("BRL", Decimal("5"), D1),
        ("EUR", Decimal("0.9"), D1),
        ("BRL", Decimal("6"), D2),
        ("EUR", Decimal("0.8"), D2),
    ):
        session.add(FxRate(base_currency="USD", quote_currency=quote, date=on, rate=rate, source="test"))
    await session.commit()


def _tx(user: User, account: Account, amount: str, currency: str, on: date,
        primary: str | None, rate: str | None) -> Transaction:
    return Transaction(
        id=uuid.uuid4(),
        user_id=user.id,
        workspace_id=account.workspace_id,
        account_id=account.id,
        description=f"{currency} {amount}",
        amount=Decimal(amount),
        currency=currency,
        date=on,
        type="debit",
        source="manual",
        amount_primary=Decimal(primary) if primary is not None else None,
        fx_rate_used=Decimal(rate) if rate is not None else None,
    )


@pytest_asyncio.fixture
async def brl_rows(session: AsyncSession, test_user: User, test_account: Account, rates):
    """Rows stamped while the user's display currency was BRL."""
    usd = _tx(test_user, test_account, "10.00", "USD", D1, "50.00", "5")
    brl = _tx(test_user, test_account, "120.00", "BRL", D2, "120.00", "1")
    # No GBP rate is stored anywhere: it can only be "not converted".
    gbp = _tx(test_user, test_account, "20.00", "GBP", D1, "99.00", "4.95")
    rec = RecurringTransaction(
        user_id=test_user.id,
        workspace_id=test_account.workspace_id,
        account_id=test_account.id,
        description="Rent",
        amount=Decimal("60.00"),
        currency="BRL",
        type="debit",
        frequency="monthly",
        start_date=D1,
        next_occurrence=D1,
        amount_primary=Decimal("60.00"),
        fx_rate_used=Decimal("1"),
    )
    asset = Asset(
        user_id=test_user.id,
        workspace_id=test_account.workspace_id,
        name="Car",
        type="vehicle",
        currency="BRL",
        purchase_date=D2,
        purchase_price=Decimal("600.00"),
        purchase_price_primary=Decimal("600.00"),
    )
    session.add_all([usd, brl, gbp, rec, asset])
    await session.commit()
    return {"usd": usd, "brl": brl, "gbp": gbp, "rec": rec, "asset": asset}


async def _reload(session: AsyncSession, rows: dict) -> None:
    for row in rows.values():
        await session.refresh(row)


def _assert_in_usd(rows: dict) -> None:
    # A row already in the new currency is its own amount at rate 1.
    assert rows["usd"].amount_primary == Decimal("10.00")
    assert rows["usd"].fx_rate_used == Decimal("1")
    # A foreign row is converted at its own date's rate (6 BRL/USD in Sept).
    assert rows["brl"].amount_primary == Decimal("20.00")
    assert rows["rec"].amount_primary == Decimal("12.00")  # March, 5 BRL/USD
    assert rows["asset"].purchase_price_primary == Decimal("100.00")
    # No rate: honest NULL, not a number left in the old currency.
    assert rows["gbp"].amount_primary is None
    assert rows["gbp"].fx_rate_used is None


@pytest.mark.asyncio
async def test_display_currency_change_restamps_primary_amounts(
    client: AsyncClient, session: AsyncSession, auth_headers, test_user: User, brl_rows
):
    prefs = {**(test_user.preferences or {}), "currency_display": "USD"}
    response = await client.patch("/api/users/me", headers=auth_headers, json={"preferences": prefs})
    assert response.status_code == 200

    await _reload(session, brl_rows)
    _assert_in_usd(brl_rows)


@pytest.mark.asyncio
async def test_workspace_currency_change_restamps_owner_primary_amounts(
    client: AsyncClient, session: AsyncSession, auth_headers,
    test_workspace: Workspace, brl_rows,
):
    response = await client.patch(
        f"/api/workspaces/{test_workspace.id}", headers=auth_headers,
        json={"default_currency": "USD"},
    )
    assert response.status_code == 200

    await _reload(session, brl_rows)
    _assert_in_usd(brl_rows)


@pytest.mark.asyncio
async def test_other_preference_change_leaves_primary_amounts_alone(
    client: AsyncClient, session: AsyncSession, auth_headers, test_user: User, brl_rows
):
    prefs = {**(test_user.preferences or {}), "language": "en"}
    response = await client.patch("/api/users/me", headers=auth_headers, json={"preferences": prefs})
    assert response.status_code == 200

    await _reload(session, brl_rows)
    assert brl_rows["gbp"].amount_primary == Decimal("99.00")


@pytest.mark.asyncio
async def test_restamp_is_idempotent_and_skips_other_users(
    session: AsyncSession, test_user: User, test_account: Account, brl_rows
):
    other = User(
        id=uuid.uuid4(), email="other@example.com", hashed_password="x",
        is_active=True, preferences={"currency_display": "BRL"},
    )
    session.add(other)
    await session.flush()
    theirs = _tx(other, test_account, "10.00", "USD", D1, "50.00", "5")
    session.add(theirs)
    test_user.preferences = {**(test_user.preferences or {}), "currency_display": "USD"}
    await session.commit()

    first = await restamp_user_primary_amounts(session, test_user.id)
    await session.commit()
    assert first["transactions"] == 3
    assert first["recurring"] == 1
    assert first["assets"] == 1
    assert first["unconverted"] == 1

    second = await restamp_user_primary_amounts(session, test_user.id)
    await session.commit()
    assert (second["transactions"], second["recurring"], second["assets"]) == (0, 0, 0)

    await _reload(session, brl_rows)
    _assert_in_usd(brl_rows)
    await session.refresh(theirs)
    assert theirs.amount_primary == Decimal("50.00")
