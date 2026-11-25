"""A recurring bill's amount follows the transactions' rule (ADR 0008).

Its occurrences become transactions, so an amount a transaction refuses has no
place on the bill: `1e15` overflowed `Numeric(15, 2)` and came back as a 500 on
Postgres, and zero or a negative value was accepted. A conversion the backend
works out itself is bounded the same way.
"""
import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.core.app_clock import app_today
from app.models.fx_rate import FxRate
from app.models.recurring_transaction import RecurringTransaction

MAX = "9999999999999.99"
TOO_BIG = "2000000000000"  # 2e12 USD is 1e13 BRL at 5
LIMIT_MESSAGE = f"amount_primary must be at most {MAX}"
REFUSED = ["1e15", "10000000000000", "0", "-5", "0.001", "NaN", "Infinity"]


@pytest_asyncio.fixture
async def usd_rate(session, test_user):
    assert test_user.primary_currency == "BRL"
    session.add(FxRate(
        base_currency="USD", quote_currency="BRL", date=app_today(),
        rate=Decimal("5"), source="test",
    ))
    await session.commit()


async def _create(client, auth_headers, account, **overrides):
    return await client.post(
        "/api/recurring-transactions",
        headers=auth_headers,
        json={
            "description": f"Bill {uuid.uuid4()}",
            "amount": "100",
            "currency": "BRL",
            "type": "debit",
            "frequency": "monthly",
            "start_date": app_today().isoformat(),
            "account_id": str(account.id),
            **overrides,
        },
    )


async def _patch(client, auth_headers, recurring_id, **changes):
    return await client.patch(
        f"/api/recurring-transactions/{recurring_id}", headers=auth_headers, json=changes
    )


async def _stored(session, recurring_id) -> RecurringTransaction:
    row = await session.scalar(
        select(RecurringTransaction)
        .where(RecurringTransaction.id == uuid.UUID(recurring_id))
        .execution_options(populate_existing=True)
    )
    assert row is not None
    return row


@pytest.mark.asyncio
@pytest.mark.parametrize("amount", REFUSED)
async def test_create_refuses(client, auth_headers, session, test_account, amount):
    resp = await _create(client, auth_headers, test_account, amount=amount)

    assert resp.status_code == 422, resp.text
    assert (await session.scalars(select(RecurringTransaction))).all() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("amount", [*REFUSED, None])
async def test_edit_refuses(client, auth_headers, session, test_account, amount):
    created = (await _create(client, auth_headers, test_account)).json()

    resp = await _patch(client, auth_headers, created["id"], amount=amount)

    assert resp.status_code == 422, resp.text
    assert (await _stored(session, created["id"])).amount == Decimal("100.00")


@pytest.mark.asyncio
@pytest.mark.parametrize("amount", ["0.01", MAX])
async def test_the_limits_themselves_are_accepted(client, auth_headers, test_account, amount):
    created = await _create(client, auth_headers, test_account, amount=amount)
    assert created.status_code == 201, created.text

    edited = await _patch(client, auth_headers, created.json()["id"], amount=amount)
    assert edited.status_code == 200, edited.text
    assert Decimal(str(edited.json()["amount"])) == Decimal(amount)


@pytest.mark.asyncio
async def test_an_edit_without_the_amount_still_works(client, auth_headers, test_account):
    created = (await _create(client, auth_headers, test_account)).json()

    resp = await _patch(client, auth_headers, created["id"], description="Renamed")

    assert resp.status_code == 200, resp.text
    assert Decimal(str(resp.json()["amount"])) == Decimal("100")


@pytest.mark.asyncio
async def test_create_refuses_a_conversion_past_the_column(
    client, auth_headers, session, test_account, usd_rate
):
    resp = await _create(client, auth_headers, test_account, amount=TOO_BIG, currency="USD")

    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"] == LIMIT_MESSAGE
    await session.rollback()
    assert (await session.scalars(select(RecurringTransaction))).all() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("seed, changes", [
    ({"currency": "USD"}, {"amount": TOO_BIG}),
    ({"amount": TOO_BIG}, {"currency": "USD"}),
])
async def test_edit_refuses_a_conversion_past_the_column(
    client, auth_headers, session, test_account, usd_rate, seed, changes
):
    created = (await _create(client, auth_headers, test_account, **seed)).json()

    resp = await _patch(client, auth_headers, created["id"], **changes)

    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"] == LIMIT_MESSAGE
    await session.rollback()
    row = await _stored(session, created["id"])
    assert (row.amount, row.currency) == (
        Decimal(str(created["amount"])), created["currency"]
    )


@pytest.fixture
def rate_fetch_commits(monkeypatch):
    """No stored rate for the day: the on-demand fetch stores one and commits
    the request's session, as the real `sync_rates` does."""
    async def sync(session, target_date=None):
        session.add(FxRate(
            base_currency="USD", quote_currency="BRL", date=target_date,
            rate=Decimal("5"), source="test",
        ))
        await session.commit()
        return 1

    monkeypatch.setattr("app.services.fx_rate_service.sync_rates", sync)


@pytest.mark.asyncio
async def test_a_rate_fetch_does_not_save_a_refused_create(
    client, auth_headers, session, test_account, rate_fetch_commits
):
    resp = await _create(client, auth_headers, test_account, amount=TOO_BIG, currency="USD")

    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"] == LIMIT_MESSAGE
    await session.rollback()
    assert (await session.scalars(select(RecurringTransaction))).all() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"amount": TOO_BIG, "currency": "USD"},
    {"amount": TOO_BIG, "currency": "USD", "description": "Renamed"},
])
async def test_a_rate_fetch_does_not_save_a_refused_edit(
    client, auth_headers, session, test_account, rate_fetch_commits, changes
):
    created = (await _create(client, auth_headers, test_account)).json()

    resp = await _patch(client, auth_headers, created["id"], **changes)

    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"] == LIMIT_MESSAGE
    await session.rollback()
    row = await _stored(session, created["id"])
    assert (row.amount, row.currency, row.amount_primary, row.description) == (
        Decimal("100.00"), "BRL", Decimal("100.00"), created["description"]
    )


@pytest.mark.asyncio
async def test_an_edit_uses_the_rate_it_fetched(
    client, auth_headers, test_account, rate_fetch_commits
):
    created = (await _create(client, auth_headers, test_account)).json()

    resp = await _patch(client, auth_headers, created["id"], amount="200", currency="USD")

    assert resp.status_code == 200, resp.text
    assert resp.json()["amount_primary"] == 1000


@pytest.mark.asyncio
async def test_an_edit_of_the_amount_converts_it_again(
    client, auth_headers, test_account, usd_rate
):
    created = (await _create(client, auth_headers, test_account, currency="USD")).json()
    assert created["amount_primary"] == 500

    resp = await _patch(client, auth_headers, created["id"], amount="200")

    assert resp.status_code == 200, resp.text
    assert resp.json()["amount_primary"] == 1000
