"""Flagging a transaction to come back to it later, with an optional reminder (issue #56)."""
from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
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


@pytest.mark.asyncio
async def test_a_reminder_rides_on_the_flag_and_unflagging_clears_it(
    client: AsyncClient, auth_headers, test_transactions: list[Transaction]
):
    tx = test_transactions[0]
    due = (app_today() + timedelta(days=3)).isoformat()

    reminded = await client.patch(
        f"/api/transactions/{tx.id}",
        json={"is_flagged": True, "flag_due_date": due, "flag_note": "  waiting for the refund  "},
        headers=auth_headers,
    )
    assert reminded.status_code == 200
    body = reminded.json()
    assert body["is_flagged"] is True
    assert body["flag_due_date"] == due
    assert body["flag_note"] == "waiting for the refund"

    # A blank note is no note.
    blank = await client.patch(
        f"/api/transactions/{tx.id}", json={"flag_note": "   "}, headers=auth_headers
    )
    assert blank.json()["flag_note"] is None
    assert blank.json()["flag_due_date"] == due

    unflagged = await client.patch(
        f"/api/transactions/{tx.id}", json={"is_flagged": False}, headers=auth_headers
    )
    assert unflagged.json()["is_flagged"] is False
    assert unflagged.json()["flag_due_date"] is None
    assert unflagged.json()["flag_note"] is None


@pytest.mark.asyncio
async def test_a_reminder_sent_for_an_unflagged_row_does_not_flag_it(
    client: AsyncClient, auth_headers, test_transactions: list[Transaction]
):
    """A note saved as the user clicks Unflag can arrive second; it must not
    bring the flag back."""
    tx = test_transactions[0]
    late = await client.patch(
        f"/api/transactions/{tx.id}",
        json={"flag_note": "typed before unflagging", "flag_due_date": "2026-12-01"},
        headers=auth_headers,
    )
    assert late.status_code == 200
    assert late.json()["is_flagged"] is False
    assert late.json()["flag_note"] is None
    assert late.json()["flag_due_date"] is None


@pytest.mark.asyncio
async def test_a_reminder_note_over_500_characters_is_refused(
    client: AsyncClient, auth_headers, test_transactions: list[Transaction]
):
    response = await client.patch(
        f"/api/transactions/{test_transactions[0].id}",
        json={"flag_note": "x" * 501},
        headers=auth_headers,
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_flag_due_filters_and_summary_follow_the_reminder_date(
    client: AsyncClient,
    auth_headers,
    session: AsyncSession,
    test_transactions: list[Transaction],
):
    today = app_today()
    overdue, soon, later = test_transactions[0], test_transactions[1], test_transactions[2]
    overdue.is_flagged, overdue.flag_due_date = True, today - timedelta(days=1)
    soon.is_flagged, soon.flag_due_date = True, today
    later.is_flagged, later.flag_due_date = True, today + timedelta(days=30)
    await session.commit()

    response = await client.get("/api/transactions?flag_due=overdue", headers=auth_headers)
    assert [i["id"] for i in response.json()["items"]] == [str(overdue.id)]

    response = await client.get("/api/transactions?flag_due=soon", headers=auth_headers)
    assert [i["id"] for i in response.json()["items"]] == [str(soon.id)]

    summary = await client.get("/api/transactions/flagged-summary", headers=auth_headers)
    assert summary.status_code == 200
    assert summary.json() == {"flagged": 3, "overdue": 1, "due_soon": 1}


@pytest.mark.asyncio
async def test_bulk_flag_sets_the_date_and_bulk_unflag_clears_it(
    client: AsyncClient,
    auth_headers,
    session: AsyncSession,
    test_transactions: list[Transaction],
):
    ids = [str(test_transactions[0].id), str(test_transactions[1].id)]
    test_transactions[0].flag_note = "keep me"
    await session.commit()
    due = (app_today() + timedelta(days=7)).isoformat()

    flagged = await client.patch(
        "/api/transactions/bulk-flag",
        json={"transaction_ids": ids, "is_flagged": True, "flag_due_date": due},
        headers=auth_headers,
    )
    assert flagged.json() == {"updated": 2}
    rows = (await client.get("/api/transactions?flagged=true", headers=auth_headers)).json()["items"]
    assert {r["id"] for r in rows} == set(ids)
    assert {r["flag_due_date"] for r in rows} == {due}
    # Flagging in bulk keeps each row's own note.
    assert next(r for r in rows if r["id"] == ids[0])["flag_note"] == "keep me"

    await client.patch(
        "/api/transactions/bulk-flag",
        json={"transaction_ids": ids, "is_flagged": False},
        headers=auth_headers,
    )
    summary = await client.get("/api/transactions/flagged-summary", headers=auth_headers)
    assert summary.json()["flagged"] == 0
    for tx in test_transactions[:2]:
        await session.refresh(tx)
        assert tx.flag_note is None and tx.flag_due_date is None


@pytest.mark.asyncio
async def test_a_viewer_cannot_bulk_flag(
    client: AsyncClient, viewer_auth_headers, test_transactions: list[Transaction]
):
    response = await client.patch(
        "/api/transactions/bulk-flag",
        json={"transaction_ids": [str(test_transactions[0].id)], "is_flagged": True},
        headers=viewer_auth_headers,
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_flagged_rows_sort_by_reminder_date_with_undated_last(
    client: AsyncClient,
    auth_headers,
    session: AsyncSession,
    test_transactions: list[Transaction],
):
    today = app_today()
    undated, later, overdue = test_transactions[0], test_transactions[1], test_transactions[2]
    undated.is_flagged = True
    later.is_flagged, later.flag_due_date = True, today + timedelta(days=10)
    overdue.is_flagged, overdue.flag_due_date = True, today - timedelta(days=2)
    await session.commit()

    response = await client.get(
        "/api/transactions?flagged=true&sort_by=flag_due_date&sort_dir=asc",
        headers=auth_headers,
    )

    assert [i["id"] for i in response.json()["items"]] == [
        str(overdue.id), str(later.id), str(undated.id),
    ]
