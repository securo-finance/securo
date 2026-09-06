"""Money promised, in the forecast, without being counted twice.

The whole reason invoices stayed out of the projected balance until now:
the same R$5.000 is routinely an open invoice *and* a pending bank credit,
and nothing could say they were the same money. Payment matching is what
says it, so these tests care less about "does an invoice show up" than
about "does it stop showing up the moment the payment does".
"""
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.payee import Payee
from app.services import invoice_forecast_service as forecast

TODAY = date.today()
SOON = TODAY + timedelta(days=10)
FAR = TODAY + timedelta(days=400)


@pytest_asyncio.fixture
async def business_ws(client: AsyncClient, auth_headers) -> dict:
    resp = await client.post(
        "/api/workspaces",
        headers=auth_headers,
        json={"name": "Previsao", "kind": "business", "self_membership": True},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest_asyncio.fixture
async def biz_headers(auth_headers, business_ws) -> dict:
    return {**auth_headers, "X-Workspace-Id": business_ws["id"]}


@pytest_asyncio.fixture
async def account(session: AsyncSession, business_ws, test_user) -> Account:
    acc = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=uuid.UUID(business_ws["id"]),
        name="Conta PJ",
        type="checking",
        currency="USD",
        balance=Decimal("0"),
    )
    session.add(acc)
    await session.commit()
    return acc


@pytest_asyncio.fixture
async def client_payee(session: AsyncSession, business_ws, test_user) -> Payee:
    payee = Payee(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=uuid.UUID(business_ws["id"]),
        name="Cliente Alpha",
        source="manual",
    )
    session.add(payee)
    await session.commit()
    return payee


async def an_invoice(client, headers, **overrides) -> dict:
    payload = {"total": "5000.00", "due_date": str(SOON)}
    payload.update(overrides)
    resp = await client.post("/api/invoices", headers=headers, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def claims(session, business_ws, *, start=None, end=None):
    return await forecast.claims_in_range(
        session,
        uuid.UUID(business_ws["id"]),
        start or TODAY,
        end or FAR,
    )


# ---------------------------------------------------------------------------
# What is carried
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_an_open_invoice_is_money_the_forecast_can_carry(
    client: AsyncClient, biz_headers, session: AsyncSession, business_ws
):
    await an_invoice(client, biz_headers)
    found = await claims(session, business_ws)

    assert len(found) == 1
    assert found[0].amount == Decimal("5000.00")
    assert found[0].due_date == SOON
    assert found[0].signed == Decimal("5000.00")


@pytest.mark.asyncio
async def test_a_bill_to_pay_points_the_other_way(
    client: AsyncClient, biz_headers, session: AsyncSession, business_ws
):
    """A payable is the same claim with the sign reversed. Filing it as
    income would turn every supplier bill into revenue."""
    await an_invoice(
        client, biz_headers, direction="payable", origin="imported",
        external_source="supplier", external_number="NF-1",
    )
    found = await claims(session, business_ws)

    assert len(found) == 1
    assert found[0].direction == "payable"
    assert found[0].signed == Decimal("-5000.00")


@pytest.mark.asyncio
async def test_a_draft_is_not_owed_by_anybody_yet(
    client: AsyncClient, biz_headers, session: AsyncSession, business_ws
):
    await an_invoice(client, biz_headers, as_draft=True)
    assert await claims(session, business_ws) == []


@pytest.mark.asyncio
async def test_a_cancelled_invoice_is_money_that_is_not_coming(
    client: AsyncClient, biz_headers, session: AsyncSession, business_ws
):
    invoice = await an_invoice(client, biz_headers)
    resp = await client.post(f"/api/invoices/{invoice['id']}/void", headers=biz_headers)
    assert resp.status_code == 200, resp.text

    assert await claims(session, business_ws) == []


@pytest.mark.asyncio
async def test_written_off_money_is_not_forecast_either(
    client: AsyncClient, biz_headers, session: AsyncSession, business_ws
):
    invoice = await an_invoice(client, biz_headers)
    resp = await client.post(
        f"/api/invoices/{invoice['id']}/uncollectible", headers=biz_headers
    )
    assert resp.status_code == 200, resp.text

    assert await claims(session, business_ws) == []


# ---------------------------------------------------------------------------
# The double count this waited for
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_paid_invoice_leaves_the_forecast_to_the_transaction(
    client: AsyncClient, biz_headers, session: AsyncSession, business_ws, account,
    client_payee,
):
    """The reason invoices stayed out until matching existed. Once the
    payment is linked the claim is gone, and the money is counted once."""
    await an_invoice(client, biz_headers, payee_id=str(client_payee.id))
    tx = await client.post(
        "/api/transactions",
        headers=biz_headers,
        json={
            "description": "PIX ALPHA",
            "amount": "5000.00",
            "currency": "USD",
            "date": str(TODAY),
            "type": "credit",
            "account_id": str(account.id),
            "payee_id": str(client_payee.id),
        },
    )
    assert tx.status_code in (200, 201), tx.text

    # Nobody linked anything by hand: the engine did it on the way in,
    # which is the whole point. The claim is gone and the transaction
    # carries the money alone.
    assert await claims(session, business_ws) == []


@pytest.mark.asyncio
async def test_only_what_is_left_is_carried(
    client: AsyncClient, biz_headers, session: AsyncSession, business_ws, account
):
    """Half paid means half forecast. Carrying the whole total would
    count the settled half twice."""
    invoice = await an_invoice(client, biz_headers)
    tx = await client.post(
        "/api/transactions",
        headers=biz_headers,
        json={
            "description": "Sinal",
            "amount": "2000.00",
            "currency": "USD",
            "date": str(TODAY),
            "type": "credit",
            "account_id": str(account.id),
        },
    )
    await client.post(
        f"/api/invoices/{invoice['id']}/allocations",
        headers=biz_headers,
        json={"transaction_id": tx.json()["id"], "amount": "2000.00"},
    )

    found = await claims(session, business_ws)
    assert len(found) == 1
    assert found[0].amount == Decimal("3000.00")


# ---------------------------------------------------------------------------
# Where it sits in time
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_it_lands_on_the_day_it_was_promised_for(
    client: AsyncClient, biz_headers, session: AsyncSession, business_ws
):
    await an_invoice(client, biz_headers)

    assert await claims(session, business_ws, end=SOON) == [], "before the due date"
    assert len(await claims(session, business_ws, end=SOON + timedelta(days=1))) == 1


@pytest.mark.asyncio
async def test_an_overdue_invoice_is_not_relocated_to_today(
    client: AsyncClient, biz_headers, session: AsyncSession, business_ws
):
    """Still owed, deliberately not projected. The only date we have has
    passed, and moving it to today would invent one nobody promised, and
    make the aging table and the forecast disagree about one debt."""
    await an_invoice(
        client,
        biz_headers,
        issue_date=str(TODAY - timedelta(days=90)),
        due_date=str(TODAY - timedelta(days=60)),
    )

    assert await claims(session, business_ws) == []


@pytest.mark.asyncio
async def test_a_foreign_invoice_stays_in_its_own_currency(
    client: AsyncClient, biz_headers, session: AsyncSession, business_ws
):
    """Unconverted on the way out: a balance walk keeps a figure per
    currency, and converting here would force it to undo the conversion."""
    await an_invoice(client, biz_headers, currency="EUR")
    found = await claims(session, business_ws)

    assert len(found) == 1
    assert found[0].currency == "EUR"


@pytest.mark.asyncio
async def test_another_workspace_never_leaks_into_this_forecast(
    client: AsyncClient, biz_headers, session: AsyncSession, business_ws, auth_headers
):
    other = await client.post(
        "/api/workspaces",
        headers=auth_headers,
        json={"name": "Outra", "kind": "business", "self_membership": True},
    )
    await an_invoice(
        client, {**auth_headers, "X-Workspace-Id": other.json()["id"]}
    )

    assert await claims(session, business_ws) == []


# ---------------------------------------------------------------------------
# Through the dashboard, which is where a person sees it
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_projected_balance_carries_what_is_owed(
    client: AsyncClient, biz_headers, session: AsyncSession, account
):
    """Reaching the endpoint, not just the service. The claim was that
    nothing in invoicing reaches the projected balance; this is the line
    that stops being true."""
    month = TODAY.replace(day=1).isoformat()
    before = await client.get(
        f"/api/dashboard/summary?month={month}", headers=biz_headers
    )
    assert before.status_code == 200, before.text
    base = before.json()["projected_balance"]

    await an_invoice(client, biz_headers, due_date=str(TODAY + timedelta(days=1)))

    after = await client.get(
        f"/api/dashboard/summary?month={month}", headers=biz_headers
    )
    grew = after.json()["projected_balance"]
    assert float(grew.get("USD", 0)) == pytest.approx(float(base.get("USD", 0)) + 5000.0)


@pytest.mark.asyncio
async def test_the_cash_flow_report_carries_it_too(
    client: AsyncClient, biz_headers, session: AsyncSession
):
    """The dashboard and the report walk the forecast separately, so
    landing in one proves nothing about the other."""
    url = "/api/reports/cash-flow?months=6&interval=monthly"
    before = await client.get(url, headers=biz_headers)
    assert before.status_code == 200, before.text

    def projected(payload: dict) -> float:
        return sum(
            float(point["breakdowns"].get("inflow") or 0) for point in payload["trend"]
        )

    base = projected(before.json())
    await an_invoice(client, biz_headers, due_date=str(TODAY + timedelta(days=1)))

    after = await client.get(url, headers=biz_headers)
    assert projected(after.json()) == pytest.approx(base + 5000.0)


@pytest.mark.asyncio
async def test_a_bill_to_pay_lands_on_the_other_side_of_the_report(
    client: AsyncClient, biz_headers, session: AsyncSession
):
    """Filing a supplier bill under income would turn every debt into
    revenue, which is the one mistake a cash-flow report cannot make."""
    url = "/api/reports/cash-flow?months=6&interval=monthly"
    before = (await client.get(url, headers=biz_headers)).json()

    def outgoing(payload: dict) -> float:
        return sum(
            float(point["breakdowns"].get("outflow") or 0) for point in payload["trend"]
        )

    base = outgoing(before)
    await an_invoice(
        client, biz_headers, direction="payable", origin="imported",
        external_source="supplier", external_number="NF-2",
        due_date=str(TODAY + timedelta(days=1)),
    )

    after = (await client.get(url, headers=biz_headers)).json()
    assert outgoing(after) == pytest.approx(base + 5000.0)


@pytest.mark.asyncio
async def test_a_filter_that_matches_no_account_does_not_carry_claims(
    client: AsyncClient, biz_headers, session: AsyncSession, account
):
    """A collection of wallets and no bank accounts still filters.

    It coerces the account list to empty, and empty means *narrowed to
    nothing*, not *not narrowed*. Reading it as falsy put every claim in
    the workspace back into a total whose transactions had all been
    filtered away: a projected balance built from money the filter had
    just excluded."""
    month = TODAY.replace(day=1).isoformat()
    wallets_only = f"?month={month}&asset_group_ids={uuid.uuid4()}"

    before = (await client.get(f"/api/dashboard/summary{wallets_only}", headers=biz_headers)).json()
    await an_invoice(client, biz_headers, due_date=str(TODAY + timedelta(days=1)))
    after = (await client.get(f"/api/dashboard/summary{wallets_only}", headers=biz_headers)).json()

    assert after["projected_balance"] == before["projected_balance"]

    # And unfiltered it does carry, so the guard narrowed the case rather
    # than switching the feature off.
    whole = (await client.get(f"/api/dashboard/summary?month={month}", headers=biz_headers)).json()
    assert float(whole["projected_balance"].get("USD", 0)) > 0
