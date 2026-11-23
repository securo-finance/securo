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
        ("amount_primary", "0.001"),
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
