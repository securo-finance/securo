"""End-to-end tests for the 'shared transactions become first-class
in the linked member's ledger' design:

  - Shared rows show up in /api/transactions for the linked member.
  - The viewer sees their share, not the parent's full amount.
  - Settlement-created debits are excluded from spending reports.
  - Owner doesn't see duplicates (they own the parent).
"""

import pytest
from httpx import AsyncClient


async def _register(client: AsyncClient, email: str, password: str) -> dict:
    resp = await client.post(
        "/api/auth/register",
        json={"email": email, "password": password},
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


async def _login(client: AsyncClient, email: str, password: str) -> dict:
    resp = await client.post(
        "/api/auth/login",
        data={"username": email, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def _create_account(client, headers, name="Wallet"):
    resp = await client.post(
        "/api/accounts",
        headers=headers,
        json={"name": name, "type": "checking", "balance": 0, "currency": "USD"},
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


async def _create_group_with_self_and_friend(client, owner_headers, friend_email):
    g = (
        await client.post(
            "/api/groups",
            headers=owner_headers,
            json={"name": "Trip", "kind": "social", "default_currency": "USD"},
        )
    ).json()
    me = (
        await client.post(
            f"/api/groups/{g['id']}/members",
            headers=owner_headers,
            json={"name": "Me", "is_self": True},
        )
    ).json()
    friend = (
        await client.post(
            f"/api/groups/{g['id']}/members",
            headers=owner_headers,
            json={"name": "Friend", "email": friend_email},
        )
    ).json()
    return g, me, friend


@pytest.mark.asyncio
async def test_linked_member_sees_shared_transaction_in_their_list(client, auth_headers, test_user):
    # Owner = the existing test_user.
    # Friend = a freshly registered user we'll link.
    friend_email = "shared-tx-friend@example.com"
    await _register(client, friend_email, "friendpassword12")
    friend_headers = await _login(client, friend_email, "friendpassword12")

    account = await _create_account(client, auth_headers)
    group, me_member, friend_member = await _create_group_with_self_and_friend(
        client, auth_headers, friend_email
    )

    # Owner creates a $90 expense split equally with the friend.
    resp = await client.post(
        "/api/transactions",
        headers=auth_headers,
        json={
            "account_id": account["id"],
            "description": "Concert Tickets",
            "amount": 90,
            "date": "2026-04-28",
            "type": "debit",
            "currency": "USD",
            "splits": {
                "share_type": "equal",
                "splits": [
                    {"group_member_id": me_member["id"]},
                    {"group_member_id": friend_member["id"]},
                ],
            },
        },
    )
    assert resp.status_code == 201

    # Friend's list should now include the shared transaction with
    # viewer_share = 45 and is_shared = True.
    list_resp = await client.get("/api/transactions", headers=friend_headers)
    assert list_resp.status_code == 200
    items = list_resp.json()["items"]
    shared = [t for t in items if t.get("is_shared")]
    assert len(shared) == 1
    assert shared[0]["description"] == "Concert Tickets"
    assert float(shared[0]["viewer_share"]) == 45.0
    assert shared[0]["group_id"] == group["id"]


@pytest.mark.asyncio
async def test_owner_does_not_see_shared_duplicate(client, auth_headers, test_user):
    """Owners see their own transaction once, never tagged as shared."""
    friend_email = "shared-tx-owner-test@example.com"
    await _register(client, friend_email, "friendpassword12")

    account = await _create_account(client, auth_headers)
    _, me, friend = await _create_group_with_self_and_friend(client, auth_headers, friend_email)

    await client.post(
        "/api/transactions",
        headers=auth_headers,
        json={
            "account_id": account["id"],
            "description": "Dinner",
            "amount": 60,
            "date": "2026-04-28",
            "type": "debit",
            "currency": "USD",
            "splits": {
                "share_type": "equal",
                "splits": [
                    {"group_member_id": me["id"]},
                    {"group_member_id": friend["id"]},
                ],
            },
        },
    )

    list_resp = await client.get("/api/transactions", headers=auth_headers)
    rows = list_resp.json()["items"]
    dinner_rows = [r for r in rows if r["description"] == "Dinner"]
    assert len(dinner_rows) == 1
    assert dinner_rows[0]["is_shared"] is False
    # viewer_share is also populated for the owner (= their own split
    # share), so the UI can render "your share" alongside the full
    # parent amount. is_shared stays False — no duplicate row.
    assert float(dinner_rows[0]["viewer_share"]) == 30.0


@pytest.mark.asyncio
async def test_owner_viewer_share_null_when_owner_not_in_splits(client, auth_headers, test_user):
    """If the owner paid for others without including themselves in the
    split (e.g. covering a friend's expense), viewer_share stays None
    so the UI doesn't render a misleading 'your share: $0' line."""
    friend_email = "owner-not-in-splits@example.com"
    await _register(client, friend_email, "friendpassword12")

    account = await _create_account(client, auth_headers)
    _, _me, friend = await _create_group_with_self_and_friend(client, auth_headers, friend_email)

    await client.post(
        "/api/transactions",
        headers=auth_headers,
        json={
            "account_id": account["id"],
            "description": "Gift",
            "amount": 40,
            "date": "2026-04-28",
            "type": "debit",
            "currency": "USD",
            # Owner pays but only the friend is in the split (owner is
            # covering the friend's whole share — common gift / loan case).
            "splits": {
                "share_type": "equal",
                "splits": [{"group_member_id": friend["id"]}],
            },
        },
    )

    list_resp = await client.get("/api/transactions", headers=auth_headers)
    rows = list_resp.json()["items"]
    gift_rows = [r for r in rows if r["description"] == "Gift"]
    assert len(gift_rows) == 1
    assert gift_rows[0]["is_shared"] is False
    assert gift_rows[0]["viewer_share"] is None


@pytest.mark.asyncio
async def test_settlement_with_account_id_creates_settlement_source_tx(
    client, auth_headers, test_user
):
    """When the payer settles via account_id, the auto-created tx is
    tagged source='settlement' so reports don't double-count it."""
    friend_email = "settlement-source-test@example.com"
    await _register(client, friend_email, "friendpassword12")
    friend_headers = await _login(client, friend_email, "friendpassword12")

    account = await _create_account(client, auth_headers)
    group, me, friend = await _create_group_with_self_and_friend(client, auth_headers, friend_email)

    # Owner pays $100, splits equally — friend ends up owing $50.
    await client.post(
        "/api/transactions",
        headers=auth_headers,
        json={
            "account_id": account["id"],
            "description": "Show",
            "amount": 100,
            "date": "2026-04-28",
            "type": "debit",
            "currency": "USD",
            "splits": {
                "share_type": "equal",
                "splits": [
                    {"group_member_id": me["id"]},
                    {"group_member_id": friend["id"]},
                ],
            },
        },
    )

    # Friend creates their own account and settles their $50 debt with
    # the optional account_id flow → backend creates a debit tx in
    # their account, tagged source='settlement'.
    friend_account = await _create_account(client, friend_headers, name="Friend's wallet")
    settle_resp = await client.post(
        f"/api/groups/{group['id']}/settlements",
        headers=friend_headers,
        json={
            "from_member_id": friend["id"],
            "to_member_id": me["id"],
            "amount": "50.00",
            "currency": "USD",
            "date": "2026-04-29",
            "account_id": friend_account["id"],
        },
    )
    assert settle_resp.status_code == 201, settle_resp.text
    settlement = settle_resp.json()
    assert settlement["transaction_id"] is not None

    # Friend's transaction list now has TWO rows: the shared $50
    # share (Show) and the settlement debit ($50). Total spending
    # reports must NOT count both — settlement.source filters it out.
    list_resp = await client.get("/api/transactions", headers=friend_headers)
    rows = list_resp.json()["items"]
    settlement_rows = [r for r in rows if r["id"] == settlement["transaction_id"]]
    assert len(settlement_rows) == 1
    assert settlement_rows[0]["source"] == "settlement"

    # The shared row is still present with viewer_share = 50.
    shared_rows = [r for r in rows if r.get("is_shared")]
    assert len(shared_rows) == 1
    assert float(shared_rows[0]["viewer_share"]) == 50.0


@pytest.mark.asyncio
async def test_group_filter_still_works_for_linked_member(client, auth_headers, test_user):
    """Sanity: the group_id filter on /transactions still scopes to
    the group's transactions for a linked member."""
    friend_email = "group-filter-shared@example.com"
    await _register(client, friend_email, "friendpassword12")
    friend_headers = await _login(client, friend_email, "friendpassword12")

    account = await _create_account(client, auth_headers)
    group, me, friend = await _create_group_with_self_and_friend(client, auth_headers, friend_email)
    await client.post(
        "/api/transactions",
        headers=auth_headers,
        json={
            "account_id": account["id"],
            "description": "Brunch",
            "amount": 40,
            "date": "2026-04-28",
            "type": "debit",
            "currency": "USD",
            "splits": {
                "share_type": "equal",
                "splits": [
                    {"group_member_id": me["id"]},
                    {"group_member_id": friend["id"]},
                ],
            },
        },
    )

    resp = await client.get(
        "/api/transactions",
        headers=friend_headers,
        params={"group_id": group["id"]},
    )
    assert resp.status_code == 200
    rows = resp.json()["items"]
    assert {r["description"] for r in rows} == {"Brunch"}
