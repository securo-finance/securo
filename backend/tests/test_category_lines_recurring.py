"""Recurring bills and installments split across categories (#425).

A split template hands its parts to every occurrence it generates, the
projections read the parts, and installments (created as a series, edited
across a series, or synced from the bank) carry the split along.
"""

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

import mcp_server.tools  # noqa: F401 -- registers the MCP handlers
from app.core.app_clock import app_today
from app.models.account import Account
from app.models.category import Category
from app.models.transaction import Transaction
from app.providers.base import AccountData, TransactionData
from app.services.connection_service import sync_connection
from app.services.recurring_transaction_service import generate_pending
from mcp_server.auth import CallContext
from mcp_server.registry import REGISTRY
from tests.test_connection_service import _make_category, _make_connection

pytestmark = pytest.mark.asyncio


async def _account(client, headers) -> dict:
    resp = await client.post(
        "/api/accounts", headers=headers,
        json={"name": "Checking", "type": "checking", "balance": 0, "currency": "USD"},
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


def _percent(*parts) -> dict:
    return {
        "share_type": "percent",
        "lines": [{"category_id": str(c.id), "share_pct": p} for c, p in parts],
    }


async def _rent(client, headers, account_id, food, home, *, amount=1000, start=None, **extra) -> dict:
    payload = {
        "description": "Rent + condo", "amount": amount, "currency": "USD", "type": "debit",
        "frequency": "monthly", "day_of_month": 5,
        "start_date": (start or date(2026, 1, 5)).isoformat(),
        "account_id": account_id,
        "category_lines": _percent((home, 70), (food, 30)),
    }
    payload.update(extra)
    resp = await client.post("/api/recurring-transactions", headers=headers, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _parts(body) -> list[tuple[str, Decimal]]:
    return [(line["category_id"], Decimal(str(line["amount"]))) for line in body["category_lines"]]


# --- Template ---------------------------------------------------------------


async def test_a_split_template_keeps_its_parts(client, auth_headers, test_categories):
    food, home, _ = test_categories
    account = await _account(client, auth_headers)
    rent = await _rent(client, auth_headers, account["id"], food, home)

    assert rent["has_category_lines"] is True
    assert rent["category_id"] is None
    assert _parts(rent) == [(str(home.id), Decimal("700.00")), (str(food.id), Decimal("300.00"))]


async def test_editing_the_template_amount_resizes_its_parts(client, auth_headers, test_categories):
    food, home, _ = test_categories
    account = await _account(client, auth_headers)
    rent = await _rent(client, auth_headers, account["id"], food, home)

    resp = await client.patch(
        f"/api/recurring-transactions/{rent['id']}", headers=auth_headers,
        json={"amount": 1200, "category_id": None},
    )
    assert resp.status_code == 200, resp.text
    assert _parts(resp.json()) == [(str(home.id), Decimal("840.00")), (str(food.id), Decimal("360.00"))]


async def test_undoing_a_template_split_keeps_the_largest_part(client, auth_headers, test_categories):
    food, home, _ = test_categories
    account = await _account(client, auth_headers)
    rent = await _rent(client, auth_headers, account["id"], food, home)

    resp = await client.patch(
        f"/api/recurring-transactions/{rent['id']}", headers=auth_headers,
        json={"category_lines": {"share_type": "exact", "lines": []}, "category_id": None},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["has_category_lines"] is False
    assert body["category_id"] == str(home.id)


@pytest.mark.parametrize(
    "lines",
    [
        _percent,  # replaced below with a one-line split
        "category_and_lines",
        "does_not_add_up",
    ],
)
async def test_a_template_split_is_validated_like_a_transaction(
    client, auth_headers, test_categories, lines
):
    food, home, _ = test_categories
    account = await _account(client, auth_headers)
    if lines is _percent:
        extra = {"category_lines": {"share_type": "percent", "lines": [
            {"category_id": str(home.id), "share_pct": 100},
        ]}}
    elif lines == "category_and_lines":
        extra = {"category_id": str(food.id)}
    else:
        extra = {"category_lines": _percent((home, 70), (food, 20))}
    payload = {
        "description": "Rent", "amount": 1000, "currency": "USD", "type": "debit",
        "frequency": "monthly", "day_of_month": 5, "start_date": "2026-01-05",
        "account_id": account["id"], "category_lines": _percent((home, 70), (food, 30)),
    }
    payload.update(extra)
    resp = await client.post("/api/recurring-transactions", headers=auth_headers, json=payload)
    assert resp.status_code == 400, resp.text


async def test_an_ignored_category_cannot_hold_a_template_part(
    client, auth_headers, session, test_user, test_categories
):
    food, _, _ = test_categories
    ignored = Category(id=uuid.uuid4(), user_id=test_user.id, name="Ignored", is_ignored=True)
    session.add(ignored)
    await session.commit()
    account = await _account(client, auth_headers)
    resp = await client.post(
        "/api/recurring-transactions", headers=auth_headers,
        json={"description": "Rent", "amount": 1000, "currency": "USD", "type": "debit",
              "frequency": "monthly", "day_of_month": 5, "start_date": "2026-01-05",
              "account_id": account["id"], "category_lines": _percent((ignored, 70), (food, 30))},
    )
    assert resp.status_code == 400


# --- Generation -------------------------------------------------------------


async def test_every_generated_occurrence_starts_split(
    client, auth_headers, session, test_user, test_categories
):
    food, home, _ = test_categories
    account = await _account(client, auth_headers)
    rent = await _rent(
        client, auth_headers, account["id"], food, home, amount="1000.01",
        start=date(2026, 1, 5),
    )

    count = await generate_pending(session, test_user.id, up_to=date(2026, 3, 10), include_manual=True)
    assert count == 3

    rows = (await session.execute(
        select(Transaction).where(Transaction.recurring_transaction_id == uuid.UUID(rent["id"]))
        .order_by(Transaction.date)
    )).scalars().all()
    assert len(rows) == 3
    for row in rows:
        assert row.has_category_lines is True
        assert row.category_id is None
        amounts = [line.amount for line in row.category_lines]
        assert sum(amounts) == Decimal("1000.01")
        assert [line.share_pct for line in row.category_lines] == [Decimal("70"), Decimal("30")]
        assert amounts == [Decimal("700.01"), Decimal("300.00")]


# --- Projections ------------------------------------------------------------


async def test_projections_read_the_template_parts(client, auth_headers, session, test_user, test_categories):
    food, home, _ = test_categories
    invest = Category(
        id=uuid.uuid4(), user_id=test_user.id, name="Savings", treat_as_transfer=True,
    )
    session.add(invest)
    await session.commit()
    account = await _account(client, auth_headers)
    next_month = date(app_today().year + 1, 1, 1)
    resp = await client.post(
        "/api/recurring-transactions", headers=auth_headers,
        json={"description": "Paycheck saving", "amount": 1000, "currency": "USD", "type": "debit",
              "frequency": "monthly", "day_of_month": 5, "start_date": next_month.isoformat(),
              "account_id": account["id"],
              "category_lines": _percent((home, 60), (food, 30), (invest, 10))},
    )
    assert resp.status_code == 201, resp.text
    month = next_month.isoformat()

    spending = (await client.get(
        "/api/dashboard/spending-by-category", headers=auth_headers, params={"month": month},
    )).json()
    projected = {row["category_name"]: row["projected_total"] for row in spending}
    assert projected == {home.name: 600.0, food.name: 300.0}

    summary = (await client.get(
        "/api/dashboard/summary", headers=auth_headers, params={"month": month},
    )).json()
    # The transfer-like part leaves the projected expenses.
    assert summary["projected_expenses"] == 900.0

    budgets = (await client.get(
        "/api/budgets/comparison", headers=auth_headers, params={"month": month},
    )).json()
    by_name = {row["category_name"]: float(row["projected_amount"]) for row in budgets}
    assert by_name[home.name] == 600.0 and by_name[food.name] == 300.0

    projected_rows = (await client.get(
        "/api/dashboard/projected-transactions", headers=auth_headers, params={"month": month},
    )).json()
    (row,) = [r for r in projected_rows if r["description"] == "Paycheck saving"]
    assert row["has_category_lines"] is True
    assert [(p["category_name"], p["amount"], p["treat_as_transfer"]) for p in row["category_lines"]] == [
        (home.name, 600.0, False), (food.name, 300.0, False), (invest.name, 100.0, True),
    ]

    calendar = (await client.get(
        "/api/transactions/calendar", headers=auth_headers, params={"month": month},
    )).json()
    # The first occurrence falls on the start date.
    day = next(
        d for d in calendar["days"]
        if d["in_month"] and any(i["description"] == "Paycheck saving" for i in d["items"])
    )
    assert day["projected_expense"] == 900.0
    assert day["projected_transfer_net"] == -100.0
    assert [i["has_category_lines"] for i in day["items"]] == [True]


# --- Installments -----------------------------------------------------------


async def test_editing_a_series_with_parts_splits_every_installment(client, auth_headers, test_categories):
    food, home, _ = test_categories
    account = await _account(client, auth_headers)
    resp = await client.post(
        "/api/transactions/installments", headers=auth_headers,
        json={"installments": 3, "base": {
            "account_id": account["id"], "description": "Sofa", "amount": 300,
            "date": "2026-10-10", "type": "debit", "category_id": str(home.id),
        }},
    )
    assert resp.status_code == 201, resp.text
    first = resp.json()[0]

    resp = await client.patch(
        f"/api/transactions/{first['id']}", headers=auth_headers,
        json={"apply_to": "all", "category_id": None, "category_lines": _percent((home, 80), (food, 20))},
    )
    assert resp.status_code == 200, resp.text

    rows = (await client.get(
        "/api/transactions", headers=auth_headers, params={"q": "Sofa"},
    )).json()["items"]
    assert len(rows) == 3
    for row in rows:
        assert row["has_category_lines"] is True
        assert _parts(row) == [(str(home.id), Decimal("240.00")), (str(food.id), Decimal("60.00"))]

    # And undone the same way.
    resp = await client.patch(
        f"/api/transactions/{first['id']}", headers=auth_headers,
        json={"apply_to": "all", "category_id": None,
              "category_lines": {"share_type": "exact", "lines": []}},
    )
    assert resp.status_code == 200, resp.text
    rows = (await client.get(
        "/api/transactions", headers=auth_headers, params={"q": "Sofa"},
    )).json()["items"]
    assert all(r["has_category_lines"] is False and r["category_id"] == str(home.id) for r in rows)


async def test_a_synced_installment_inherits_the_previous_parcel_split(session, test_user, test_workspace):
    conn = await _make_connection(session, test_user.id, "Split Installment Bank")
    food = await _make_category(session, test_user.id, "Groceries")
    home = await _make_category(session, test_user.id, "Home")

    provider = AsyncMock()
    provider.refresh_credentials = AsyncMock(return_value={"token": "t"})
    provider.get_accounts = AsyncMock(return_value=[AccountData(
        external_id="split-installment-acc", name="Card", type="credit_card",
        balance=Decimal("0"), currency="BRL",
    )])
    provider.get_transactions = AsyncMock(return_value=[])
    patches = (
        patch("app.services.connection_service.get_provider", return_value=provider),
        patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock),
        patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock),
    )
    for p in patches:
        p.start()
    try:
        await sync_connection(session, conn.id, test_workspace.id, test_user.id)
        account = (await session.execute(
            select(Account).where(Account.external_id == "split-installment-acc")
        )).scalar_one()
        purchase = date(2026, 1, 15)
        previous = Transaction(
            user_id=test_user.id, workspace_id=test_workspace.id, account_id=account.id,
            external_id="parcel-1", description="STORE 1/03", amount=Decimal("90.00"),
            currency="BRL", date=purchase, effective_date=purchase, type="debit",
            source="sync", status="posted", installment_number=1, total_installments=3,
            installment_total_amount=Decimal("270.00"), installment_purchase_date=purchase,
        )
        session.add(previous)
        await session.flush()
        from app.schemas.transaction_category_line import TransactionCategoryLinesInput
        from app.services import category_line_service

        await category_line_service.replace_lines(
            session, previous,
            TransactionCategoryLinesInput.model_validate(
                {"share_type": "exact", "lines": [
                    {"category_id": str(home.id), "amount": "60"},
                    {"category_id": str(food.id), "amount": "30"},
                ]}
            ),
        )
        await session.commit()

        provider.get_transactions = AsyncMock(return_value=[TransactionData(
            external_id="parcel-2", description="STORE 2/03", amount=Decimal("90.00"),
            date=date(2026, 2, 15), type="debit", currency="BRL",
            installment_number=2, total_installments=3,
            installment_total_amount=Decimal("270.00"), installment_purchase_date=purchase,
        )])
        await sync_connection(session, conn.id, test_workspace.id, test_user.id)
    finally:
        for p in patches:
            p.stop()

    parcel = (await session.execute(
        select(Transaction).where(Transaction.external_id == "parcel-2")
    )).scalar_one()
    await session.refresh(parcel, ["category_lines"])
    assert parcel.has_category_lines is True
    assert parcel.category_id is None
    assert [(line.category_id, line.amount) for line in parcel.category_lines] == [
        (home.id, Decimal("60.00")), (food.id, Decimal("30.00")),
    ]


# --- MCP --------------------------------------------------------------------


async def test_mcp_creates_and_lists_a_split_recurring(
    session, client, auth_headers, test_user, test_categories
):
    food, home, _ = test_categories
    account = await _account(client, auth_headers)
    handler = REGISTRY["propose_create_recurring_transaction"].handler
    applied = await handler(
        session=session, ctx=CallContext(user_id=test_user.id, external=True), apply=True,
        description="Rent", amount=1000, type="debit", frequency="monthly", day_of_month=5,
        account_id=account["id"], category_lines=_percent((home, 70), (food, 30)),
    )
    assert applied["applied"] is True, applied
    assert [line["amount"] for line in applied["proposed"]["category_lines"]["lines"]] == [700.0, 300.0]

    listed = await REGISTRY["list_recurring_transactions"].handler(
        session=session, ctx=CallContext(user_id=test_user.id),
    )
    (row,) = [r for r in listed["items"] if r["id"] == applied["id"]]
    assert row["category_id"] is None
    assert [(p["category_name"], p["amount"]) for p in row["category_lines"]] == [
        (home.name, 700.0), (food.name, 300.0),
    ]


# --- Review follow-ups ------------------------------------------------------


async def test_an_uncategorized_charge_a_split_bill_claims_takes_its_parts(
    client, auth_headers, session, test_user, test_categories
):
    """The charge was already there when the schedule came due: linking it
    to the bill hands it the bill's parts."""
    food, home, _ = test_categories
    account = await _account(client, auth_headers)
    resp = await client.post(
        "/api/transactions", headers=auth_headers,
        json={"account_id": account["id"], "description": "Rent + condo", "amount": 1000,
              "date": "2026-01-05", "type": "debit", "currency": "USD"},
    )
    assert resp.status_code == 201, resp.text
    charge = resp.json()
    await _rent(client, auth_headers, account["id"], food, home, start=date(2026, 1, 5))

    await generate_pending(session, test_user.id, up_to=date(2026, 1, 6), include_manual=True)

    after = (await client.get(f"/api/transactions/{charge['id']}", headers=auth_headers)).json()
    assert after["recurring_transaction_id"] is not None
    assert after["has_category_lines"] is True
    assert _parts(after) == [(str(home.id), Decimal("700.00")), (str(food.id), Decimal("300.00"))]


async def test_a_template_turned_card_credit_cannot_keep_a_transfer_part(
    client, auth_headers, session, test_user, test_categories
):
    food, _, _ = test_categories
    transfer = Category(id=uuid.uuid4(), user_id=test_user.id, name="Card payment", treat_as_transfer=True)
    session.add(transfer)
    await session.commit()
    resp = await client.post(
        "/api/accounts", headers=auth_headers,
        json={"name": "Card", "type": "credit_card", "balance": 0, "currency": "USD"},
    )
    card = resp.json()
    account = await _account(client, auth_headers)
    rent = await _rent(client, auth_headers, account["id"], food, transfer)

    resp = await client.patch(
        f"/api/recurring-transactions/{rent['id']}", headers=auth_headers,
        json={"type": "credit", "account_id": card["id"]},
    )
    assert resp.status_code == 400


async def test_a_series_wide_type_change_validates_every_split_installment(
    client, auth_headers, session, test_user, test_categories
):
    food, _, _ = test_categories
    transfer = Category(id=uuid.uuid4(), user_id=test_user.id, name="Card payment", treat_as_transfer=True)
    session.add(transfer)
    await session.commit()
    card = (await client.post(
        "/api/accounts", headers=auth_headers,
        json={"name": "Card", "type": "credit_card", "balance": 0, "currency": "USD"},
    )).json()
    resp = await client.post(
        "/api/transactions/installments", headers=auth_headers,
        json={"installments": 2, "base": {
            "account_id": card["id"], "description": "Refundable", "amount": 100,
            "date": "2026-10-10", "type": "debit", "category_id": str(food.id),
        }},
    )
    assert resp.status_code == 201, resp.text
    first, second = resp.json()
    # Only the sibling is split, with a transfer part.
    resp = await client.patch(
        f"/api/transactions/{second['id']}", headers=auth_headers,
        json={"category_id": None, "category_lines": _percent((food, 50), (transfer, 50))},
    )
    assert resp.status_code == 200, resp.text

    resp = await client.patch(
        f"/api/transactions/{first['id']}", headers=auth_headers,
        json={"apply_to": "all", "type": "credit", "category_id": None},
    )
    assert resp.status_code == 400


async def test_a_category_in_a_split_bill_counts_as_used_and_moves_with_it(
    client, auth_headers, test_categories
):
    food, home, income = test_categories
    account = await _account(client, auth_headers)
    rent = await _rent(client, auth_headers, account["id"], food, home)

    usage = (await client.get(f"/api/categories/{food.id}/usage", headers=auth_headers)).json()
    assert usage["recurring_transactions"] == 1

    resp = await client.delete(f"/api/categories/{food.id}", headers=auth_headers)
    assert resp.status_code in (400, 409), resp.text

    resp = await client.delete(
        f"/api/categories/{food.id}", headers=auth_headers,
        params={"transfer_to_category_id": str(income.id)},
    )
    assert resp.status_code == 204, resp.text
    listed = (await client.get("/api/recurring-transactions", headers=auth_headers)).json()
    (after,) = [r for r in listed if r["id"] == rent["id"]]
    assert [line["category_id"] for line in after["category_lines"]] == [str(home.id), str(income.id)]
