"""The one-click "post today" quick action (issue #1144).

A pending entry is confirmed as posted and dated with the workspace's current
calendar day from a single request, the same result as saving the edit dialog
by hand. Proved over HTTP so the route, the write gate, and the not-found path
are exercised together, not just the service.
"""
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest_asyncio
from httpx import AsyncClient

from app.core.app_clock import app_today
from app.models.account import Account
from app.models.transaction import Transaction
from app.models.user import User
from app.services.workspace_service import create_personal_workspace_for_user


@pytest_asyncio.fixture
async def pending_transaction(session, test_user, test_account) -> Transaction:
    """A manual pending charge dated a few days back, in the test workspace."""
    tx = Transaction(
        id=uuid4(),
        user_id=test_user.id,
        account_id=test_account.id,
        description="PENDING CHARGE",
        amount=Decimal("42.00"),
        date=app_today() - timedelta(days=3),
        type="debit",
        source="manual",
        status="pending",
    )
    session.add(tx)
    await session.commit()
    await session.refresh(tx)
    return tx


async def test_post_today_marks_pending_posted_and_dates_it_today(
    client: AsyncClient, auth_headers, session, pending_transaction: Transaction
):
    tx = pending_transaction
    assert tx.status == "pending"
    assert tx.date != app_today(), "fixture must start on a different day than today"

    resp = await client.post(f"/api/transactions/{tx.id}/post-today", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "posted"
    assert body["date"] == app_today().isoformat()

    await session.refresh(tx)
    assert tx.status == "posted"
    assert tx.date == app_today()


async def test_post_today_does_not_touch_other_fields(
    client: AsyncClient, auth_headers, session, pending_transaction: Transaction
):
    """Only status and date move; amount, description and type are left alone."""
    tx = pending_transaction
    resp = await client.post(f"/api/transactions/{tx.id}/post-today", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert Decimal(body["amount"]) == tx.amount
    assert body["description"] == "PENDING CHARGE"
    assert body["type"] == "debit"
    assert body["source"] == "manual"


async def test_a_viewer_cannot_post_today(
    client: AsyncClient, viewer_auth_headers, pending_transaction: Transaction
):
    tx = pending_transaction
    resp = await client.post(f"/api/transactions/{tx.id}/post-today", headers=viewer_auth_headers)
    assert resp.status_code == 403, resp.text


async def test_post_today_on_an_unknown_id_says_not_found(
    client: AsyncClient, auth_headers
):
    resp = await client.post(f"/api/transactions/{uuid4()}/post-today", headers=auth_headers)
    # A missing route also answers 404, so pin the detail the endpoint sets.
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Transaction not found"


async def test_post_today_cannot_reach_another_workspace(
    client: AsyncClient, auth_headers, session
):
    """A pending row in someone else's workspace answers 404 and stays as it was."""
    other = User(
        id=uuid4(),
        email="post-today-other@example.com",
        hashed_password="x",
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    session.add(other)
    await session.flush()
    other_ws = await create_personal_workspace_for_user(session, other)
    account = Account(
        id=uuid4(),
        user_id=other.id,
        workspace_id=other_ws.id,
        name="Other Checking",
        type="checking",
        balance=Decimal("0"),
        currency="BRL",
    )
    session.add(account)
    await session.flush()
    past = app_today() - timedelta(days=3)
    foreign = Transaction(
        id=uuid4(),
        user_id=other.id,
        workspace_id=other_ws.id,
        account_id=account.id,
        description="OTHER PENDING CHARGE",
        amount=Decimal("10.00"),
        date=past,
        type="debit",
        source="manual",
        status="pending",
    )
    session.add(foreign)
    await session.commit()

    resp = await client.post(f"/api/transactions/{foreign.id}/post-today", headers=auth_headers)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Transaction not found"

    await session.refresh(foreign)
    assert foreign.status == "pending"
    assert foreign.date == past
