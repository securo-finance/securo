"""Members of a shared workspace who display different currencies.

`amount_primary` is stamped in the display currency of the row's owner. Ana
(BRL) owns the workspace and enters the expenses; Bruno (EUR) is a member.
Bruno's totals used to add Ana's BRL stamps as if they were euros, so
R$ 1.000 showed as 1.000 €. Every figure he sees is now in EUR, converted at
each row's date, and the dashboard, the list footer and its rows agree.
Ana's own numbers do not move.
"""

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.models.account import Account
from app.models.category import Category
from app.models.fx_rate import FxRate
from app.models.group import GroupMember
from app.models.transaction import Transaction
from app.models.user import User
from app.models.workspace import WorkspaceMember
from app.schemas.group import GroupCreate, GroupMemberCreate
from app.schemas.transaction_split import TransactionSplitInput, TransactionSplitsInput
from app.plugins.manifest import parse_manifest
from app.services import (
    group_service,
    plugin_report_service,
    split_service,
    workspace_service,
)
from app.services._query_filters import reporting_date_col
from app.services.admin_service import get_credit_card_accounting_mode
from tests.test_report_plugins import _manifest


def _last_month() -> tuple[date, date, date]:
    """The previous month's start and two days in it, all in the past."""
    start = (app_today().replace(day=1) - timedelta(days=1)).replace(day=1)
    return start, start + timedelta(days=4), start + timedelta(days=14)


def _rate(quote: str, rate: str, when: date) -> FxRate:
    return FxRate(
        id=uuid.uuid4(), base_currency="USD", quote_currency=quote,
        rate=Decimal(rate), date=when, source="test",
    )


@pytest.fixture
async def couple(
    client: AsyncClient,
    session: AsyncSession,
    auth_headers: dict,
    test_user: User,
    test_workspace,
    test_categories,
):
    """Ana (the fixture user, BRL) and Bruno (EUR) share Ana's workspace.

    Ana spends R$ 1.000 on day 5 (BRL→EUR 0,20) and R$ 400 on day 15
    (BRL→EUR 0,25): 200 € + 100 € = 300 € for Bruno, R$ 1.400 for Ana.
    """
    month, day5, day15 = _last_month()
    session.add_all([
        _rate("BRL", "5", day5), _rate("EUR", "1", day5),
        _rate("BRL", "4", day15), _rate("EUR", "1", day15),
    ])

    email = f"bruno-{uuid.uuid4().hex[:8]}@example.com"
    resp = await client.post(
        "/api/auth/register", json={"email": email, "password": "brunopassword12"}
    )
    assert resp.status_code in (200, 201), resp.text
    bruno = (await session.execute(select(User).where(User.email == email))).scalar_one()
    bruno.preferences = {**(bruno.preferences or {}), "currency_display": "EUR"}
    session.add(
        WorkspaceMember(
            id=uuid.uuid4(), workspace_id=test_workspace.id, user_id=bruno.id, role="editor"
        )
    )
    await session.commit()
    resp = await client.post(
        "/api/auth/login",
        data={"username": email, "password": "brunopassword12"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200, resp.text
    bruno_headers = {
        "Authorization": f"Bearer {resp.json()['access_token']}",
        "X-Workspace-Id": str(test_workspace.id),
    }
    ana_headers = {**auth_headers, "X-Workspace-Id": str(test_workspace.id)}

    account = (
        await client.post(
            "/api/accounts",
            headers=ana_headers,
            json={"name": "Conta conjunta", "type": "checking", "balance": 0, "currency": "BRL"},
        )
    ).json()
    groceries = test_categories[0]
    for amount, when in (("1000.00", day5), ("400.00", day15)):
        resp = await client.post(
            "/api/transactions",
            headers=ana_headers,
            json={
                "account_id": account["id"],
                "category_id": str(groceries.id),
                "description": "R269 groceries",
                "amount": amount,
                "currency": "BRL",
                "date": when.isoformat(),
                "type": "debit",
            },
        )
        assert resp.status_code == 201, resp.text
    return {
        "month": month,
        "ana": ana_headers,
        "bruno": bruno_headers,
        "category_id": str(groceries.id),
    }


def _month_params(month: date) -> dict:
    end = (month + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    return {"from_date": month.isoformat(), "to_date": end.isoformat()}


async def _dashboard(client: AsyncClient, headers: dict, month: date) -> dict:
    resp = await client.get(
        "/api/dashboard/summary", headers=headers, params={"month": month.isoformat()}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _category_total(client: AsyncClient, headers: dict, month: date, category_id: str):
    resp = await client.get(
        "/api/dashboard/spending-by-category",
        headers=headers,
        params={"month": month.isoformat()},
    )
    assert resp.status_code == 200, resp.text
    return next(row["total"] for row in resp.json() if row["category_id"] == category_id)


async def _list(client: AsyncClient, headers: dict, month: date, **params) -> dict:
    resp = await client.get(
        "/api/transactions",
        headers=headers,
        params={**_month_params(month), "q": "R269", "user_pnl_only": "true", **params},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _report_expenses(client: AsyncClient, headers: dict, month: date) -> float:
    params = _month_params(month)
    resp = await client.get(
        "/api/reports/income-expenses",
        headers=headers,
        params={"start_date": params["from_date"], "end_date": params["to_date"]},
    )
    assert resp.status_code == 200, resp.text
    breakdowns = {b["key"]: b["value"] for b in resp.json()["summary"]["breakdowns"]}
    return breakdowns["expenses"]


async def _budget_actual(client: AsyncClient, headers: dict, month: date, category_id: str):
    resp = await client.get(
        "/api/budgets/comparison", headers=headers, params={"month": month.isoformat()}
    )
    assert resp.status_code == 200, resp.text
    row = next(r for r in resp.json() if r["category_id"] == category_id)
    return float(row["actual_amount"])


@pytest.mark.asyncio
async def test_member_in_another_currency_sees_the_report_and_budget_converted(
    client, couple
):
    month = couple["month"]
    assert await _report_expenses(client, couple["bruno"], month) == pytest.approx(300.0)
    assert await _budget_actual(
        client, couple["bruno"], month, couple["category_id"]
    ) == pytest.approx(300.0)


@pytest.mark.asyncio
async def test_member_in_another_currency_sees_the_dashboard_converted(client, couple):
    summary = await _dashboard(client, couple["bruno"], couple["month"])
    assert summary["monthly_expenses_primary"] == pytest.approx(300.0)
    assert await _category_total(
        client, couple["bruno"], couple["month"], couple["category_id"]
    ) == pytest.approx(300.0)


@pytest.mark.asyncio
async def test_member_in_another_currency_sees_the_list_converted(client, couple):
    body = await _list(client, couple["bruno"], couple["month"])
    assert body["summary"]["currency"] == "EUR"
    assert body["summary"]["expense"] == pytest.approx(300.0)
    # Each row's secondary value is its own conversion, and they add up to
    # the footer: a drill-down lands on the same number as the card.
    primaries = sorted(abs(item["amount_primary"]) for item in body["items"])
    assert primaries == pytest.approx([100.0, 200.0])
    assert all(not item["fx_fallback"] for item in body["items"])

    drill = await _list(
        client, couple["bruno"], couple["month"], category_id=couple["category_id"]
    )
    assert drill["summary"]["expense"] == pytest.approx(300.0)


@pytest.mark.asyncio
async def test_amount_bounds_and_sort_read_the_rows_as_the_member_sees_them(
    client, couple
):
    """Bruno sees 200 € and 100 €; the stamps are Ana's R$ 1.000 and R$ 400.
    The bounds and the amount sort compared his euros with her reais, so
    `max_amount=250` found nothing."""
    month, bruno = couple["month"], couple["bruno"]

    def _shown(body: dict) -> list[float]:
        return [abs(item["amount_primary"]) for item in body["items"]]

    below = await _list(client, bruno, month, max_amount=250)
    assert sorted(_shown(below)) == pytest.approx([100.0, 200.0])
    assert below["total"] == 2

    above = await _list(client, bruno, month, min_amount=150)
    assert _shown(above) == pytest.approx([200.0])

    # Ana's own bounds still read her stamps.
    assert _shown(await _list(client, couple["ana"], month, min_amount=500)) == (
        pytest.approx([1000.0])
    )

    # Bruno's own 150 € sit between Ana's rows for him, but its stamp of 150
    # sorts before her R$ 400 when the stored stamps are compared.
    _, day5, _ = _last_month()
    account = (await client.get("/api/accounts", headers=bruno)).json()
    (joint,) = [a for a in account if a["name"] == "Conta conjunta"]
    resp = await client.post(
        "/api/transactions", headers=bruno,
        json={
            "account_id": joint["id"], "category_id": couple["category_id"],
            "description": "R269 his", "amount": "150.00", "currency": "EUR",
            "date": day5.isoformat(), "type": "debit",
        },
    )
    assert resp.status_code == 201, resp.text

    ordered = await _list(client, bruno, month, sort_by="amount", sort_dir="asc")
    assert _shown(ordered) == pytest.approx([100.0, 150.0, 200.0])


@pytest.mark.asyncio
async def test_a_legacy_one_to_one_stamp_stays_flagged_after_conversion(
    client, session: AsyncSession, couple, test_user: User
):
    """A US$ 50 row Ana stamped R$ 50 at the legacy 1:1 fallback rate. Bruno
    sees it converted to euros, which rewrites its rate, but the stamp is
    still not backed by a real rate and the row keeps its warning."""
    month, day5, _day15 = _last_month()
    account = (
        await session.execute(select(Account).where(Account.name == "Conta conjunta"))
    ).scalar_one()
    session.add(
        Transaction(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=account.workspace_id,
            account_id=account.id, description="R269 legacy", amount=Decimal("50.00"),
            currency="USD", amount_primary=Decimal("50.00"), fx_rate_used=Decimal("1"),
            date=day5, type="debit", source="manual",
        )
    )
    await session.commit()

    body = await _list(client, couple["bruno"], month, q="R269 legacy")
    [row] = body["items"]
    assert row["amount_primary"] == pytest.approx(10.0)
    assert row["fx_fallback"] is True


async def _member(
    session: AsyncSession, group_id: uuid.UUID, workspace_id: uuid.UUID, **fields
) -> GroupMember:
    member = await group_service.create_member(
        session, group_id, workspace_id, GroupMemberCreate(**fields)
    )
    assert member is not None
    return member


@pytest.fixture
async def couple_with_groups(session: AsyncSession, test_user: User, test_workspace, couple):
    """On top of `couple`: Ana splits her R$ 1.000 in the shared workspace
    with Bruno, and pays a US$ 300 trip in her own workspace split in three
    (Ana, Bruno, Carla). Ana stamps the trip R$ 1.500 (USD→BRL 5 on day 5).

    Today's rates differ from day 5's on purpose (USD→BRL 6, USD→EUR 0,80):
    Bruno's US$ 100 share is 100 € at the row's date and 80 € at today's.
    """
    month, day5, _day15 = _last_month()
    today = app_today()
    session.add_all([_rate("BRL", "6", today), _rate("EUR", "0.8", today)])
    bruno = (
        await session.execute(
            select(User).where(User.email.like("bruno-%@example.com"))
        )
    ).scalar_one()

    casal = await group_service.create_group(
        session, test_workspace.id, test_user.id, GroupCreate(name="Casal R269")
    )
    casal_members = [
        await _member(session, casal.id, test_workspace.id, name="Ana", is_self=True),
        await _member(session, casal.id, test_workspace.id, name="Bruno", email=bruno.email),
    ]
    rent = (
        await session.execute(
            select(Transaction).where(
                Transaction.workspace_id == test_workspace.id,
                Transaction.amount == Decimal("1000.00"),
            )
        )
    ).scalar_one()
    await split_service.replace_splits(
        session,
        rent,
        TransactionSplitsInput(
            share_type="equal",
            splits=[TransactionSplitInput(group_member_id=m.id) for m in casal_members],
        ),
        test_user.id,
    )

    personal = await workspace_service.create_workspace(
        session, name="Ana R269", creator=test_user, seed_defaults=False
    )
    travel = Category(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=personal.id,
        name="Viagens R269", icon="plane", color="#0EA5E9",
    )
    account = Account(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=personal.id,
        name="Cartão", type="checking", balance=Decimal("0"), currency="USD",
    )
    session.add_all([travel, account])
    await session.flush()
    trip = Transaction(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=personal.id,
        account_id=account.id, category_id=travel.id, description="R269 trip",
        amount=Decimal("300.00"), currency="USD", amount_primary=Decimal("1500.00"),
        date=day5, type="debit", source="manual",
    )
    session.add(trip)
    await session.flush()
    viagem = await group_service.create_group(
        session, personal.id, test_user.id, GroupCreate(name="Viagem R269")
    )
    trip_members = [
        await _member(session, viagem.id, personal.id, name="Ana", is_self=True),
        await _member(session, viagem.id, personal.id, name="Bruno", email=bruno.email),
        await _member(session, viagem.id, personal.id, name="Carla"),
    ]
    await split_service.replace_splits(
        session,
        trip,
        TransactionSplitsInput(
            share_type="equal",
            splits=[TransactionSplitInput(group_member_id=m.id) for m in trip_members],
        ),
        test_user.id,
    )
    await session.commit()
    return {**couple, "travel_id": str(travel.id)}


async def _report(client: AsyncClient, headers: dict, month: date) -> dict:
    params = _month_params(month)
    resp = await client.get(
        "/api/reports/income-expenses",
        headers=headers,
        params={"start_date": params["from_date"], "end_date": params["to_date"]},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_group_shares_agree_across_card_footer_report_and_drill_down(
    client, couple_with_groups
):
    """Bruno's November in the shared workspace (#269 re-test): his 100 €
    half of Ana's split row, her other 100 € row whole and his 100 € share of
    the trip (the split row used to count whole). The card used to add his
    share of Ana's split row again on top of the whole row, and every
    share at today's rate; the report's slice took the trip at today's
    rate (80 €) while its drill-down read 100 €."""
    data = couple_with_groups
    month, bruno = data["month"], data["bruno"]

    footer = (await _list(client, bruno, month))["summary"]["expense"]
    assert footer == pytest.approx(300.0)

    summary = await _dashboard(client, bruno, month)
    assert summary["monthly_expenses_primary"] == pytest.approx(footer)

    report = await _report(client, bruno, month)
    breakdowns = {b["key"]: b["value"] for b in report["summary"]["breakdowns"]}
    assert breakdowns["expenses"] == pytest.approx(footer)

    slice_value = next(
        item["value"] for item in report["composition"] if item["key"] == data["travel_id"]
    )
    drill = await _list(client, bruno, month, category_id=data["travel_id"])
    assert slice_value == pytest.approx(100.0)
    assert drill["summary"]["expense"] == pytest.approx(slice_value)


@pytest.mark.asyncio
async def test_the_owner_keeps_her_own_numbers(client, couple):
    summary = await _dashboard(client, couple["ana"], couple["month"])
    assert summary["monthly_expenses_primary"] == pytest.approx(1400.0)
    assert await _category_total(
        client, couple["ana"], couple["month"], couple["category_id"]
    ) == pytest.approx(1400.0)
    body = await _list(client, couple["ana"], couple["month"])
    assert body["summary"]["currency"] == "BRL"
    assert body["summary"]["expense"] == pytest.approx(1400.0)
    assert sorted(abs(i["amount_primary"]) for i in body["items"]) == pytest.approx(
        [400.0, 1000.0]
    )
    month = couple["month"]
    assert await _report_expenses(client, couple["ana"], month) == pytest.approx(1400.0)
    assert await _budget_actual(
        client, couple["ana"], month, couple["category_id"]
    ) == pytest.approx(1400.0)


@pytest.mark.asyncio
async def test_bill_legs_a_day_apart_cancel_for_the_member_too(
    client, session, test_user, test_workspace, couple
):
    """An unpaired R$ 100 bill: paid from checking on day 5, credited on the
    card on day 6, with BRL→EUR moving from 0,20 to 0,25 in between. The legs
    pair on the stored amount, so Bruno's Money Map has no 20 € contribution
    that Ana's does not have either."""
    month, ana = couple["month"], couple["ana"]
    day5, day6 = month + timedelta(days=4), month + timedelta(days=5)
    session.add_all([_rate("BRL", "4", day6), _rate("EUR", "1", day6)])
    bill = Category(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        name="R269 bill payment", icon="credit-card", color="#64748B",
        treat_as_transfer=True,
    )
    session.add(bill)
    await session.commit()

    accounts = []
    for name, kind in (("R269 checking", "checking"), ("R269 card", "credit_card")):
        resp = await client.post(
            "/api/accounts", headers=ana,
            json={"name": name, "type": kind, "balance": 0, "currency": "BRL"},
        )
        assert resp.status_code in (200, 201), resp.text
        accounts.append(resp.json()["id"])
    for account_id, kind, when in ((accounts[0], "debit", day5), (accounts[1], "credit", day6)):
        resp = await client.post(
            "/api/transactions", headers=ana,
            json={
                "account_id": account_id, "category_id": str(bill.id),
                "description": "R269 bill", "amount": "100.00", "currency": "BRL",
                "date": when.isoformat(), "type": kind,
            },
        )
        assert resp.status_code == 201, resp.text

    for who in ("ana", "bruno"):
        report = await _report(client, couple[who], month)
        assert not [
            item for item in report.get("composition") or []
            if item.get("group") in ("investments", "withdrawals")
        ], who


async def _usd_row(client: AsyncClient, couple: dict, amount: str, typed: str | None) -> dict:
    """Ana's USD expense on day 5 (USD→BRL 5, USD→EUR 1), listed as Bruno
    sees it: the stamp in euros, the rate against euros."""
    _, day5, _ = _last_month()
    ana = couple["ana"]
    resp = await client.post(
        "/api/accounts", headers=ana,
        json={"name": "R440 Wise USD", "type": "checking", "balance": 0, "currency": "USD"},
    )
    assert resp.status_code in (200, 201), resp.text
    payload = {
        "account_id": resp.json()["id"], "description": "R269 R440 typed",
        "amount": amount, "currency": "USD", "date": day5.isoformat(), "type": "debit",
    }
    if typed is not None:
        payload["amount_primary"] = typed
    resp = await client.post("/api/transactions", headers=ana, json=payload)
    assert resp.status_code == 201, resp.text
    body = await _list(client, couple["bruno"], couple["month"], q="R440")
    (row,) = body["items"]
    return row


async def _brl_account_usd_row(
    client: AsyncClient, couple: dict, amount: str, typed: str
) -> dict:
    """The same USD expense, but held in an account of Ana's own currency, so
    the account leg has a figure of its own: the typed R$ 1.560 rather than a
    conversion of the row's amount."""
    _, day5, _ = _last_month()
    ana = couple["ana"]
    resp = await client.post(
        "/api/accounts", headers=ana,
        json={"name": "R440 Conta BRL", "type": "checking", "balance": 0, "currency": "BRL"},
    )
    assert resp.status_code in (200, 201), resp.text
    resp = await client.post(
        "/api/transactions",
        headers=ana,
        json={
            "account_id": resp.json()["id"], "description": "R269 R440 typed",
            "amount": amount, "currency": "USD", "date": day5.isoformat(),
            "type": "debit", "amount_primary": typed,
        },
    )
    assert resp.status_code == 201, resp.text
    body = await _list(client, couple["bruno"], couple["month"], q="R440")
    (row,) = body["items"]
    return row


def _dialog_save(row: dict, **changes) -> dict:
    """What the transaction dialog sends back: the fields it was shown."""
    return {
        "description": row["description"], "amount": row["amount"],
        "currency": row["currency"], "date": row["date"], "type": row["type"],
        "amount_primary": row["amount_primary"], "fx_rate_used": row["fx_rate_used"],
        **changes,
    }


async def _stored(session: AsyncSession, row_id: str) -> Transaction:
    session.expire_all()
    tx = await session.get(Transaction, uuid.UUID(row_id))
    assert tx is not None
    return tx


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("typed", "stamp", "shown"),
    [("1560.00", "1560.00", 312.0), (None, "1500.00", 300.0)],
)
async def test_member_editing_the_description_keeps_the_owners_stamp(
    client, session, couple, typed, stamp, shown
):
    """#440: Bruno (EUR) renames Ana's row; the dialog sends back the euros
    it showed, which were written over her reais (R$ 1.560 became 312)."""
    row = await _usd_row(client, couple, "300.00", typed)
    assert abs(row["amount_primary"]) == pytest.approx(shown)

    resp = await client.patch(
        f"/api/transactions/{row['id']}", headers=couple["bruno"],
        json=_dialog_save(row, description="R269 R440 renamed"),
    )
    assert resp.status_code == 200, resp.text

    tx = await _stored(session, row["id"])
    assert tx.description == "R269 R440 renamed"
    assert tx.amount_primary == Decimal(stamp)
    assert Decimal(str(tx.fx_rate_used)) == Decimal(stamp) / 300
    if typed is not None:
        assert tx.amount_primary_typed == Decimal(typed)
        assert tx.amount_primary_typed_currency == "BRL"


@pytest.mark.asyncio
async def test_member_typing_a_conversion_saves_it_in_the_owners_currency(
    client, session, couple
):
    """330 € typed by Bruno is R$ 1.650 for Ana at the row's date (0,20)."""
    row = await _usd_row(client, couple, "300.00", "1560.00")
    resp = await client.patch(
        f"/api/transactions/{row['id']}", headers=couple["bruno"],
        json=_dialog_save(row, amount_primary=330.0, fx_rate_used=1.1),
    )
    assert resp.status_code == 200, resp.text

    tx = await _stored(session, row["id"])
    assert tx.amount_primary == Decimal("1650.00")
    assert Decimal(str(tx.fx_rate_used)) == Decimal("5.5")
    assert tx.amount_primary_typed == Decimal("1650.00")
    assert tx.amount_primary_typed_currency == "BRL"

    # Only the rate changed in the dialog.
    resp = await client.patch(
        f"/api/transactions/{row['id']}", headers=couple["bruno"],
        json={"fx_rate_used": 1.2},
    )
    assert resp.status_code == 200, resp.text
    tx = await _stored(session, row["id"])
    assert tx.amount_primary == Decimal("1800.00")


@pytest.mark.asyncio
async def test_member_changing_the_amount_restamps_in_the_owners_currency(
    client, session, couple
):
    """A new amount from Bruno is stamped at Ana's BRL rate, not in euros."""
    row = await _usd_row(client, couple, "100.00", None)
    resp = await client.patch(
        f"/api/transactions/{row['id']}", headers=couple["bruno"],
        json={"amount": "200.00"},
    )
    assert resp.status_code == 200, resp.text
    tx = await _stored(session, row["id"])
    assert tx.amount_primary == Decimal("1000.00")


@pytest.mark.asyncio
async def test_member_conversion_without_a_rate_is_refused(client, session, couple):
    """No EUR/BRL rate at all: Bruno's euros can't become Ana's reais."""
    row = await _usd_row(client, couple, "300.00", "1560.00")
    await session.execute(delete(FxRate))
    await session.commit()

    resp = await client.patch(
        f"/api/transactions/{row['id']}", headers=couple["bruno"],
        json={"amount_primary": 400.0},
    )
    assert resp.status_code == 400, resp.text
    assert "No exchange rate" in resp.json()["detail"]
    tx = await _stored(session, row["id"])
    assert tx.amount_primary == Decimal("1560.00")


@pytest.mark.asyncio
async def test_owner_editing_her_own_row_keeps_what_she_types(client, session, couple):
    row = await _usd_row(client, couple, "300.00", "1560.00")
    resp = await client.patch(
        f"/api/transactions/{row['id']}", headers=couple["ana"],
        json={"description": "R269 R440 hers", "amount_primary": 1600.0},
    )
    assert resp.status_code == 200, resp.text
    tx = await _stored(session, row["id"])
    assert tx.amount_primary == Decimal("1600.00")
    assert tx.amount_primary_typed == Decimal("1600.00")


@pytest.mark.asyncio
async def test_every_view_the_dialog_opens_from_shows_the_members_currency(
    client, couple
):
    """#440: the calendar and the dashboard open the dialog from the detail
    GET, which returned Ana's reais to Bruno; the PATCH then read them as
    euros. Detail, PATCH response and calendar now read like the list."""
    row = await _usd_row(client, couple, "300.00", "1560.00")
    bruno = couple["bruno"]

    resp = await client.get(f"/api/transactions/{row['id']}", headers=bruno)
    assert resp.status_code == 200, resp.text
    detail = resp.json()
    assert detail["amount_primary"] == pytest.approx(row["amount_primary"])
    assert detail["fx_rate_used"] == pytest.approx(row["fx_rate_used"])
    assert abs(detail["amount_primary"]) == pytest.approx(312.0)

    resp = await client.patch(
        f"/api/transactions/{row['id']}", headers=bruno,
        json={"description": "R269 R440 detail"},
    )
    assert resp.status_code == 200, resp.text
    assert abs(resp.json()["amount_primary"]) == pytest.approx(312.0)

    resp = await client.get(
        "/api/transactions/calendar", headers=bruno,
        params={"month": couple["month"].isoformat()},
    )
    assert resp.status_code == 200, resp.text
    (item,) = [
        item for day in resp.json()["days"] for item in day["items"]
        if item.get("id") == row["id"]
    ]
    assert item["amount_primary"] == pytest.approx(312.0)

    # Ana still reads her own reais everywhere.
    resp = await client.get(f"/api/transactions/{row['id']}", headers=couple["ana"])
    assert abs(resp.json()["amount_primary"]) == pytest.approx(1560.0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sent",
    [
        {"amount_primary": 1560.0, "fx_rate_used": 5.2},
        {"amount_primary": 1560.0},
        {"fx_rate_used": 5.2},
    ],
)
async def test_member_sending_back_the_owners_stored_stamp_keeps_it(
    client, session, couple, sent
):
    """#440 guard: a client that read the row unconverted sends Ana's
    R$ 1.560 / 5,2 back as Bruno's; it was saved as 8.666,67."""
    row = await _usd_row(client, couple, "300.00", "1560.00")
    resp = await client.patch(
        f"/api/transactions/{row['id']}", headers=couple["bruno"],
        json={"description": "R269 R440 calendar", "amount": "300.00", **sent},
    )
    assert resp.status_code == 200, resp.text
    tx = await _stored(session, row["id"])
    assert tx.description == "R269 R440 calendar"
    assert tx.amount_primary == Decimal("1560.00")
    assert tx.amount_primary_typed == Decimal("1560.00")


@pytest.mark.asyncio
async def test_member_moving_the_row_converts_his_figure_at_the_new_date(
    client, session, couple
):
    """Bruno moves the row from day 5 (BRL→EUR 0,20) to day 15 (0,25) and
    types 330 €: R$ 1.320 for Ana, not R$ 1.650 at the old date's rate."""
    _, _, day15 = _last_month()
    row = await _usd_row(client, couple, "300.00", "1560.00")
    resp = await client.patch(
        f"/api/transactions/{row['id']}", headers=couple["bruno"],
        json=_dialog_save(row, date=day15.isoformat(), amount_primary=330.0, fx_rate_used=1.1),
    )
    assert resp.status_code == 200, resp.text
    tx = await _stored(session, row["id"])
    assert tx.date == day15
    assert tx.amount_primary == Decimal("1320.00")
    assert abs(resp.json()["amount_primary"]) == pytest.approx(330.0)
    # The retyped figure is the one kept: Ana's old R$ 1.560 does not come
    # back through the typed copy (#444).
    assert (tx.amount_primary_typed, tx.amount_primary_typed_currency) == (
        Decimal("1320.00"), "BRL",
    )


@pytest.mark.asyncio
async def test_member_moving_only_the_date_keeps_the_owners_typed_stamp(
    client, session, couple
):
    """#444: Bruno moves Ana's 300 USD from day 5 to day 15 and touches
    nothing else, so the dialog sends back the 312 € it was shown; the server
    drops that echo. Ana's typed R$ 1.560 at 5,2 must survive the move
    instead of taking the day-15 market conversion (R$ 1.200)."""
    _, _, day15 = _last_month()
    row = await _usd_row(client, couple, "300.00", "1560.00")
    resp = await client.patch(
        f"/api/transactions/{row['id']}", headers=couple["bruno"],
        json=_dialog_save(row, date=day15.isoformat()),
    )
    assert resp.status_code == 200, resp.text
    tx = await _stored(session, row["id"])
    assert tx.date == day15
    assert tx.amount_primary == Decimal("1560.00")
    assert (tx.amount_primary_typed, tx.amount_primary_typed_currency) == (
        Decimal("1560.00"), "BRL",
    )
    assert Decimal(str(tx.fx_rate_used)) == Decimal("5.2")


@pytest.mark.asyncio
async def test_member_moving_only_the_date_keeps_the_typed_account_leg(
    client, session, couple
):
    """#444, account leg: the account is held in BRL, so Ana's typed R$ 1.560
    is what `amount_account` holds. Bruno moving only the date must keep it
    there instead of leaving the day-15 conversion (R$ 1.200) written by the
    restamp."""
    _, _, day15 = _last_month()
    row = await _brl_account_usd_row(client, couple, "300.00", "1560.00")
    tx = await _stored(session, row["id"])
    assert tx.amount_account == Decimal("1560.00")
    assert tx.amount_account_source == "user"

    resp = await client.patch(
        f"/api/transactions/{row['id']}", headers=couple["bruno"],
        json=_dialog_save(row, date=day15.isoformat()),
    )
    assert resp.status_code == 200, resp.text
    tx = await _stored(session, row["id"])
    assert tx.date == day15
    assert tx.amount_primary == Decimal("1560.00")
    assert tx.amount_account == Decimal("1560.00")
    assert tx.amount_account_source == "user"
    assert Decimal(str(tx.fx_rate_used)) == Decimal("5.2")


@pytest.mark.asyncio
async def test_member_moving_only_the_date_restamps_a_derived_stamp(
    client, session, couple
):
    """The guard on the fix above: with nothing typed, a date move still
    follows the date, R$ 1.500 at day 5 (5,00) becoming R$ 1.200 at day 15
    (4,00)."""
    _, _, day15 = _last_month()
    row = await _usd_row(client, couple, "300.00", None)
    resp = await client.patch(
        f"/api/transactions/{row['id']}", headers=couple["bruno"],
        json=_dialog_save(row, date=day15.isoformat()),
    )
    assert resp.status_code == 200, resp.text
    tx = await _stored(session, row["id"])
    assert tx.date == day15
    assert tx.amount_primary == Decimal("1200.00")
    assert tx.amount_primary_typed is None
    assert Decimal(str(tx.fx_rate_used)) == Decimal("4")


@pytest.mark.asyncio
async def test_owner_moving_only_the_date_restamps_a_derived_stamp(
    client, session, couple
):
    """#481: the web dialog of the owner now sends only the date for a
    conversion it did not touch, so Ana's derived stamp must follow the new
    day's rate exactly as it does for Bruno."""
    _, _, day15 = _last_month()
    row = await _usd_row(client, couple, "300.00", None)
    resp = await client.patch(
        f"/api/transactions/{row['id']}", headers=couple["ana"],
        json={"date": day15.isoformat()},
    )
    assert resp.status_code == 200, resp.text
    tx = await _stored(session, row["id"])
    assert tx.date == day15
    assert tx.amount_primary == Decimal("1200.00")
    assert tx.amount_primary_typed is None
    assert Decimal(str(tx.fx_rate_used)) == Decimal("4")


@pytest.mark.asyncio
async def test_owner_moving_date_and_amount_of_a_derived_stamp_follows_the_new_day(
    client, session, couple
):
    """#483: the dialog reads `fx_typed` to know a derived stamp, and then
    sends only the date and the amount; the stamp follows the new day's rate,
    600 USD at day 15 (4,00) being R$ 2.400, and stays derived."""
    _, _, day15 = _last_month()
    row = await _usd_row(client, couple, "300.00", None)
    assert row["fx_typed"] is False
    resp = await client.patch(
        f"/api/transactions/{row['id']}", headers=couple["ana"],
        json={"date": day15.isoformat(), "amount": "600.00"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["fx_typed"] is False
    tx = await _stored(session, row["id"])
    assert tx.amount_primary == Decimal("2400.00")
    assert tx.amount_primary_typed is None


@pytest.mark.asyncio
async def test_a_typed_stamp_reads_as_typed(client, couple):
    """The dialog sends a typed stamp back when the amount changes (#482),
    and only a typed one (#483)."""
    row = await _usd_row(client, couple, "300.00", "1560.00")
    assert row["fx_typed"] is True
    resp = await client.get(f"/api/transactions/{row['id']}", headers=couple["ana"])
    assert resp.json()["fx_typed"] is True


@pytest.mark.asyncio
async def test_owner_moving_only_the_date_keeps_her_typed_stamp(
    client, session, couple
):
    """#481: a figure Ana typed is about the amount, not the date, so moving
    only the date keeps it, the same result Bruno's edit gives."""
    _, _, day15 = _last_month()
    row = await _usd_row(client, couple, "300.00", "1560.00")
    resp = await client.patch(
        f"/api/transactions/{row['id']}", headers=couple["ana"],
        json={"date": day15.isoformat()},
    )
    assert resp.status_code == 200, resp.text
    tx = await _stored(session, row["id"])
    assert tx.date == day15
    assert tx.amount_primary == Decimal("1560.00")
    assert (tx.amount_primary_typed, tx.amount_primary_typed_currency) == (
        Decimal("1560.00"), "BRL",
    )
    assert Decimal(str(tx.fx_rate_used)) == Decimal("5.2")


@pytest.mark.asyncio
async def test_owner_changing_the_amount_keeps_her_typed_conversion(
    client, session, couple
):
    """#482: Ana raises 300 USD to 400 and the dialog shows R$ 2.080 at 5,2.
    The save carries that figure, and it is what gets stored as typed, not a
    conversion derived at the market rate."""
    row = await _usd_row(client, couple, "300.00", "1560.00")
    resp = await client.patch(
        f"/api/transactions/{row['id']}", headers=couple["ana"],
        json=_dialog_save(
            row, amount="400.00", amount_primary=2080.0, fx_rate_used=5.2
        ),
    )
    assert resp.status_code == 200, resp.text
    tx = await _stored(session, row["id"])
    assert tx.amount == Decimal("400.00")
    assert tx.amount_primary == Decimal("2080.00")
    assert (tx.amount_primary_typed, tx.amount_primary_typed_currency) == (
        Decimal("2080.00"), "BRL",
    )
    assert Decimal(str(tx.fx_rate_used)) == Decimal("5.2")


@pytest.mark.asyncio
async def test_member_renaming_after_a_rate_sync_keeps_the_owners_stamp(
    client, session, couple
):
    """The day-5 rate is synced again while Bruno's dialog is open. The
    dialog leaves the conversion it did not touch out of the save, so Ana's
    R$ 1.560 does not become 312 € read at the new rate."""
    _, day5, _ = _last_month()
    row = await _usd_row(client, couple, "300.00", "1560.00")
    rate = (
        await session.execute(
            select(FxRate).where(FxRate.quote_currency == "BRL", FxRate.date == day5)
        )
    ).scalar_one()
    rate.rate = Decimal("4")
    await session.commit()

    save = _dialog_save(row, description="R269 R440 after sync")
    save.pop("amount_primary")
    save.pop("fx_rate_used")
    resp = await client.patch(
        f"/api/transactions/{row['id']}", headers=couple["bruno"], json=save
    )
    assert resp.status_code == 200, resp.text
    tx = await _stored(session, row["id"])
    assert tx.amount_primary == Decimal("1560.00")
    assert tx.amount_primary_typed == Decimal("1560.00")


async def _sync_day5_brl_to(session: AsyncSession, value: str) -> None:
    _, day5, _ = _last_month()
    rate = (
        await session.execute(
            select(FxRate).where(FxRate.quote_currency == "BRL", FxRate.date == day5)
        )
    ).scalar_one()
    rate.rate = Decimal(value)
    await session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("editor", ["ana", "bruno"])
async def test_renaming_after_a_rate_sync_keeps_a_derived_stamp(
    client, session, couple, editor
):
    """Ana's US$ 300 was stamped R$ 1.500 at USD→BRL 5, with nothing typed.
    The day-5 rate is synced to 4; a rename resends the same amount, currency
    and date, which restamped the row at the new rate (R$ 1.200)."""
    row = await _usd_row(client, couple, "300.00", None)
    await _sync_day5_brl_to(session, "4")

    save = _dialog_save(row, description="R269 R440 derived after sync")
    save.pop("amount_primary")
    save.pop("fx_rate_used")
    resp = await client.patch(
        f"/api/transactions/{row['id']}", headers=couple[editor], json=save
    )
    assert resp.status_code == 200, resp.text
    tx = await _stored(session, row["id"])
    assert tx.description == "R269 R440 derived after sync"
    assert tx.amount_primary == Decimal("1500.00")
    assert Decimal(str(tx.fx_rate_used)) == Decimal("5")


@pytest.mark.asyncio
@pytest.mark.parametrize(("typed", "stamp"), [("1560.00", "1560.00"), (None, "1500.00")])
async def test_a_client_echoing_the_conversion_it_was_shown_survives_a_rate_sync(
    client, session, couple, typed, stamp
):
    """A client that sends back the euros it read, with `amount_primary_shown`
    saying it read them, keeps Ana's stamp after the day-5 rate moved: at
    BRL→EUR 0,25 the same euros no longer match today's conversion, and
    were written over her reais (312 € / 0,25 = R$ 1.248)."""
    row = await _usd_row(client, couple, "300.00", typed)
    await _sync_day5_brl_to(session, "4")

    resp = await client.patch(
        f"/api/transactions/{row['id']}", headers=couple["bruno"],
        json=_dialog_save(
            row, description="R269 R440 echo after sync",
            amount_primary_shown=row["amount_primary"],
        ),
    )
    assert resp.status_code == 200, resp.text
    tx = await _stored(session, row["id"])
    assert tx.description == "R269 R440 echo after sync"
    assert tx.amount_primary == Decimal(stamp)


@pytest.mark.asyncio
async def test_a_changed_conversion_is_still_converted_with_amount_primary_shown(
    client, session, couple
):
    """Saying what was shown does not swallow a real edit: 330 € typed over
    the 312 € shown is stored in Ana's reais at the day's rate."""
    row = await _usd_row(client, couple, "300.00", "1560.00")
    shown = row["amount_primary"]
    typed = -330.0 if shown < 0 else 330.0
    resp = await client.patch(
        f"/api/transactions/{row['id']}", headers=couple["bruno"],
        json=_dialog_save(row, amount_primary=typed, amount_primary_shown=shown),
    )
    assert resp.status_code == 200, resp.text
    tx = await _stored(session, row["id"])
    assert tx.amount_primary is not None
    assert abs(tx.amount_primary) == Decimal("1650.00")


@pytest.mark.asyncio
async def test_a_typed_row_in_the_members_currency_reads_as_its_own_amount(
    client, session, couple
):
    """Ana's 300 € with a typed R$ 1.560: the R$ 1.560 holds in reais only.
    Read back into euros it showed 312 € to Bruno, who sees the 300 € that
    were charged in every view (#463). His rename keeps Ana's typed stamp."""
    _, day5, _ = _last_month()
    accounts = (await client.get("/api/accounts", headers=couple["ana"])).json()
    (joint,) = [a for a in accounts if a["name"] == "Conta conjunta"]
    resp = await client.post(
        "/api/transactions", headers=couple["ana"],
        json={
            "account_id": joint["id"], "description": "R269 R440 euros",
            "amount": "300.00", "currency": "EUR", "amount_primary": "1560.00",
            "date": day5.isoformat(), "type": "debit",
        },
    )
    assert resp.status_code == 201, resp.text
    row_id = resp.json()["id"]

    bruno = couple["bruno"]
    body = await _list(client, bruno, couple["month"], q="R440 euros")
    (listed,) = body["items"]
    assert abs(listed["amount_primary"]) == pytest.approx(300.0)
    assert listed["fx_fallback"] is False
    assert body["summary"]["expense"] == pytest.approx(300.0)

    detail = (await client.get(f"/api/transactions/{row_id}", headers=bruno)).json()
    assert abs(detail["amount_primary"]) == pytest.approx(300.0)

    resp = await client.get(
        "/api/transactions/calendar", headers=bruno,
        params={"month": couple["month"].isoformat()},
    )
    assert resp.status_code == 200, resp.text
    (item,) = [
        item for day in resp.json()["days"] for item in day["items"]
        if item.get("id") == row_id
    ]
    assert item["amount_primary"] == pytest.approx(300.0)

    resp = await client.patch(
        f"/api/transactions/{row_id}", headers=bruno,
        json=_dialog_save(detail, description="R269 R440 euros renamed"),
    )
    assert resp.status_code == 200, resp.text
    tx = await _stored(session, row_id)
    assert tx.description == "R269 R440 euros renamed"
    assert tx.amount_primary == Decimal("1560.00")
    assert tx.amount_primary_typed == Decimal("1560.00")


@pytest.mark.asyncio
async def test_a_converted_row_landing_on_one_to_one_is_not_flagged(client, couple):
    """US$ 100 stamped R$ 500 at a real rate reads 100 € for Bruno (USD→EUR
    1): its rewritten rate is 1.0, which is not the legacy fallback."""
    row = await _usd_row(client, couple, "100.00", None)
    assert abs(row["amount_primary"]) == pytest.approx(100.0)
    assert row["fx_rate_used"] == pytest.approx(1.0)
    assert row["fx_fallback"] is False

    resp = await client.get(f"/api/transactions/{row['id']}", headers=couple["bruno"])
    assert resp.json()["fx_fallback"] is False


def _drill_down_total(items: list[dict], currency: str) -> float:
    """The total `sumDrillDownTotals` shows: a row in the viewer's own
    currency counts its amount, any other row its primary stamp."""
    return sum(
        abs(float(item["amount"])) if item["currency"] == currency
        else abs(float(item["amount_primary"])) if item["amount_primary"] is not None
        else 0.0
        for item in items
    )


@pytest.mark.asyncio
async def test_pending_categorization_amount_is_read_in_the_viewers_currency(
    client: AsyncClient, couple: dict
):
    """#462: the counter added reais, euros and dollars as if they were one
    currency. Ana owns two uncategorized expenses, R$ 1.000 on day 5 and
    US$ 100 on day 15 (USD→BRL 4), so she reads R$ 1.400: the USD row's
    stamp. Bruno reads 300 €: 200 € for the reais and 100 € for the dollars,
    each converted at the row's own date. The list behind the counter adds
    up to the same figure for each of them."""
    _, day5, day15 = _last_month()
    responses = await client.get("/api/accounts", headers=couple["ana"])
    assert responses.status_code == 200, responses.text
    (brl,) = [a for a in responses.json() if a["currency"] == "BRL"]
    resp = await client.post(
        "/api/accounts", headers=couple["ana"],
        json={"name": "R462 Dólares", "type": "checking", "balance": 0, "currency": "USD"},
    )
    assert resp.status_code in (200, 201), resp.text
    usd = resp.json()

    for account, amount, currency, when in (
        (brl, "1000.00", "BRL", day5), (usd, "100.00", "USD", day15),
    ):
        resp = await client.post(
            "/api/transactions", headers=couple["ana"],
            json={
                "account_id": account["id"], "description": "R462 sem categoria",
                "amount": amount, "currency": currency, "date": when.isoformat(),
                "type": "debit",
            },
        )
        assert resp.status_code == 201, resp.text

    ana = await _dashboard(client, couple["ana"], couple["month"])
    bruno = await _dashboard(client, couple["bruno"], couple["month"])
    assert ana["pending_categorization"] == 2
    assert ana["pending_categorization_amount"] == pytest.approx(1400.0)
    assert bruno["pending_categorization_amount"] == pytest.approx(300.0)

    for headers, currency, expected in (
        (couple["ana"], "BRL", 1400.0), (couple["bruno"], "EUR", 300.0),
    ):
        resp = await client.get(
            "/api/transactions", headers=headers,
            params={"uncategorized": "true", "user_pnl_only": "true", "limit": 200},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 2
        assert _drill_down_total(body["items"], currency) == pytest.approx(expected)


@pytest.mark.asyncio
async def test_a_row_in_the_viewers_currency_reads_as_its_own_amount(
    client, session, couple
):
    """Bruno (EUR) enters R$ 10,03 on day 5 (BRL→EUR 0,20): stamped 2,01 €.
    Read back through his stamp it was R$ 10,05 for Ana; it is her currency,
    so every view shows R$ 10,03 and her totals move by exactly that (#463)."""
    month, day5, _ = _last_month()
    accounts = (await client.get("/api/accounts", headers=couple["bruno"])).json()
    (joint,) = [a for a in accounts if a["name"] == "Conta conjunta"]
    resp = await client.post(
        "/api/transactions", headers=couple["bruno"],
        json={
            "account_id": joint["id"], "category_id": couple["category_id"],
            "description": "R269 R463 reais", "amount": "10.03", "currency": "BRL",
            "date": day5.isoformat(), "type": "debit",
        },
    )
    assert resp.status_code == 201, resp.text
    row_id = resp.json()["id"]
    assert (await _stored(session, row_id)).amount_primary == Decimal("2.01")

    ana = couple["ana"]
    body = await _list(client, ana, month, q="R463")
    (listed,) = body["items"]
    assert listed["amount_primary"] == pytest.approx(10.03)
    assert listed["fx_rate_used"] == pytest.approx(1.0)
    assert listed["fx_fallback"] is False
    assert body["summary"]["expense"] == pytest.approx(10.03)

    detail = (await client.get(f"/api/transactions/{row_id}", headers=ana)).json()
    assert detail["amount_primary"] == pytest.approx(10.03)

    total = 1410.03
    assert (await _list(client, ana, month))["summary"]["expense"] == pytest.approx(total)
    assert (await _dashboard(client, ana, month))["monthly_expenses_primary"] == (
        pytest.approx(total)
    )
    assert await _category_total(client, ana, month, couple["category_id"]) == (
        pytest.approx(total)
    )
    assert await _report_expenses(client, ana, month) == pytest.approx(total)
    assert await _budget_actual(client, ana, month, couple["category_id"]) == (
        pytest.approx(total)
    )

    resp = await client.get(
        "/api/transactions/calendar", headers=ana, params={"month": month.isoformat()}
    )
    assert resp.status_code == 200, resp.text
    (item,) = [
        item for day in resp.json()["days"] for item in day["items"]
        if item.get("id") == row_id
    ]
    assert item["amount_primary"] == pytest.approx(10.03)

    # Ana editing only the description sends back the 10,03 she was shown,
    # and Bruno's stamp stays as he had it.
    resp = await client.patch(
        f"/api/transactions/{row_id}", headers=ana,
        json={**_dialog_save(detail), "description": "R269 R463 renamed"},
    )
    assert resp.status_code == 200, resp.text
    assert (await _stored(session, row_id)).amount_primary == Decimal("2.01")


@pytest.mark.asyncio
async def test_a_split_row_in_the_viewers_currency_matches_its_own_part(
    client, couple, test_categories
):
    """Bruno splits R$ 10,03 into R$ 5,01 and R$ 5,02. Ana filtering the
    first category sees R$ 5,01 matched, not the R$ 5,00 his euro stamp
    gave back."""
    month, day5, _ = _last_month()
    accounts = (await client.get("/api/accounts", headers=couple["bruno"])).json()
    (joint,) = [a for a in accounts if a["name"] == "Conta conjunta"]
    first, second = couple["category_id"], str(test_categories[1].id)
    resp = await client.post(
        "/api/transactions", headers=couple["bruno"],
        json={
            "account_id": joint["id"], "description": "R269 R468 split",
            "amount": "10.03", "currency": "BRL", "date": day5.isoformat(),
            "type": "debit",
            "category_lines": {
                "share_type": "exact",
                "lines": [
                    {"category_id": first, "amount": "5.01"},
                    {"category_id": second, "amount": "5.02"},
                ],
            },
        },
    )
    assert resp.status_code == 201, resp.text

    body = await _list(client, couple["ana"], month, q="R468", category_id=first)
    (listed,) = body["items"]
    assert listed["amount_primary"] == pytest.approx(10.03)
    assert float(listed["matched_amount_primary"]) == pytest.approx(5.01)


@pytest.mark.asyncio
async def test_a_lowercase_code_in_the_viewers_currency_reads_as_its_own_amount(
    client, session, couple
):
    """A row stored as "brl" (an import, an old row) by Bruno still reads as
    its own R$ 10,03 for Ana in the SQL totals, like the rows they drill
    into."""
    month, day5, _ = _last_month()
    bruno = (
        await session.execute(select(User).where(User.email.like("bruno-%@example.com")))
    ).scalar_one()
    joint = (
        await session.execute(select(Account).where(Account.name == "Conta conjunta"))
    ).scalar_one()
    session.add(
        Transaction(
            id=uuid.uuid4(), user_id=bruno.id, workspace_id=joint.workspace_id,
            account_id=joint.id, category_id=uuid.UUID(couple["category_id"]),
            description="R269 R468 lowercase", amount=Decimal("10.03"),
            currency="brl", amount_primary=Decimal("2.01"), date=day5,
            type="debit", source="manual",
        )
    )
    await session.commit()

    ana, total = couple["ana"], 1410.03
    assert (await _list(client, ana, month))["summary"]["expense"] == pytest.approx(total)
    assert (await _dashboard(client, ana, month))["monthly_expenses_primary"] == (
        pytest.approx(total)
    )
    assert await _report_expenses(client, ana, month) == pytest.approx(total)


async def _statement(client: AsyncClient, headers: dict, month: date, **params) -> dict:
    resp = await client.get(
        "/api/reports/category-statement", headers=headers,
        params={"month": month.strftime("%Y-%m"), "months": 1, **params},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _cash_flow(client: AsyncClient, headers: dict, **params) -> dict:
    resp = await client.get(
        "/api/reports/cash-flow", headers=headers, params={"months": 3, **params}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _joint_account(client: AsyncClient, couple: dict) -> str:
    accounts = (await client.get("/api/accounts", headers=couple["ana"])).json()
    (joint,) = [a for a in accounts if a["name"] == "Conta conjunta"]
    return joint["id"]


async def _ana_posts(client: AsyncClient, couple: dict, category_id: str, **fields) -> None:
    resp = await client.post(
        "/api/transactions", headers=couple["ana"],
        json={
            "account_id": await _joint_account(client, couple), "category_id": category_id,
            "description": "R302 row", "currency": "BRL", **fields,
        },
    )
    assert resp.status_code == 201, resp.text


@pytest.mark.asyncio
async def test_the_category_statement_is_read_in_the_viewers_currency(
    client, couple, test_categories
):
    """Ana also receives R$ 5.000 on day 5 (1.000 € for Bruno). The statement
    added her reais as euros; it now agrees with Income vs Expenses."""
    month, day5, _ = _last_month()
    await _ana_posts(
        client, couple, str(test_categories[1].id),
        amount="5000.00", date=day5.isoformat(), type="credit",
    )
    joint = await _joint_account(client, couple)

    for who, currency, income, expenses in (
        ("bruno", "EUR", 1000.0, 300.0), ("ana", "BRL", 5000.0, 1400.0),
    ):
        for params in ({}, {"account_ids": joint}):
            body = await _statement(client, couple[who], month, **params)
            assert body["currency"] == currency
            assert body["income"]["totals"] == pytest.approx([income])
            assert body["expenses"]["totals"] == pytest.approx([-expenses])
        report = await _report(client, couple[who], month)
        breakdowns = {b["key"]: b["value"] for b in report["summary"]["breakdowns"]}
        assert (breakdowns["income"], breakdowns["expenses"]) == pytest.approx(
            (income, expenses)
        )


@pytest.mark.asyncio
async def test_the_category_statement_keeps_the_viewers_split_share_converted(
    client, couple_with_groups
):
    """Bruno's half of Ana's R$ 1.000 is 100 €, her R$ 400 are 100 € and his
    share of the trip outside this workspace is 100 €."""
    body = await _statement(client, couple_with_groups["bruno"], couple_with_groups["month"])
    assert body["expenses"]["totals"] == pytest.approx([-300.0])


@pytest.mark.asyncio
async def test_the_category_statement_section_follows_the_viewers_net(
    client, couple, test_categories
):
    """R$ 1.000 in at 0,20 and R$ 900 out at 0,25 is a net income for Ana
    (R$ 100) and a net expense for Bruno (-25 €), so the row sits in the
    section its own numbers point to."""
    month, day5, day15 = _last_month()
    category = str(test_categories[1].id)
    for amount, when, kind in (("1000.00", day5, "credit"), ("900.00", day15, "debit")):
        await _ana_posts(
            client, couple, category, amount=amount, date=when.isoformat(), type=kind
        )

    ana = await _statement(client, couple["ana"], month)
    bruno = await _statement(client, couple["bruno"], month)
    assert ana["income"]["totals"] == pytest.approx([100.0])
    assert bruno["income"]["totals"] == pytest.approx([0.0])
    assert bruno["expenses"]["totals"] == pytest.approx([-325.0])


@pytest.mark.asyncio
async def test_the_cash_flow_posted_rows_are_read_in_the_viewers_currency(client, couple):
    for who, expenses in (("bruno", 300.0), ("ana", 1400.0)):
        body = await _cash_flow(client, couple[who])
        outflow = sum(p["breakdowns"]["outflow"] for p in body["trend"])
        assert outflow == pytest.approx(expenses)


@pytest.mark.asyncio
async def test_the_cash_flow_pending_rows_are_read_in_the_viewers_currency(
    client, session: AsyncSession, couple, test_user: User
):
    """Ana's pending R$ 100 next week is 25 € for Bruno."""
    due = app_today() + timedelta(days=7)
    account = (
        await session.execute(select(Account).where(Account.name == "Conta conjunta"))
    ).scalar_one()
    session.add(
        Transaction(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=account.workspace_id,
            account_id=account.id, description="R302 pending", amount=Decimal("100.00"),
            currency="BRL", amount_primary=Decimal("100.00"), date=due, type="debit",
            source="manual", status="pending", category_id=uuid.UUID(couple["category_id"]),
        )
    )
    await session.commit()
    for who, expected in (("bruno", 25.0), ("ana", 100.0)):
        body = await _cash_flow(client, couple[who])
        (point,) = [p for p in body["trend"] if p["date"] == due.isoformat()]
        assert point["breakdowns"]["outflow"] == pytest.approx(expected)


@pytest.mark.asyncio
async def test_the_cash_flow_baseline_is_read_in_the_viewers_currency(client, couple):
    """The projection averages the past over the same days: R$ 1.400 for
    Ana, 300 € for Bruno."""
    projected = {}
    today = app_today().isoformat()
    for who in ("ana", "bruno"):
        body = await _cash_flow(client, couple[who], baseline="true")
        projected[who] = sum(
            p["breakdowns"]["outflow"] for p in body["trend"] if p["date"] > today
        )
    assert projected["ana"] > 0
    # Each daily point is rounded to the cent.
    assert projected["bruno"] / projected["ana"] == pytest.approx(300 / 1400, rel=0.01)


@pytest.mark.asyncio
async def test_plugin_reports_are_read_in_the_viewers_currency(
    client, session: AsyncSession, couple, test_user: User, test_workspace
):
    """A manifest report and the rows a code report gets: 300 € for Bruno,
    R$ 1.400 for Ana."""
    bruno = (
        await session.execute(select(User).where(User.email.like("bruno-%@example.com")))
    ).scalar_one()
    month, _, day15 = _last_month()
    manifest = parse_manifest(_manifest({
        "key": "by-category", "name": {"en": "By category"},
        "group_by": "category", "chart": "pie",
    }))
    report_date = reporting_date_col(await get_credit_card_accounting_mode(session))
    for user, currency, parts in (
        (bruno, "EUR", ["100.00", "200.00"]), (test_user, "BRL", ["1000.00", "400.00"]),
    ):
        result = await plugin_report_service.run_report(
            session, test_workspace.id, user.id, manifest, manifest.contributes.reports[0],
            month, day15,
        )
        assert result.currency == currency
        assert float(result.total) == pytest.approx(sum(float(p) for p in parts))

        data = await plugin_report_service._transactions_input(
            session, test_workspace.id, user.id, currency, month, day15, None, report_date,
        )
        assert sorted(r["amount_primary"] for r in data["rows"]) == parts
        assert sorted(r["my_share_amount_primary"] for r in data["rows"]) == parts
        assert sorted(r["amount"] for r in data["rows"]) == ["1000.00", "400.00"]
