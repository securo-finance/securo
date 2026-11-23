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
from sqlalchemy import select
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
from app.services import group_service, split_service, workspace_service


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

    ordered = await _list(client, bruno, month, sort_by="amount", sort_dir="asc")
    assert _shown(ordered) == pytest.approx([100.0, 200.0])

    # Ana's own bounds still read her stamps.
    assert _shown(await _list(client, couple["ana"], month, min_amount=500)) == (
        pytest.approx([1000.0])
    )


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
    """Bruno's November in the shared workspace (#269 re-test): 300 € of
    Ana's rows plus his 100 € share of the trip. The card used to add his
    share of Ana's split row again on top of the whole row, and every
    share at today's rate; the report's slice took the trip at today's
    rate (80 €) while its drill-down read 100 €."""
    data = couple_with_groups
    month, bruno = data["month"], data["bruno"]

    footer = (await _list(client, bruno, month))["summary"]["expense"]
    assert footer == pytest.approx(400.0)

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
