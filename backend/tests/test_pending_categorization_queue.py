"""#469: the dashboard's categorize queue counts the same rows as its notice.

A group expense launched in another workspace shows up in the viewer's
transaction list, because their share of it counts in their P/L there. But
categorizing it is a write to the workspace that owns the row: the update is
scoped to the workspace the viewer is in, so the row would sit in the
"transactions need categorizing" queue forever. The notice and the list it
opens leave those rows out, and both keep this workspace's own rows.
"""

import uuid
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.transaction import Transaction


async def _register(client: AsyncClient, email: str, password: str) -> None:
    resp = await client.post(
        "/api/auth/register", json={"email": email, "password": password}
    )
    assert resp.status_code in (200, 201), resp.text


async def _login(client: AsyncClient, email: str, password: str) -> dict:
    resp = await client.post(
        "/api/auth/login",
        data={"username": email, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def _uncategorized_shared_expense(
    client: AsyncClient, owner_headers: dict, friend_email: str
) -> None:
    """The owner pays 90, uncategorized, split equally with the invited member."""
    account = (
        await client.post(
            "/api/accounts",
            headers=owner_headers,
            json={"name": "Queue wallet", "type": "checking", "balance": 0, "currency": "BRL"},
        )
    ).json()
    group = (
        await client.post(
            "/api/groups",
            headers=owner_headers,
            json={"name": "Queue trip", "kind": "social", "default_currency": "BRL"},
        )
    ).json()
    me = (
        await client.post(
            f"/api/groups/{group['id']}/members",
            headers=owner_headers,
            json={"name": "Me", "is_self": True},
        )
    ).json()
    friend = (
        await client.post(
            f"/api/groups/{group['id']}/members",
            headers=owner_headers,
            json={"name": "Friend", "email": friend_email},
        )
    ).json()
    resp = await client.post(
        "/api/transactions",
        headers=owner_headers,
        json={
            "account_id": account["id"],
            "description": "R469 taxi refunded",
            "amount": 90,
            "date": "2026-11-10",
            "type": "debit",
            "currency": "BRL",
            "splits": {
                "share_type": "equal",
                "splits": [
                    {"group_member_id": me["id"]},
                    {"group_member_id": friend["id"]},
                ],
            },
        },
    )
    assert resp.status_code == 201, resp.text


async def _queue(client: AsyncClient, headers: dict, workspace_only: bool) -> dict:
    params = {"uncategorized": "true", "user_pnl_only": "true", "limit": 200}
    if workspace_only:
        params["workspace_only"] = "true"
    resp = await client.get("/api/transactions", headers=headers, params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _notice(client: AsyncClient, headers: dict) -> tuple[int, float]:
    resp = await client.get("/api/dashboard/summary", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return body["pending_categorization"], body["pending_categorization_amount"]


@pytest.mark.asyncio
async def test_member_queue_leaves_out_the_expense_from_another_workspace(
    client, auth_headers
):
    """The invited member's notice and its list count no rows: the only
    uncategorized expense is the owner's, in the owner's workspace."""
    friend_email = "queue-friend@example.com"
    await _register(client, friend_email, "friendpassword12")
    friend_headers = await _login(client, friend_email, "friendpassword12")
    await _uncategorized_shared_expense(client, auth_headers, friend_email)

    assert await _notice(client, friend_headers) == (0, pytest.approx(0.0))

    # The default list still surfaces the viewer's share of that expense.
    visible = await _queue(client, friend_headers, workspace_only=False)
    assert visible["total"] == 1

    queue = await _queue(client, friend_headers, workspace_only=True)
    assert queue["total"] == 0
    assert queue["summary"]["expense"] == pytest.approx(0.0)


async def _uncategorized_row(
    client: AsyncClient, auth_headers: dict, account_id: str, description: str
) -> str:
    resp = await client.post(
        "/api/transactions",
        headers=auth_headers,
        json={
            "account_id": account_id,
            "description": description,
            "amount": 10.03,
            "date": "2026-11-10",
            "type": "debit",
            "currency": "BRL",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_counter_reads_the_typed_conversion_of_a_row_in_the_viewers_currency(
    client, auth_headers, session: AsyncSession
):
    """#467: a row whose own amount is 10.03 and whose primary stamp is the
    10.00 the user typed (ADR 0031). Every total that reads the stamp says
    10.00, the list footer included; the counter used to add the account
    charge because the row is already in the viewer's currency. The stored
    state is written directly: what is under test is the read, and this is
    the row a display-currency round trip leaves behind.
    """
    account = (
        await client.post(
            "/api/accounts",
            headers=auth_headers,
            json={"name": "R467 typed", "type": "checking", "balance": 0, "currency": "BRL"},
        )
    ).json()
    first = await _uncategorized_row(client, auth_headers, account["id"], "R467 tip")
    row = (
        await session.execute(select(Transaction).where(Transaction.id == uuid.UUID(first)))
    ).scalar_one()
    row.amount_primary = Decimal("10.00")
    row.amount_primary_typed = Decimal("10.00")
    row.amount_primary_typed_currency = "BRL"
    await session.commit()

    count, amount = await _notice(client, auth_headers)
    assert count == 1
    assert amount == pytest.approx(10.0)

    queue = await _queue(client, auth_headers, workspace_only=True)
    assert abs(float(queue["items"][0]["amount_primary"])) == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_counter_reads_a_legacy_lowercase_code_as_the_viewers_currency(
    client, auth_headers, session: AsyncSession
):
    """#467: a row stored with a lowercase code ("brl") is in the viewer's
    currency. The counter no longer compares the code textually, so its
    stamp counts like any other."""
    account = (
        await client.post(
            "/api/accounts",
            headers=auth_headers,
            json={"name": "R467 legacy", "type": "checking", "balance": 0, "currency": "BRL"},
        )
    ).json()
    first = await _uncategorized_row(client, auth_headers, account["id"], "R467 legacy tip")
    row = (
        await session.execute(select(Transaction).where(Transaction.id == uuid.UUID(first)))
    ).scalar_one()
    row.currency = "brl"
    row.amount = Decimal("10.03")
    row.amount_primary = Decimal("10.00")
    await session.commit()

    count, amount = await _notice(client, auth_headers)
    assert count == 1
    assert amount == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_owner_keeps_notice_and_queue_equal(client, auth_headers):
    """The row's owner categorizes it from their own workspace, so it stays
    in their notice and in the list it opens."""
    friend_email = "queue-owner-friend@example.com"
    await _register(client, friend_email, "friendpassword12")
    await _uncategorized_shared_expense(client, auth_headers, friend_email)

    assert await _notice(client, auth_headers) == (1, pytest.approx(90.0))

    queue = await _queue(client, auth_headers, workspace_only=True)
    assert queue["total"] == 1
    assert abs(float(queue["items"][0]["amount_primary"])) == pytest.approx(90.0)
