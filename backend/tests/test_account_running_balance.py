"""Server-side running balance for the account page (#1202).

The account page used to walk its balance column, chart and projected balance
from `summary.opening_balance` over the rows it had loaded, capped at 500, so
anything past that cap never moved the balance. These cover the two places the
server now does that walk:

- `running_balance` / `running_balance_primary` on GET /api/transactions
- GET /api/accounts/{id}/running-balance, the daily series behind the chart
"""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.category import Category
from app.models.fx_rate import FxRate
from app.models.transaction import Transaction
from app.models.user import User
from app.services.workspace_service import create_personal_workspace_for_user


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CREATED = datetime(2026, 1, 1, tzinfo=timezone.utc)


async def _make_account(
    session: AsyncSession, user_id: uuid.UUID,
    acc_type: str = "checking", currency: str = "BRL",
) -> Account:
    account = Account(
        id=uuid.uuid4(), user_id=user_id, name=f"Running {acc_type}",
        type=acc_type, balance=Decimal("0"), currency=currency,
    )
    session.add(account)
    await session.commit()
    await session.refresh(account)
    return account


async def _add_txn(
    session: AsyncSession, user_id: uuid.UUID, account: Account,
    amount: str, txn_type: str, txn_date: date,
    created_at: datetime | None = None, **extra,
) -> Transaction:
    txn = Transaction(
        id=uuid.uuid4(), user_id=user_id, account_id=account.id,
        description=f"{txn_type} {amount}", amount=Decimal(amount),
        currency=extra.pop("currency", account.currency), type=txn_type,
        date=txn_date, source=extra.pop("source", "manual"),
        # Distinct by default so rows sharing a date keep a known order.
        created_at=created_at or datetime.now(timezone.utc),
        **extra,
    )
    session.add(txn)
    await session.commit()
    await session.refresh(txn)
    return txn


async def _list(client: AsyncClient, headers: dict, **params) -> list[dict]:
    response = await client.get(
        "/api/transactions",
        params={"include_running_balance": "true", "include_opening_balance": "true", **params},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()["items"]


async def _series(client: AsyncClient, headers: dict, account_id, **params):
    return await client.get(
        f"/api/accounts/{account_id}/running-balance", params=params, headers=headers,
    )


# ---------------------------------------------------------------------------
# GET /api/transactions?include_running_balance=true
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_running_balance_is_continuous_across_pages(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user,
):
    """Each row carries opening + every older row in the range, whichever page
    it is on, and the newest row lands on the series' last point."""
    account = await _make_account(session, test_user.id)
    # Before the range: only reaches the walk through the opening balance.
    await _add_txn(
        session, test_user.id, account, "1000", "credit", date(2026, 1, 15),
        source="opening_balance",
    )
    for amount, txn_type, day in [
        ("200", "credit", 2), ("50", "debit", 5), ("30", "debit", 5),
        ("100", "credit", 10), ("400", "debit", 20),
    ]:
        await _add_txn(session, test_user.id, account, amount, txn_type, date(2026, 2, day))

    params = {"account_id": str(account.id), "from": "2026-02-01", "to": "2026-02-28", "limit": 3}
    items = (
        await _list(client, auth_headers, **params, page=1)
        + await _list(client, auth_headers, **params, page=2)
    )

    assert [tx["running_balance"] for tx in items] == [820, 1220, 1120, 1150, 1200]
    assert all(tx["running_balance_primary"] is None for tx in items)

    summary = await client.get(
        f"/api/accounts/{account.id}/summary",
        params={"from": "2026-02-01", "to": "2026-02-28"}, headers=auth_headers,
    )
    assert summary.json()["opening_balance"] == 1000

    series = await _series(client, auth_headers, account.id, **{"from": "2026-02-01", "to": "2026-02-28"})
    assert series.status_code == 200
    assert series.json()[-1]["balance"] == items[0]["running_balance"]


@pytest.mark.asyncio
async def test_running_balance_breaks_same_date_ties_like_the_list(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user,
):
    """Rows on one date walk in the reverse of the list order (created_at,
    then id), so the balances read top to bottom without jumps."""
    account = await _make_account(session, test_user.id)
    day = date(2026, 3, 3)
    # Three rows share created_at, as a bank sync batch does; powers of two
    # make every ordering produce a different set of balances.
    for amount in ("1", "2", "4"):
        await _add_txn(session, test_user.id, account, amount, "credit", day, created_at=_CREATED)
    await _add_txn(
        session, test_user.id, account, "8", "credit", day,
        created_at=_CREATED + timedelta(seconds=1),
    )

    items = await _list(
        client, auth_headers, account_ids=[str(account.id)], **{"from": "2026-03-01", "to": "2026-03-31"},
    )

    expected, balance = [], Decimal(0)
    for tx in reversed(items):
        balance += Decimal(tx["amount"])
        expected.append(float(balance))
    assert [tx["running_balance"] for tx in reversed(items)] == expected
    assert items[0]["description"] == "credit 8"


@pytest.mark.asyncio
async def test_running_balance_counts_rows_like_the_account_page(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user,
):
    """Ignored rows stay listed but move nothing; pending, future-dated and
    the opening row inside the range all move the balance."""
    account = await _make_account(session, test_user.id)
    ignored_category = Category(
        id=uuid.uuid4(), user_id=test_user.id, name="Ignored", is_ignored=True,
    )
    session.add(ignored_category)
    await session.commit()
    today = date.today()
    await _add_txn(
        session, test_user.id, account, "1000", "credit", today - timedelta(days=5),
        source="opening_balance",
    )
    await _add_txn(
        session, test_user.id, account, "100", "debit", today - timedelta(days=4), is_ignored=True,
    )
    await _add_txn(
        session, test_user.id, account, "200", "debit", today - timedelta(days=3),
        category_id=ignored_category.id,
    )
    await _add_txn(
        session, test_user.id, account, "50", "debit", today - timedelta(days=2), status="pending",
    )
    await _add_txn(session, test_user.id, account, "300", "credit", today + timedelta(days=5))

    range_params = {
        "from": (today - timedelta(days=5)).isoformat(),
        "to": (today + timedelta(days=10)).isoformat(),
    }
    items = await _list(client, auth_headers, account_id=str(account.id), **range_params)

    assert [tx["running_balance"] for tx in items] == [1250, 950, 1000, 1000, 1000]
    series = await _series(client, auth_headers, account.id, **range_params)
    assert series.json()[-1]["balance"] == 1250


@pytest.mark.asyncio
async def test_running_balance_on_connected_account_starts_from_summary_opening(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user, test_account,
):
    """A connected account seeds the walk from the provider snapshot exactly
    as the summary does, so the walk ends on the provider's balance."""
    today = date.today()
    await _add_txn(session, test_user.id, test_account, "500", "credit", today - timedelta(days=30))
    await _add_txn(session, test_user.id, test_account, "300", "credit", today - timedelta(days=10))
    await _add_txn(
        session, test_user.id, test_account, "20", "debit", today - timedelta(days=5), status="pending",
    )
    range_params = {"from": (today - timedelta(days=20)).isoformat(), "to": today.isoformat()}

    summary = (await client.get(
        f"/api/accounts/{test_account.id}/summary", params=range_params, headers=auth_headers,
    )).json()
    items = await _list(client, auth_headers, account_id=str(test_account.id), **range_params)

    # Provider 1500, less the 300 posted and the −20 pending inside the range.
    assert summary["opening_balance"] == 1220
    assert [tx["running_balance"] for tx in items] == [1500, 1520]
    assert items[0]["running_balance"] == summary["current_balance"]
    series = await _series(client, auth_headers, test_account.id, **range_params)
    assert series.json()[0]["balance"] == summary["opening_balance"]
    assert series.json()[-1]["balance"] == 1500


@pytest.mark.asyncio
async def test_running_balance_primary_for_foreign_currency_account(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user,
):
    """A USD account for a BRL user also walks in BRL: the opening converted
    like the summary's, then each row's stamped amount_primary. A row with no
    stamp moves only the native balance."""
    session.add(FxRate(
        base_currency="USD", quote_currency="BRL", date=date.today(),
        rate=Decimal("5"), source="test",
    ))
    await session.commit()
    account = await _make_account(session, test_user.id, currency="USD")
    await _add_txn(
        session, test_user.id, account, "100", "credit", date(2026, 1, 10),
        source="opening_balance", amount_primary=Decimal("480"),
    )
    await _add_txn(
        session, test_user.id, account, "10", "debit", date(2026, 2, 3),
        amount_primary=Decimal("52"),
    )
    await _add_txn(session, test_user.id, account, "5", "debit", date(2026, 2, 4))
    range_params = {"from": "2026-02-01", "to": "2026-02-28"}

    items = await _list(client, auth_headers, account_id=str(account.id), **range_params)
    summary = (await client.get(
        f"/api/accounts/{account.id}/summary", params=range_params, headers=auth_headers,
    )).json()

    assert summary["opening_balance_primary"] == 500
    assert [tx["running_balance"] for tx in items] == [85, 90]
    assert [tx["running_balance_primary"] for tx in items] == [448, 448]
    series = (await _series(client, auth_headers, account.id, **range_params)).json()
    assert series[-1]["balance"] == 85
    assert series[-1]["balance_primary"] == 448


@pytest.mark.parametrize("case", [
    "flag_off", "no_from", "search", "category", "sort_amount", "sort_date_asc",
    "two_accounts", "credit_card", "no_opening_row",
])
@pytest.mark.asyncio
async def test_running_balance_is_null_when_the_list_is_not_one_ledger(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user, case,
):
    """Filtered, reordered or multi-account lists would show balances out of
    sequence, so they get null rather than a wrong number. No error either."""
    account = await _make_account(session, test_user.id)
    other = await _make_account(session, test_user.id)
    card = await _make_account(session, test_user.id, acc_type="credit_card")
    category = Category(id=uuid.uuid4(), user_id=test_user.id, name="Food")
    session.add(category)
    await session.commit()
    for target in (account, other, card):
        await _add_txn(
            session, test_user.id, target, "10", "debit", date(2026, 2, 3), category_id=category.id,
        )

    params = {"account_id": str(account.id), "from": "2026-02-01", "to": "2026-02-28"}
    if case == "flag_off":
        params["include_running_balance"] = "false"
    elif case == "no_from":
        del params["from"]
    elif case == "search":
        params["q"] = "debit"
    elif case == "category":
        params["category_id"] = str(category.id)
    elif case == "sort_amount":
        params["sort_by"] = "amount"
    elif case == "sort_date_asc":
        params.update(sort_by="date", sort_dir="asc")
    elif case == "two_accounts":
        del params["account_id"]
        params["account_ids"] = [str(account.id), str(other.id)]
    elif case == "credit_card":
        params["account_id"] = str(card.id)
    elif case == "no_opening_row":
        params["include_opening_balance"] = "false"

    items = await _list(client, auth_headers, **params)

    assert items
    assert all(tx["running_balance"] is None for tx in items)
    assert all(tx["running_balance_primary"] is None for tx in items)


# ---------------------------------------------------------------------------
# GET /api/accounts/{id}/running-balance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_running_balance_series_has_one_end_of_day_point_per_day(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user,
):
    account = await _make_account(session, test_user.id)
    await _add_txn(session, test_user.id, account, "100", "credit", date(2026, 4, 30))
    await _add_txn(session, test_user.id, account, "40", "debit", date(2026, 5, 2))
    await _add_txn(session, test_user.id, account, "10", "debit", date(2026, 5, 2))
    await _add_txn(session, test_user.id, account, "25", "credit", date(2026, 5, 4))

    response = await _series(client, auth_headers, account.id, **{"from": "2026-05-01", "to": "2026-05-05"})

    assert response.status_code == 200
    assert response.json() == [
        {"date": "2026-05-01", "balance": 100, "balance_primary": None},
        {"date": "2026-05-02", "balance": 50, "balance_primary": None},
        {"date": "2026-05-03", "balance": 50, "balance_primary": None},
        {"date": "2026-05-04", "balance": 75, "balance_primary": None},
        {"date": "2026-05-05", "balance": 75, "balance_primary": None},
    ]


@pytest.mark.asyncio
async def test_running_balance_series_defaults_to_the_current_month(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user,
):
    """Missing bounds default like the summary's: month start to today."""
    account = await _make_account(session, test_user.id)

    response = await _series(client, auth_headers, account.id)

    today = date.today()
    assert response.status_code == 200
    points = response.json()
    assert points[0]["date"] == today.replace(day=1).isoformat()
    assert points[-1]["date"] == today.isoformat()


@pytest.mark.asyncio
async def test_running_balance_series_hides_other_workspaces_accounts(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user,
):
    other_user = User(
        id=uuid.uuid4(), email="running-balance-other@example.test",
        hashed_password="synthetic-unused", is_active=True, is_verified=True,
    )
    session.add(other_user)
    await session.flush()
    workspace = await create_personal_workspace_for_user(session, other_user)
    foreign = Account(
        id=uuid.uuid4(), user_id=other_user.id, workspace_id=workspace.id,
        name="Private", type="checking", balance=Decimal("0"), currency="BRL",
    )
    session.add(foreign)
    await session.commit()

    assert (await _series(client, auth_headers, foreign.id)).status_code == 404
    assert (await _series(client, auth_headers, uuid.uuid4())).status_code == 404


@pytest.mark.asyncio
async def test_running_balance_series_rejects_credit_cards(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user,
):
    card = await _make_account(session, test_user.id, acc_type="credit_card")

    response = await _series(client, auth_headers, card.id)

    assert response.status_code == 400


@pytest.mark.parametrize("date_from, date_to", [
    ("2026-05-02", "2026-05-01"),
    # One day past ten calendar years, the reports' custom-range cap.
    ("2016-01-01", "2026-01-02"),
])
@pytest.mark.asyncio
async def test_running_balance_series_rejects_bad_ranges(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user, date_from, date_to,
):
    account = await _make_account(session, test_user.id)

    response = await _series(client, auth_headers, account.id, **{"from": date_from, "to": date_to})

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_running_balance_series_accepts_the_longest_range(
    client: AsyncClient, auth_headers, session: AsyncSession, test_user,
):
    account = await _make_account(session, test_user.id)
    date_from = date(2016, 1, 1)
    date_to = date(2026, 1, 1)

    response = await _series(
        client, auth_headers, account.id, **{"from": date_from.isoformat(), "to": date_to.isoformat()},
    )

    assert response.status_code == 200
    assert len(response.json()) == (date_to - date_from).days + 1


@pytest.mark.asyncio
async def test_running_balance_series_is_readable_by_viewer(
    client: AsyncClient, viewer_auth_headers, session: AsyncSession, test_user,
):
    account = await _make_account(session, test_user.id)

    response = await _series(client, viewer_auth_headers, account.id)

    assert response.status_code == 200
