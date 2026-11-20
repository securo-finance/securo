"""Stored primary amounts follow the owner's display currency (#1381).

`amount_primary` is a cache of each row's value in its owner's primary
currency. A display currency change used to leave the cache in the old
currency, so totals summing it kept the old numbers under the new symbol.
"""
import uuid
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, patch

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
from app.cli import restamp_primary_amounts
from tests.conftest import TestSessionLocal
from app.services.primary_restamp_service import (
    fetch_missing_today_rates,
    restamp_user_primary_amounts,
)

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


@pytest.mark.asyncio
async def test_rerun_after_reload_sees_no_change_at_stored_rate_scale(
    session: AsyncSession, test_user: User, brl_rows
):
    # 1 BRL = 1/6 USD repeats forever; the column keeps ten decimals.
    test_user.preferences = {**(test_user.preferences or {}), "currency_display": "USD"}
    await session.commit()
    user_id = test_user.id
    await restamp_user_primary_amounts(session, user_id)
    await session.commit()
    session.expire_all()

    again = await restamp_user_primary_amounts(session, user_id)
    assert (again["transactions"], again["recurring"], again["assets"]) == (0, 0, 0)
    await session.refresh(brl_rows["brl"])
    assert brl_rows["brl"].fx_rate_used == Decimal("0.1666666667")


@pytest.mark.asyncio
async def test_restamp_never_fetches_and_the_fetch_runs_once(
    session: AsyncSession, test_user: User, brl_rows
):
    # None of BRL, USD's peers or GBP has a quote for today.
    with patch(
        "app.services.primary_restamp_service.sync_rates", new=AsyncMock(return_value=0)
    ) as sync:
        await restamp_user_primary_amounts(session, test_user.id)
        assert sync.await_count == 0

        await fetch_missing_today_rates(session, test_user.id, "EUR")
        assert sync.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failing", ["rate lookup", "restamp"])
async def test_failed_currency_switch_commits_nothing_and_a_retry_restamps(
    client: AsyncClient, session: AsyncSession, auth_headers, test_user: User, brl_rows,
    failing,
):
    # The preference and the restamp commit together: whichever step fails,
    # neither is kept, and the same request later does the whole switch.
    target = (
        "app.services.primary_restamp_service.fetch_missing_today_rates"
        if failing == "rate lookup"
        else "app.services.primary_restamp_service.restamp_user_primary_amounts"
    )
    prefs = {**(test_user.preferences or {}), "currency_display": "USD"}
    with patch(target, new=AsyncMock(side_effect=RuntimeError("boom"))), \
            pytest.raises(RuntimeError):
        await client.patch("/api/users/me", headers=auth_headers, json={"preferences": prefs})

    await session.refresh(test_user)
    assert test_user.primary_currency == "BRL"
    await _reload(session, brl_rows)
    assert brl_rows["usd"].amount_primary == Decimal("50.00")

    response = await client.patch("/api/users/me", headers=auth_headers, json={"preferences": prefs})
    assert response.status_code == 200
    assert response.json()["preferences"]["currency_display"] == "USD"
    await _reload(session, brl_rows)
    _assert_in_usd(brl_rows)


@pytest.fixture
def cli_session(session: AsyncSession):
    @asynccontextmanager
    async def maker():
        yield session

    with patch("app.cli.async_session_maker", new=maker):
        yield session


@pytest.mark.asyncio
async def test_cli_empty_email_is_a_lookup_not_every_user(
    cli_session: AsyncSession, test_user: User, brl_rows
):
    with patch("app.services.primary_restamp_service.restamp_user_primary_amounts") as restamp, \
            pytest.raises(SystemExit) as exit_info:
        await restamp_primary_amounts("")
    assert exit_info.value.code == 1
    restamp.assert_not_called()


@pytest.mark.asyncio
async def test_cli_keeps_going_after_one_user_fails(
    cli_session: AsyncSession, test_user: User, brl_rows
):
    other = User(
        id=uuid.uuid4(), email="other@example.com", hashed_password="x",
        is_active=True, preferences={"currency_display": "BRL"},
    )
    cli_session.add(other)
    await cli_session.commit()

    done: list[uuid.UUID] = []

    async def restamp(_session, user_id):
        if not done:
            done.append(user_id)
            raise RuntimeError("boom")
        done.append(user_id)
        return {}

    with patch(
        "app.services.primary_restamp_service.restamp_user_primary_amounts", new=restamp
    ), pytest.raises(SystemExit) as exit_info:
        await restamp_primary_amounts()
    assert exit_info.value.code == 1
    assert set(done) == {test_user.id, other.id}


@pytest.mark.asyncio
async def test_round_trip_keeps_the_charged_amount_in_balance_and_reports(
    client: AsyncClient, session: AsyncSession, auth_headers, test_user: User, rates
):
    """A USD purchase the BRL card billed at R$ 545.10 (ADR 0027): switching
    to USD and back never moves the balance, and the reports read 545.10
    again in BRL instead of a market conversion of the USD amount."""
    async def display(currency: str) -> None:
        prefs = {**(test_user.preferences or {}), "currency_display": currency}
        response = await client.patch("/api/users/me", headers=auth_headers, json={"preferences": prefs})
        assert response.status_code == 200, response.text

    async def balance(account_id: str) -> Decimal:
        response = await client.get(f"/api/accounts/{account_id}", headers=auth_headers)
        assert response.status_code == 200, response.text
        return Decimal(str(response.json()["current_balance"]))

    await display("BRL")
    account = await client.post(
        "/api/accounts", headers=auth_headers,
        json={"name": "Card", "type": "checking", "balance": "0", "currency": "BRL"},
    )
    assert account.status_code == 201, account.text
    account_id = account.json()["id"]
    created = await client.post(
        "/api/transactions", headers=auth_headers,
        json={"account_id": account_id, "description": "Online store", "amount": "100.00",
              "currency": "USD", "date": D1.isoformat(), "type": "debit",
              "amount_primary": "545.10"},
    )
    assert created.status_code == 201, created.text
    tx = await session.get(Transaction, uuid.UUID(created.json()["id"]))
    assert tx is not None
    assert await balance(account_id) == Decimal("-545.10")

    await display("USD")
    await session.refresh(tx)
    assert (tx.amount_primary, tx.fx_rate_used) == (Decimal("100.00"), Decimal("1"))
    assert tx.amount_account == Decimal("545.10")
    assert await balance(account_id) == Decimal("-545.10")

    await display("BRL")
    await session.refresh(tx)
    assert tx.amount_primary == Decimal("545.10")  # the charge, not 100 x 5 = 500
    assert tx.fx_rate_used == Decimal("5.4510000000")
    assert await balance(account_id) == Decimal("-545.10")


@pytest.mark.asyncio
async def test_clearing_preferences_restamps_into_the_default_currency(
    client: AsyncClient, session: AsyncSession, auth_headers, test_user: User, brl_rows
):
    """`{"preferences": null}` falls back to the default currency (USD), which
    is a currency change like any other."""
    response = await client.patch(
        "/api/users/me", headers=auth_headers, json={"preferences": None}
    )
    assert response.status_code == 200, response.text

    await _reload(session, brl_rows)
    _assert_in_usd(brl_rows)


@pytest.mark.asyncio
async def test_a_switch_committed_meanwhile_is_compared_not_the_stale_user(
    session: AsyncSession, test_user: User, brl_rows
):
    """This request loaded the user while the display was BRL; another one has
    since switched to USD and restamped. Asking for BRL now is a change from
    the committed USD, so it restamps back instead of trusting the stale BRL."""
    from sqlalchemy import update as sa_update
    from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase

    from app.core.auth import UserManager
    from app.schemas.user import UserUpdate

    stale_prefs = dict(test_user.preferences or {})
    assert stale_prefs.get("currency_display", "BRL") == "BRL"
    await session.execute(
        sa_update(User)
        .where(User.id == test_user.id)
        .values(preferences={**stale_prefs, "currency_display": "USD"})
        .execution_options(synchronize_session=False)
    )
    await session.commit()
    async with TestSessionLocal() as other:
        await restamp_user_primary_amounts(other, test_user.id)
        await other.commit()

    manager = UserManager(SQLAlchemyUserDatabase(session, User))
    await manager.update(
        UserUpdate(preferences={**stale_prefs, "currency_display": "BRL"}), test_user
    )

    await _reload(session, brl_rows)
    assert brl_rows["usd"].amount_primary == Decimal("50.00")
    assert brl_rows["brl"].amount_primary == Decimal("120.00")


async def _usd_row_typed_in_brl(client: AsyncClient, auth_headers, test_user: User):
    """A USD 300 purchase on a USD account, typed as costing R$ 1,560 while the
    market rate stored for its date is 5 (R$ 1,500)."""
    prefs = {**(test_user.preferences or {}), "currency_display": "BRL"}
    switched = await client.patch("/api/users/me", headers=auth_headers, json={"preferences": prefs})
    assert switched.status_code == 200, switched.text
    account = await client.post(
        "/api/accounts", headers=auth_headers,
        json={"name": "Wise USD", "type": "checking", "balance": "0", "currency": "USD"},
    )
    assert account.status_code == 201, account.text
    created = await client.post(
        "/api/transactions", headers=auth_headers,
        json={"account_id": account.json()["id"], "description": "Hotel Lisboa",
              "amount": "300.00", "currency": "USD", "date": D1.isoformat(),
              "type": "debit", "amount_primary": "1560.00"},
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


async def _display(client: AsyncClient, auth_headers, test_user: User, currency: str) -> None:
    prefs = {**(test_user.preferences or {}), "currency_display": currency}
    response = await client.patch("/api/users/me", headers=auth_headers, json={"preferences": prefs})
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_round_trip_brings_back_a_typed_amount_on_a_same_currency_account(
    client: AsyncClient, session: AsyncSession, auth_headers, test_user: User, rates
):
    """#1561 treated `amount_primary` as a cache: on a USD row of a USD account
    the typed R$ 1,560 lived nowhere else, and BRL -> USD -> BRL replaced it
    with 300 x 5 = 1,500 (ADR 0028)."""
    tx_id = await _usd_row_typed_in_brl(client, auth_headers, test_user)
    tx = await session.get(Transaction, uuid.UUID(tx_id))
    assert tx is not None
    assert tx.amount_primary == Decimal("1560.00")

    await _display(client, auth_headers, test_user, "USD")
    await session.refresh(tx)
    assert (tx.amount_primary, tx.fx_rate_used) == (Decimal("300.00"), Decimal("1"))

    await _display(client, auth_headers, test_user, "BRL")
    await session.refresh(tx)
    assert tx.amount_primary == Decimal("1560.00")
    assert tx.fx_rate_used == Decimal("5.2000000000")

    # A rerun at the same currency changes nothing.
    assert (await restamp_user_primary_amounts(session, test_user.id))["transactions"] == 0


@pytest.mark.asyncio
async def test_a_typed_amount_survives_a_resave_and_goes_with_a_new_amount(
    client: AsyncClient, session: AsyncSession, auth_headers, test_user: User, rates
):
    tx_id = await _usd_row_typed_in_brl(client, auth_headers, test_user)

    # The dialog resending the same amount keeps the typed conversion.
    resaved = await client.patch(
        f"/api/transactions/{tx_id}", headers=auth_headers,
        json={"amount": "300.00", "description": "Hotel Lisboa (2 nights)"},
    )
    assert resaved.status_code == 200, resaved.text
    tx = await session.get(Transaction, uuid.UUID(tx_id))
    assert tx is not None
    await session.refresh(tx)
    assert tx.amount_primary == Decimal("1560.00")

    # A new amount without a new conversion is converted at the stored rate,
    # and a later round trip does not resurrect the old typed figure.
    changed = await client.patch(
        f"/api/transactions/{tx_id}", headers=auth_headers, json={"amount": "200.00"},
    )
    assert changed.status_code == 200, changed.text
    await session.refresh(tx)
    assert tx.amount_primary == Decimal("1000.00")
    assert tx.amount_primary_typed is None

    await _display(client, auth_headers, test_user, "USD")
    await _display(client, auth_headers, test_user, "BRL")
    await session.refresh(tx)
    assert tx.amount_primary == Decimal("1000.00")


@pytest.mark.asyncio
async def test_posting_a_pending_row_today_drops_its_typed_amount(
    client: AsyncClient, session: AsyncSession, auth_headers, test_user: User, rates
):
    """"Post today" moves the date, so the conversion typed for the old date
    no longer holds and a round trip must not bring it back."""
    tx_id = await _usd_row_typed_in_brl(client, auth_headers, test_user)
    tx = await session.get(Transaction, uuid.UUID(tx_id))
    assert tx is not None
    tx.status = "pending"
    await session.commit()

    posted = await client.post(f"/api/transactions/{tx_id}/post-today", headers=auth_headers)
    assert posted.status_code == 200, posted.text
    await session.refresh(tx)
    assert (tx.amount_primary_typed, tx.amount_primary_typed_currency) == (None, None)

    await _display(client, auth_headers, test_user, "USD")
    await _display(client, auth_headers, test_user, "BRL")
    await session.refresh(tx)
    assert tx.amount_primary != Decimal("1560.00")
