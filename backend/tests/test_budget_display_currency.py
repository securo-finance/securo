"""A budget's limit follows the viewer's display currency, like its spending.

Spending is summed from `amount_primary`, which follows the display currency
since #1381. A budget used to be read as its raw number, so after a switch
from BRL to USD a R$ 1,800 limit read as $1,800 next to spending converted
to dollars. The limit keeps its own currency and is converted on read.
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.category import Category
from app.models.fx_rate import FxRate
from app.models.transaction import Transaction
from app.models.user import User

MONTH = "2025-03-01"
SPENT_ON = date(2025, 3, 10)


@pytest_asyncio.fixture
async def brl_month(
    session: AsyncSession, clean_db, test_user: User, test_account: Account,
    test_categories: list[Category],
) -> Category:
    # 1 USD = 5 BRL all month, so R$ 1,800 is $360 and R$ 900 is $180.
    for on in (date(2025, 3, 1), SPENT_ON):
        session.add(FxRate(base_currency="USD", quote_currency="BRL", date=on, rate=Decimal("5"), source="test"))
    category = test_categories[0]
    session.add(Transaction(
        id=uuid.uuid4(), user_id=test_user.id, account_id=test_account.id,
        category_id=category.id, description="Market", amount=Decimal("900.00"),
        currency="BRL", amount_primary=Decimal("900.00"), fx_rate_used=Decimal("1"),
        date=SPENT_ON, type="debit", source="manual", status="posted",
    ))
    await session.commit()
    return category


async def _set_currency(client: AsyncClient, headers, user: User, currency: str) -> None:
    prefs = {**(user.preferences or {}), "currency_display": currency}
    response = await client.patch("/api/users/me", headers=headers, json={"preferences": prefs})
    assert response.status_code == 200


async def _comparison(client: AsyncClient, headers, category: Category) -> dict:
    response = await client.get("/api/budgets/comparison", params={"month": MONTH}, headers=headers)
    assert response.status_code == 200
    return next(row for row in response.json() if row["category_id"] == str(category.id))


@pytest.mark.asyncio
async def test_limit_is_converted_with_the_spending_and_comes_back_exactly(
    client: AsyncClient, session: AsyncSession, auth_headers, test_user: User, brl_month: Category,
):
    created = await client.post(
        "/api/budgets", headers=auth_headers,
        json={"category_id": str(brl_month.id), "amount": "1800", "month": MONTH, "is_recurring": True},
    )
    assert created.status_code == 201
    assert created.json()["currency"] == "BRL"

    in_brl = await _comparison(client, auth_headers, brl_month)
    assert Decimal(in_brl["budget_amount"]) == Decimal("1800.00")
    assert in_brl["percentage_used"] == 50.0

    await _set_currency(client, auth_headers, test_user, "USD")
    in_usd = await _comparison(client, auth_headers, brl_month)
    assert Decimal(in_usd["actual_amount"]) == Decimal("180.00")
    assert Decimal(in_usd["budget_amount"]) == Decimal("360.00")
    assert in_usd["percentage_used"] == in_brl["percentage_used"]

    listed = await client.get("/api/budgets", params={"month": MONTH}, headers=auth_headers)
    [budget] = listed.json()
    assert (Decimal(budget["amount"]), budget["currency"]) == (Decimal("1800.00"), "BRL")
    assert Decimal(budget["amount_primary"]) == Decimal("360.00")

    summary = await client.get("/api/budgets/rollover-summary", params={"month": MONTH}, headers=auth_headers)
    assert Decimal(summary.json()["total_budgeted"]) == Decimal("360.00")
    forecast = await client.get(
        "/api/budgets/forecast", params={"start_month": MONTH, "months": 1}, headers=auth_headers,
    )
    assert Decimal(forecast.json()["items"][0]["projected_budget"]) == Decimal("360.00")

    await _set_currency(client, auth_headers, test_user, "BRL")
    back = await _comparison(client, auth_headers, brl_month)
    assert Decimal(back["budget_amount"]) == Decimal("1800.00")


@pytest.mark.asyncio
async def test_workspace_currency_switch_converts_the_limit_too(
    client: AsyncClient, auth_headers, test_workspace, brl_month: Category,
):
    await client.post(
        "/api/budgets", headers=auth_headers,
        json={"category_id": str(brl_month.id), "amount": "1800", "month": MONTH},
    )
    response = await client.patch(
        f"/api/workspaces/{test_workspace.id}", headers=auth_headers, json={"default_currency": "USD"},
    )
    assert response.status_code == 200

    row = await _comparison(client, auth_headers, brl_month)
    assert Decimal(row["budget_amount"]) == Decimal("360.00")
    assert row["percentage_used"] == 50.0


@pytest.mark.asyncio
async def test_a_limit_typed_after_the_switch_is_in_the_new_currency(
    client: AsyncClient, session: AsyncSession, auth_headers, test_user: User, brl_month: Category,
):
    await _set_currency(client, auth_headers, test_user, "USD")
    created = await client.post(
        "/api/budgets", headers=auth_headers,
        json={"category_id": str(brl_month.id), "amount": "400", "month": MONTH, "is_recurring": True},
    )
    assert created.json()["currency"] == "USD"

    edited = await client.patch(
        f"/api/budgets/{created.json()['id']}", headers=auth_headers,
        json={"amount": "360", "effective_month": "2025-04-01"},
    )
    assert edited.json()["currency"] == "USD"

    await _set_currency(client, auth_headers, test_user, "BRL")
    row = await _comparison(client, auth_headers, brl_month)
    assert Decimal(row["budget_amount"]) == Decimal("2000.00")


@pytest.mark.asyncio
async def test_editing_a_limit_restates_it_in_the_editor_currency(
    client: AsyncClient, session: AsyncSession, auth_headers, test_user: User, brl_month: Category,
):
    created = await client.post(
        "/api/budgets", headers=auth_headers,
        json={"category_id": str(brl_month.id), "amount": "1800", "month": MONTH},
    )
    await _set_currency(client, auth_headers, test_user, "USD")
    edited = await client.patch(
        f"/api/budgets/{created.json()['id']}", headers=auth_headers, json={"amount": "300"},
    )
    assert (Decimal(edited.json()["amount"]), edited.json()["currency"]) == (Decimal("300.00"), "USD")

    row = await _comparison(client, auth_headers, brl_month)
    assert Decimal(row["budget_amount"]) == Decimal("300.00")
    assert row["percentage_used"] == 60.0


@pytest.mark.asyncio
async def test_copying_a_month_keeps_each_limit_currency(
    client: AsyncClient, session: AsyncSession, auth_headers, test_user: User, brl_month: Category,
):
    await client.post(
        "/api/budgets", headers=auth_headers,
        json={"category_id": str(brl_month.id), "amount": "1800", "month": MONTH},
    )
    await _set_currency(client, auth_headers, test_user, "USD")
    copied = await client.post(
        "/api/budgets/copy-month", headers=auth_headers,
        json={"source_month": MONTH, "target_month": "2025-04-01"},
    )
    assert copied.status_code == 200
    [budget] = copied.json()
    assert (Decimal(budget["amount"]), budget["currency"]) == (Decimal("1800.00"), "BRL")
    assert Decimal(budget["amount_primary"]) == Decimal("360.00")
