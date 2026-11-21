"""The expected-income routes: GET reads, PUT writes under the write gate."""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fx_rate import FxRate
from app.models.workspace import Workspace, WorkspaceMember

pytestmark = pytest.mark.asyncio

_MONTH = "2026-04-28"


async def _other_workspace(session: AsyncSession, test_user) -> Workspace:
    other = Workspace(
        id=uuid.uuid4(), name="Vizinho", kind="personal", default_currency="USD", locale="en"
    )
    session.add(other)
    await session.flush()
    session.add(
        WorkspaceMember(id=uuid.uuid4(), workspace_id=other.id, user_id=test_user.id, role="owner")
    )
    await session.commit()
    return other


@pytest.mark.asyncio
async def test_get_without_a_stored_value_returns_null_body(client, auth_headers):
    resp = await client.get(
        "/api/budgets/expected-income", params={"month": _MONTH}, headers=auth_headers
    )
    assert resp.status_code == 200
    assert resp.json() is None


@pytest.mark.asyncio
async def test_get_without_a_month_reads_the_current_one(client, auth_headers, monkeypatch):
    # Freezing the clock makes a wrong default month fail even with rows present.
    monkeypatch.setattr("app.api.budgets.app_today", lambda: date(2026, 4, 15))
    for month, amount in (("2026-03-10", "100.00"), ("2026-04-10", "200.00")):
        written = await client.put(
            "/api/budgets/expected-income",
            json={"month": month, "amount": amount},
            headers=auth_headers,
        )
        assert written.status_code == 200, written.text

    resp = await client.get("/api/budgets/expected-income", headers=auth_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["month"] == "2026-04-01"
    assert Decimal(str(body["amount"])) == Decimal("200.00")


@pytest.mark.asyncio
async def test_put_then_get_reads_back_the_typed_value(client, auth_headers):
    put = await client.put(
        "/api/budgets/expected-income",
        json={"month": _MONTH, "amount": "3450.00"},
        headers=auth_headers,
    )
    assert put.status_code == 200, put.text
    assert float(put.json()["amount"]) == 3450.00
    assert put.json()["currency"] == "BRL"  # the writer's display currency, not USD

    get = await client.get(
        "/api/budgets/expected-income", params={"month": _MONTH}, headers=auth_headers
    )
    assert get.status_code == 200
    body = get.json()
    assert float(body["amount"]) == 3450.00
    assert body["currency"] == "BRL"
    # Stored as BRL read as BRL: no rate needed, and the primary figure echoes it.
    assert float(body["amount_primary"]) == 3450.00


@pytest.mark.asyncio
async def test_put_with_a_null_amount_clears_the_value(client, auth_headers):
    await client.put(
        "/api/budgets/expected-income",
        json={"month": _MONTH, "amount": "3450.00"},
        headers=auth_headers,
    )
    clear = await client.put(
        "/api/budgets/expected-income",
        json={"month": _MONTH, "amount": None},
        headers=auth_headers,
    )
    assert clear.status_code == 200
    assert clear.json()["amount"] is None

    get = await client.get(
        "/api/budgets/expected-income", params={"month": _MONTH}, headers=auth_headers
    )
    assert get.status_code == 200
    assert get.json() is None


@pytest.mark.asyncio
async def test_a_negative_amount_is_refused(client, auth_headers):
    resp = await client.put(
        "/api/budgets/expected-income",
        json={"month": _MONTH, "amount": "-10.00"},
        headers=auth_headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_a_currency_longer_than_the_code_column_is_refused(client, auth_headers):
    too_long = await client.put(
        "/api/budgets/expected-income",
        json={"month": _MONTH, "amount": "10.00", "currency": "ABCD"},
        headers=auth_headers,
    )
    assert too_long.status_code == 422

    ok = await client.put(
        "/api/budgets/expected-income",
        json={"month": _MONTH, "amount": "10.00", "currency": "USD"},
        headers=auth_headers,
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["currency"] == "USD"


@pytest.mark.asyncio
async def test_an_amount_beyond_the_money_column_is_refused(client, auth_headers):
    too_big = await client.put(
        "/api/budgets/expected-income",
        json={"month": _MONTH, "amount": "10000000000000.00"},
        headers=auth_headers,
    )
    assert too_big.status_code == 422

    boundary = await client.put(
        "/api/budgets/expected-income",
        json={"month": _MONTH, "amount": "9999999999999.99"},
        headers=auth_headers,
    )
    assert boundary.status_code == 200, boundary.text
    assert Decimal(str(boundary.json()["amount"])) == Decimal("9999999999999.99")


@pytest.mark.asyncio
async def test_a_viewer_reads_but_cannot_write(client, auth_headers, viewer_auth_headers):
    await client.put(
        "/api/budgets/expected-income",
        json={"month": _MONTH, "amount": "3450.00"},
        headers=auth_headers,
    )
    read = await client.get(
        "/api/budgets/expected-income", params={"month": _MONTH}, headers=viewer_auth_headers
    )
    assert read.status_code == 200
    assert float(read.json()["amount"]) == 3450.00

    write = await client.put(
        "/api/budgets/expected-income",
        json={"month": _MONTH, "amount": "4000.00"},
        headers=viewer_auth_headers,
    )
    assert write.status_code == 403


@pytest.mark.asyncio
async def test_anonymous_is_unauthorized(client):
    assert (await client.get("/api/budgets/expected-income")).status_code == 401
    assert (
            await client.put("/api/budgets/expected-income", json={"month": _MONTH, "amount": "10"})
        ).status_code == 401


@pytest.mark.asyncio
async def test_a_neighbouring_workspace_neither_sees_nor_touches_the_row(
    client, auth_headers, session, test_user
):
    await client.put(
        "/api/budgets/expected-income",
        json={"month": _MONTH, "amount": "3450.00"},
        headers=auth_headers,
    )
    other = await _other_workspace(session, test_user)

    read = await client.get(
        "/api/budgets/expected-income",
        params={"month": _MONTH},
        headers={**auth_headers, "X-Workspace-Id": str(other.id)},
    )
    assert read.status_code == 200
    assert read.json() is None

    await client.put(
        "/api/budgets/expected-income",
        json={"month": _MONTH, "amount": "9000.00"},
        headers={**auth_headers, "X-Workspace-Id": str(other.id)},
    )

    own = await client.get(
        "/api/budgets/expected-income", params={"month": _MONTH}, headers=auth_headers
    )
    assert float(own.json()["amount"]) == 3450.00


@pytest.mark.asyncio
async def test_the_put_response_uses_the_reader_currency_like_the_get(
    client, auth_headers, session
):
    session.add(
        FxRate(
            base_currency="USD",
            quote_currency="BRL",
            date=date(2026, 4, 1),
            rate=Decimal("5.0"),
            source="test",
        )
    )
    await session.flush()

    put = await client.put(
        "/api/budgets/expected-income",
        json={"month": _MONTH, "amount": "10.00", "currency": "USD"},
        headers=auth_headers,
    )
    assert put.status_code == 200, put.text
    assert Decimal(str(put.json()["amount_primary"])) == Decimal("50.00")

    get = await client.get(
        "/api/budgets/expected-income", params={"month": _MONTH}, headers=auth_headers
    )
    assert get.status_code == 200
    assert Decimal(str(get.json()["amount_primary"])) == Decimal("50.00")
