"""One amount bound for the transaction edit, on both of its paths (#270).

`amount` and `amount_primary` are `Numeric(15, 2)`. A PATCH of `0.001` used to
return 200 and the column stored 0.00, and `1e15` reached asyncpg and came back
as a 500. The MCP tool let both through its preview and handed the database
message to the external client.
"""
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.transaction import Transaction


async def _patch(client: AsyncClient, auth_headers, transaction_id, **body):
    return await client.patch(
        f"/api/transactions/{transaction_id}", headers=auth_headers, json=body
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "amount",
    [
        "0",  # zero is not money
        "0.001",  # rounds to 0.00 in the column
        "-5",  # direction lives in `type`
        "1000000000000000",  # 1e15, past Numeric(15, 2)
    ],
)
async def test_update_refuses_an_amount_the_column_cannot_hold(
    client: AsyncClient, auth_headers, test_transactions, amount: str
):
    tx = test_transactions[0]

    resp = await _patch(client, auth_headers, tx.id, amount=amount)

    assert resp.status_code == 422, resp.text
    assert "amount" in resp.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("amount_primary", "1000000000000000"),
        ("amount_primary", "-1"),
        ("fx_rate_used", "10000000000"),
        ("fx_rate_used", "0"),
    ],
)
async def test_update_refuses_a_conversion_the_column_cannot_hold(
    client: AsyncClient, auth_headers, test_transactions, field: str, value: str
):
    tx = test_transactions[0]

    resp = await _patch(client, auth_headers, tx.id, **{field: value})

    assert resp.status_code == 422, resp.text
    assert field in resp.text


@pytest.mark.asyncio
async def test_a_refused_amount_leaves_the_row_as_it_was(
    client: AsyncClient,
    auth_headers,
    session: AsyncSession,
    test_transactions,
):
    tx = test_transactions[0]
    before = tx.amount

    resp = await _patch(client, auth_headers, tx.id, amount="0.001")
    assert resp.status_code == 422, resp.text

    stored = await session.scalar(
        select(Transaction.amount)
        .where(Transaction.id == tx.id)
        .execution_options(populate_existing=True)
    )
    assert stored == before


@pytest.mark.asyncio
async def test_an_amount_is_rounded_to_cents_where_it_is_accepted(
    client: AsyncClient, auth_headers, test_transactions
):
    """Half a cent rounds up, exactly like the column would round it."""
    tx = test_transactions[0]

    resp = await _patch(client, auth_headers, tx.id, amount="0.005")

    assert resp.status_code == 200, resp.text
    assert Decimal(str(resp.json()["amount"])) == Decimal("0.01")


@pytest.mark.asyncio
async def test_the_largest_amount_the_column_holds_is_accepted(
    client: AsyncClient, auth_headers, test_transactions
):
    tx = test_transactions[0]

    resp = await _patch(client, auth_headers, tx.id, amount="9999999999999.99")

    assert resp.status_code == 200, resp.text
    assert Decimal(str(resp.json()["amount"])) == Decimal("9999999999999.99")


# --- The create path and the converted amount (issues #321 and #330) --------


def _new(test_account, **overrides):
    body = {
        "description": "Bank fee",
        "amount": "0.01",
        "date": "2026-11-10",
        "type": "debit",
        "account_id": str(test_account.id),
    }
    body.update(overrides)
    return body


@pytest.mark.asyncio
async def test_a_conversion_stored_as_zero_can_still_be_edited(
    client: AsyncClient, auth_headers, test_account
):
    """0.01 at 0.2 is 0.002, stored as 0.00; the dialog sends that pair back
    with every save, which answered 422 and locked the row (issue #330)."""
    created = await client.post(
        "/api/transactions",
        headers=auth_headers,
        json=_new(test_account, fx_rate_used="0.2"),
    )
    assert created.status_code == 201, created.text
    assert Decimal(str(created.json()["amount_primary"])) == Decimal("0")

    resp = await _patch(
        client,
        auth_headers,
        created.json()["id"],
        description="Bank fee (edited)",
        amount="0.01",
        amount_primary=0,
        fx_rate_used="0.2",
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["description"] == "Bank fee (edited)"
    assert Decimal(str(resp.json()["amount_primary"])) == Decimal("0")


@pytest.mark.asyncio
async def test_a_zero_conversion_typed_alone_keeps_a_rate_it_can_send_back(
    client: AsyncClient, auth_headers, test_account
):
    created = await client.post(
        "/api/transactions",
        headers=auth_headers,
        json=_new(test_account, amount_primary="0"),
    )
    assert created.status_code == 201, created.text
    body = created.json()

    resp = await _patch(
        client,
        auth_headers,
        body["id"],
        description="Again",
        amount_primary=body["amount_primary"],
        fx_rate_used=body["fx_rate_used"],
    )

    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_a_zero_rate_is_refused_next_to_a_real_conversion(
    client: AsyncClient, auth_headers, test_transactions
):
    resp = await _patch(
        client, auth_headers, test_transactions[0].id, amount_primary="10", fx_rate_used="0"
    )

    assert resp.status_code == 422, resp.text
    assert "fx_rate_used" in resp.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "amount",
    ["0", "0.001", "-5", "1000000000000000"],
)
async def test_create_refuses_an_amount_the_column_cannot_hold(
    client: AsyncClient,
    auth_headers,
    session: AsyncSession,
    test_account,
    amount: str,
):
    resp = await client.post(
        "/api/transactions",
        headers=auth_headers,
        json=_new(test_account, amount=amount, description="refused"),
    )

    assert resp.status_code == 422, resp.text
    assert "amount" in resp.text
    stored = await session.scalar(
        select(Transaction.id).where(Transaction.description == "refused")
    )
    assert stored is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides,field",
    [
        # amount x rate is past Numeric(15, 2).
        ({"amount": "9999999999999", "fx_rate_used": "5"}, "amount_primary"),
        # amount_primary / amount is past Numeric(20, 10).
        ({"amount": "0.01", "amount_primary": "9999999999"}, "fx_rate_used"),
    ],
)
async def test_create_refuses_a_derived_conversion_the_column_cannot_hold(
    client: AsyncClient, auth_headers, test_account, overrides, field: str
):
    resp = await client.post(
        "/api/transactions", headers=auth_headers, json=_new(test_account, **overrides)
    )

    assert resp.status_code == 422, resp.text
    assert field in resp.text


@pytest.mark.asyncio
async def test_update_refuses_a_derived_conversion_the_column_cannot_hold(
    client: AsyncClient, auth_headers, test_transactions
):
    """The edit keeps the stored amount, so the product is checked in the
    service; it used to reach the database as a 500."""
    tx = test_transactions[0]
    big = await _patch(client, auth_headers, tx.id, amount="9999999999999")
    assert big.status_code == 200, big.text

    resp = await _patch(client, auth_headers, tx.id, fx_rate_used="5")

    assert resp.status_code == 422, resp.text
    assert "amount_primary" in resp.text


@pytest.mark.asyncio
async def test_update_refuses_a_null_amount(
    client: AsyncClient, auth_headers, test_transactions
):
    resp = await _patch(client, auth_headers, test_transactions[0].id, amount=None)

    assert resp.status_code == 422, resp.text
    assert "amount" in resp.text
