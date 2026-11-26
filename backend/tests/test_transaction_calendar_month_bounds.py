"""The calendar month is bounded before any date math or occurrence walk (#598).

The month grid does date arithmetic on its edges and, for a future month,
counts every recurring occurrence since today. An unbounded month reached
`date` overflow (500) at the ends of the supported calendar and kept a worker
busy for minutes far enough ahead.
"""

import uuid
from datetime import date

import pytest
from httpx import AsyncClient

from app.core.app_clock import app_today

# Kept in the test as the contract the API documents: a month more than this
# many years away from today is refused; the edges are accepted.
MAX_OFFSET_YEARS = 50


async def _calendar(client: AsyncClient, headers: dict, month: str):
    return await client.get(
        "/api/transactions/calendar",
        headers=headers,
        params={"month": month},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("month", ["0001-01-01", "9999-12-01"])
async def test_refuses_a_month_at_the_ends_of_the_calendar(
    client: AsyncClient, auth_headers: dict, test_workspace, month: str
):
    response = await _calendar(client, auth_headers, month)

    assert response.status_code == 422, response.text
    assert f"{MAX_OFFSET_YEARS} years" in response.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize("offset", [-MAX_OFFSET_YEARS - 1, MAX_OFFSET_YEARS + 1])
async def test_refuses_a_month_one_year_past_the_supported_range(
    client: AsyncClient, auth_headers: dict, test_workspace, offset: int
):
    today = app_today()
    response = await _calendar(client, auth_headers, f"{today.year + offset}-01-01")

    assert response.status_code == 422, response.text
    assert f"{MAX_OFFSET_YEARS} years" in response.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize("offset", [-MAX_OFFSET_YEARS, MAX_OFFSET_YEARS])
async def test_accepts_the_edges_of_the_supported_range(
    client: AsyncClient, auth_headers: dict, test_workspace, offset: int
):
    today = app_today()
    response = await _calendar(client, auth_headers, f"{today.year + offset}-06-01")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["month"] == f"{today.year + offset}-06"
    in_month = [day for day in body["days"] if day["in_month"]]
    assert len(in_month) == 30
    assert in_month[0]["date"] == f"{today.year + offset}-06-01"


@pytest.mark.asyncio
async def test_a_future_month_inside_the_range_still_carries_occurrences(
    client: AsyncClient, auth_headers: dict, test_user, test_workspace, session
):
    """The accepted future edge stays computable and keeps its projection."""
    from decimal import Decimal

    from app.models.account import Account
    from app.models.recurring_transaction import RecurringTransaction

    currency = test_user.primary_currency
    account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="R598 Checking",
        type="checking",
        balance=Decimal("0"),
        currency=currency,
    )
    session.add(account)
    await session.flush()
    session.add(
        RecurringTransaction(
            id=uuid.uuid4(),
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            account_id=account.id,
            description="R598 weekly debit",
            amount=Decimal("1"),
            currency=currency,
            type="debit",
            frequency="weekly",
            start_date=date(app_today().year - 1, 1, 5),
            next_occurrence=date(app_today().year - 1, 1, 5),
        )
    )
    await session.commit()

    year = app_today().year + MAX_OFFSET_YEARS
    response = await _calendar(client, auth_headers, f"{year}-06-01")

    assert response.status_code == 200, response.text
    days = response.json()["days"]
    in_month = [day for day in days if day["in_month"]]
    assert len(in_month) == 30
    assert any(day["projected_count"] for day in in_month)
