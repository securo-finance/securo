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
from app.models.fx_rate import FxRate
from app.models.user import User
from app.models.workspace import WorkspaceMember


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
