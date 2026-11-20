"""Issue #243: the transactions footer is the viewer's P/L.

`GET /api/transactions` summarizes every matching row. A row shared from
another workspace (the group owner sees the invited member's expense, and
the other way round) used to enter that summary with its whole amount, so
the footer said 140 while the dashboard counted 70. The summary now reads
the viewer's split share, the same figure the dashboard and reports use.
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fx_rate import FxRate
from app.models.transaction import Transaction
from app.models.user import User


async def _register(client: AsyncClient, email: str) -> None:
    resp = await client.post(
        "/api/auth/register",
        json={"email": email, "password": "guestpassword12"},
    )
    assert resp.status_code in (200, 201), resp.text


async def _login(client: AsyncClient, email: str) -> dict:
    resp = await client.post(
        "/api/auth/login",
        data={"username": email, "password": "guestpassword12"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def _create_account(
    client: AsyncClient, headers: dict, currency: str, name: str = "Wallet"
) -> dict:
    resp = await client.post(
        "/api/accounts",
        headers=headers,
        json={"name": name, "type": "checking", "balance": 0, "currency": currency},
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


async def _trip_group(
    client: AsyncClient, owner_headers: dict, guest_email: str
) -> tuple[dict, dict, dict]:
    group = (
        await client.post(
            "/api/groups",
            headers=owner_headers,
            json={"name": "R243 Trip", "kind": "social", "default_currency": "BRL"},
        )
    ).json()
    me = (
        await client.post(
            f"/api/groups/{group['id']}/members",
            headers=owner_headers,
            json={"name": "Me", "is_self": True},
        )
    ).json()
    guest = (
        await client.post(
            f"/api/groups/{group['id']}/members",
            headers=owner_headers,
            json={"name": "Guest", "email": guest_email},
        )
    ).json()
    assert guest["linked_user_id"]
    return group, me, guest


async def _add_expense(
    client: AsyncClient,
    headers: dict,
    account_id: str,
    description: str,
    amount: float,
    currency: str,
    splits: list[dict],
    *,
    share_type: str = "equal",
    amount_primary: float | None = None,
    fx_rate_used: float | None = None,
) -> dict:
    payload = {
        "account_id": account_id,
        "description": description,
        "amount": amount,
        "date": date.today().isoformat(),
        "type": "debit",
        "currency": currency,
        "splits": {"share_type": share_type, "splits": splits},
    }
    if amount_primary is not None:
        payload["amount_primary"] = amount_primary
    if fx_rate_used is not None:
        payload["fx_rate_used"] = fx_rate_used
    resp = await client.post("/api/transactions", headers=headers, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _summary(
    client: AsyncClient, headers: dict, query: str = "R243", **params
) -> dict:
    resp = await client.get(
        "/api/transactions", headers=headers, params={"q": query, **params}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["summary"] is not None
    return body


@pytest.fixture
async def shared_trip(client: AsyncClient, auth_headers: dict, session: AsyncSession):
    """Owner (fixture user) and a guest in another workspace share a group.

    The guest pays a 100,00 dinner split 50/50 in their own workspace; the
    owner pays a 40,00 taxi split 50/50 in theirs. Both are in BRL so the
    dashboard card can be compared with the footer.
    """
    guest_email = f"r243-guest-{uuid.uuid4().hex[:8]}@example.com"
    await _register(client, guest_email)
    guest_headers = await _login(client, guest_email)

    # The guest reports in BRL too, so their own footer is comparable.
    user = (
        await session.execute(select(User).where(User.email == guest_email))
    ).scalar_one()
    user.preferences = {**(user.preferences or {}), "currency_display": "BRL"}
    await session.commit()

    group, me, guest = await _trip_group(client, auth_headers, guest_email)

    owner_account = await _create_account(client, auth_headers, "BRL", "Owner wallet")
    guest_account = await _create_account(
        client, guest_headers, "BRL", "Guest wallet"
    )

    dinner = await _add_expense(
        client,
        guest_headers,
        guest_account["id"],
        "R243 Dinner paid by guest",
        100.0,
        "BRL",
        [
            {"group_member_id": me["id"]},
            {"group_member_id": guest["id"]},
        ],
    )
    taxi = await _add_expense(
        client,
        auth_headers,
        owner_account["id"],
        "R243 Taxi paid by owner",
        40.0,
        "BRL",
        [
            {"group_member_id": me["id"]},
            {"group_member_id": guest["id"]},
        ],
    )
    return {
        "guest_headers": guest_headers,
        "group": group,
        "me": me,
        "guest": guest,
        "dinner": dinner,
        "taxi": taxi,
    }


@pytest.mark.asyncio
async def test_footer_counts_viewer_share_not_the_whole_shared_row(
    client: AsyncClient, auth_headers: dict, shared_trip: dict, session: AsyncSession
):
    """Owner: 20 (their half of the taxi) + 50 (their half of the guest's
    dinner) = 70. On main the shared row entered whole and the footer said
    140."""
    body = await _summary(client, auth_headers)
    assert body["summary"]["expense"] == pytest.approx(70.0)
    assert body["summary"]["currency"] == "BRL"

    descriptions = {item["description"] for item in body["items"]}
    assert {"R243 Taxi paid by owner", "R243 Dinner paid by guest"} <= descriptions
    shared = next(item for item in body["items"] if item["is_shared"])
    assert float(shared["viewer_share"]) == pytest.approx(50.0)

    # The dashboard card tells the same story.
    dashboard = await client.get("/api/dashboard/summary", headers=auth_headers)
    assert dashboard.status_code == 200, dashboard.text
    assert dashboard.json()["monthly_expenses"] == pytest.approx(70.0)


@pytest.mark.asyncio
async def test_footer_is_symmetric_for_the_guest(
    client: AsyncClient, auth_headers: dict, shared_trip: dict
):
    """The invited member counts their 50 dinner share plus their 20 of the
    owner's taxi — the same 70, not the 100 they paid."""
    guest_headers = shared_trip["guest_headers"]
    body = await _summary(client, guest_headers)
    assert body["summary"]["expense"] == pytest.approx(70.0)
    assert body["summary"]["currency"] == "BRL"


@pytest.mark.asyncio
async def test_footer_with_user_pnl_only_matches_the_dashboard_card(
    client: AsyncClient, auth_headers: dict, shared_trip: dict
):
    """The dashboard drill-down (`user_pnl_only=true`) must sum to the card
    it opened from, shared rows included."""
    body = await _summary(client, auth_headers, user_pnl_only="true")
    assert body["summary"]["expense"] == pytest.approx(70.0)

    dashboard = await client.get("/api/dashboard/summary", headers=auth_headers)
    card = dashboard.json()["monthly_expenses"]
    assert body["summary"]["expense"] == pytest.approx(card)


@pytest.mark.asyncio
async def test_footer_counts_share_for_percent_and_exact_splits(
    client: AsyncClient, auth_headers: dict
):
    """A 30/70 percent split and an exact 80/20 split both enter the owner's
    footer by their share (30 and 80), not by the whole 100."""
    guest_email = f"r243-percent-{uuid.uuid4().hex[:8]}@example.com"
    await _register(client, guest_email)
    guest_headers = await _login(client, guest_email)
    _group, me, guest = await _trip_group(client, auth_headers, guest_email)
    guest_account = await _create_account(client, guest_headers, "BRL", "Guest wallet")

    await _add_expense(
        client,
        guest_headers,
        guest_account["id"],
        "R243 Percent dinner",
        100.0,
        "BRL",
        [
            {"group_member_id": me["id"], "share_pct": 30},
            {"group_member_id": guest["id"], "share_pct": 70},
        ],
        share_type="percent",
    )
    await _add_expense(
        client,
        guest_headers,
        guest_account["id"],
        "R243 Exact dinner",
        100.0,
        "BRL",
        [
            {"group_member_id": me["id"], "share_amount": 80},
            {"group_member_id": guest["id"], "share_amount": 20},
        ],
        share_type="exact",
    )

    body = await _summary(client, auth_headers)
    assert body["summary"]["expense"] == pytest.approx(110.0)  # 30 + 80
    assert body["summary"]["currency"] == "BRL"


@pytest.mark.asyncio
async def test_footer_converts_a_shared_row_from_its_own_currency(
    client: AsyncClient, auth_headers: dict, session: AsyncSession
):
    """The shared row is in USD; the owner reports in BRL. Its 100,00 USD
    stamp to 500,00 BRL at the stored rate, so the owner's half is 250,00 —
    the footer carries the converted share, not 100."""
    guest_email = f"r243-fx-{uuid.uuid4().hex[:8]}@example.com"
    await _register(client, guest_email)
    guest_headers = await _login(client, guest_email)
    user = (
        await session.execute(select(User).where(User.email == guest_email))
    ).scalar_one()
    user.preferences = {**(user.preferences or {}), "currency_display": "BRL"}
    await session.commit()

    session.add(
        FxRate(
            base_currency="USD",
            quote_currency="BRL",
            date=date.today(),
            rate=Decimal("5.0"),
            source="test",
        )
    )
    await session.commit()

    _group, me, guest = await _trip_group(client, auth_headers, guest_email)
    guest_account = await _create_account(client, guest_headers, "USD", "Guest wallet")

    await _add_expense(
        client,
        guest_headers,
        guest_account["id"],
        "R243 Dollar dinner",
        100.0,
        "USD",
        [
            {"group_member_id": me["id"]},
            {"group_member_id": guest["id"]},
        ],
    )

    body = await _summary(client, auth_headers)
    assert body["summary"]["expense"] == pytest.approx(250.0)
    assert body["summary"]["currency"] == "BRL"


@pytest.mark.asyncio
async def test_footer_follows_a_shared_row_whose_amount_the_sync_changed(
    client: AsyncClient, auth_headers: dict, shared_trip: dict, session: AsyncSession
):
    """The bank sync rewrites `amount` and rescales the category lines, but
    leaves the splits alone (connection_service.py). The owner's 40,00 taxi
    becomes 60,00 while its splits still say 20/20; reading the stored share
    against the new total would leave the unallocated 20,00 out of the
    owner's side. The owner's share is what the other member doesn't have:
    60 - 20 = 40, plus the 50 dinner share = 90, the same figure the
    dashboard card shows."""
    taxi_id = uuid.UUID(shared_trip["taxi"]["id"])
    await session.execute(
        update(Transaction)
        .where(Transaction.id == taxi_id)
        .values(amount=Decimal("60.00"), amount_primary=Decimal("60.00"))
    )
    await session.commit()

    body = await _summary(client, auth_headers)
    assert body["summary"]["expense"] == pytest.approx(90.0)

    dashboard = await client.get("/api/dashboard/summary", headers=auth_headers)
    assert dashboard.status_code == 200, dashboard.text
    assert float(dashboard.json()["monthly_expenses"]) == pytest.approx(
        body["summary"]["expense"]
    )
