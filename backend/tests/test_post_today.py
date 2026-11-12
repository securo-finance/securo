"""The one-click "post today" quick action (issue #1144).

A pending manual entry is confirmed as posted and dated with the workspace's
current calendar day from a single request. Synchronized dates remain
provider-owned. Proved over HTTP so the route, the write gate, and the not-found
path are exercised together, not just the service.
"""
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.core.app_clock import app_today
from app.models.account import Account
from app.models.fx_rate import FxRate
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


@pytest.mark.parametrize("paired_source", ["sync", "manual"])
async def test_post_today_preserves_synced_pair_provider_fields(
    client: AsyncClient,
    auth_headers,
    session,
    test_user,
    test_workspace,
    pending_transaction: Transaction,
    monkeypatch,
    paired_source,
):
    fixed_today = date(2025, 3, 10)
    monkeypatch.setattr(
        "app.services.transaction_service.app_today", lambda: fixed_today
    )
    test_user.preferences = {**(test_user.preferences or {}), "currency_display": "BRL"}
    paired_account = Account(
        id=uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name=f"{paired_source} pair",
        type="checking",
        balance=Decimal("0"),
        currency="USD",
    )
    pair_id = uuid4()
    pending_transaction.transfer_pair_id = pair_id
    paired = Transaction(
        id=uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=paired_account.id,
        description="Paired transfer",
        amount=Decimal("123.45"),
        currency="USD",
        date=date(2025, 1, 15),
        effective_date=date(2025, 2, 20),
        type="credit",
        source=paired_source,
        status="pending",
        transfer_pair_id=pair_id,
        amount_primary=Decimal("678.90") if paired_source == "sync" else Decimal("617.25"),
        fx_rate_used=Decimal("5.5") if paired_source == "sync" else Decimal("5"),
    )
    session.add_all(
        [
            paired_account,
            paired,
            FxRate(
                base_currency="USD",
                quote_currency="BRL",
                date=date(2025, 1, 15),
                rate=Decimal("5"),
                source="test",
            ),
            FxRate(
                base_currency="USD",
                quote_currency="BRL",
                date=fixed_today,
                rate=Decimal("6"),
                source="test",
            ),
        ]
    )
    await session.commit()
    protected = (
        paired.amount,
        paired.date,
        paired.amount_primary,
        paired.fx_rate_used,
        paired.effective_date,
    )

    response = await client.post(
        f"/api/transactions/{pending_transaction.id}/post-today",
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    await session.refresh(pending_transaction)
    await session.refresh(paired)

    assert pending_transaction.status == "posted"
    assert pending_transaction.date == fixed_today
    assert paired.status == "posted"
    if paired_source == "sync":
        assert (
            paired.amount,
            paired.date,
            paired.amount_primary,
            paired.fx_rate_used,
            paired.effective_date,
        ) == protected
    else:
        assert paired.amount == Decimal("123.45")
        assert paired.amount_primary == Decimal("740.70")
        assert paired.fx_rate_used == Decimal("6")
        assert paired.date == fixed_today
        assert paired.effective_date == fixed_today


@pytest.mark.parametrize(
    "synced_date", [date(2025, 3, 7), date(2025, 3, 10)]
)
async def test_post_today_rejects_synced_anchor(
    client: AsyncClient,
    auth_headers,
    session,
    pending_transaction: Transaction,
    monkeypatch,
    synced_date,
):
    fixed_today = date(2025, 3, 10)
    monkeypatch.setattr(
        "app.services.transaction_service.app_today", lambda: fixed_today
    )
    synced = pending_transaction
    synced.source = "sync"
    synced.date = synced_date
    synced.effective_date = date(2025, 2, 20)
    synced.amount_primary = Decimal("678.90")
    synced.fx_rate_used = Decimal("5.5")
    await session.commit()
    original = (
        synced.status,
        synced.date,
        synced.amount_primary,
        synced.fx_rate_used,
        synced.effective_date,
    )

    response = await client.post(
        f"/api/transactions/{synced.id}/post-today", headers=auth_headers
    )
    assert response.status_code == 400
    assert response.json()["detail"] == (
        "Cannot change amount or date on a synchronized transaction"
    )
    response = await client.get(
        f"/api/transactions/{synced.id}", headers=auth_headers
    )
    assert response.status_code == 200
    await session.refresh(synced)
    assert (
        synced.status,
        synced.date,
        synced.amount_primary,
        synced.fx_rate_used,
        synced.effective_date,
    ) == original


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
