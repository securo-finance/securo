"""Flagging a transaction to come back to it later (issue #56)."""
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.transaction import Transaction


@pytest.mark.asyncio
async def test_flag_and_unflag_a_transaction(
    client: AsyncClient, auth_headers, test_transactions: list[Transaction]
):
    tx = test_transactions[0]

    flagged = await client.patch(
        f"/api/transactions/{tx.id}", json={"is_flagged": True}, headers=auth_headers
    )
    assert flagged.status_code == 200
    assert flagged.json()["is_flagged"] is True

    read = await client.get(f"/api/transactions/{tx.id}", headers=auth_headers)
    assert read.json()["is_flagged"] is True

    unflagged = await client.patch(
        f"/api/transactions/{tx.id}", json={"is_flagged": False}, headers=auth_headers
    )
    assert unflagged.json()["is_flagged"] is False


@pytest.mark.asyncio
async def test_an_explicit_null_flag_is_refused_and_leaves_the_row_alone(
    client: AsyncClient, auth_headers, test_transactions: list[Transaction]
):
    """The column is NOT NULL: null is a 422, never a 500 at commit."""
    tx = test_transactions[0]
    await client.patch(
        f"/api/transactions/{tx.id}", json={"is_flagged": True}, headers=auth_headers
    )

    response = await client.patch(
        f"/api/transactions/{tx.id}", json={"is_flagged": None}, headers=auth_headers
    )
    assert response.status_code == 422

    # Leaving the key out keeps the flag as it was.
    untouched = await client.patch(
        f"/api/transactions/{tx.id}", json={"notes": "check later"}, headers=auth_headers
    )
    assert untouched.status_code == 200
    assert untouched.json()["is_flagged"] is True


@pytest.mark.asyncio
async def test_flagging_a_synced_row_is_allowed(
    client: AsyncClient,
    auth_headers,
    session: AsyncSession,
    test_transactions: list[Transaction],
):
    """A flag is the user's note, not a ledger edit, so a bank row takes it."""
    tx = test_transactions[0]
    tx.source = "sync"
    await session.commit()

    response = await client.patch(
        f"/api/transactions/{tx.id}", json={"is_flagged": True}, headers=auth_headers
    )

    assert response.status_code == 200
    assert response.json()["is_flagged"] is True


@pytest.mark.asyncio
async def test_list_returns_only_flagged_rows_when_asked(
    client: AsyncClient,
    auth_headers,
    session: AsyncSession,
    test_transactions: list[Transaction],
):
    test_transactions[1].is_flagged = True
    await session.commit()

    everything = await client.get("/api/transactions", headers=auth_headers)
    assert everything.json()["total"] == len(test_transactions)

    response = await client.get("/api/transactions?flagged=true", headers=auth_headers)

    assert response.status_code == 200
    data = response.json()
    assert [item["id"] for item in data["items"]] == [str(test_transactions[1].id)]
    # The pager and the footer follow the filtered rows.
    assert data["total"] == 1
    flagged_amount = float(test_transactions[1].amount)
    totals = data["summary"]
    if test_transactions[1].type == "debit":
        assert totals["expense"] == pytest.approx(flagged_amount)
        assert totals["income"] == pytest.approx(0.0)
    else:
        assert totals["income"] == pytest.approx(flagged_amount)
        assert totals["expense"] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_a_viewer_cannot_flag(
    client: AsyncClient, viewer_auth_headers, test_transactions: list[Transaction]
):
    response = await client.patch(
        f"/api/transactions/{test_transactions[0].id}",
        json={"is_flagged": True},
        headers=viewer_auth_headers,
    )

    assert response.status_code == 403
