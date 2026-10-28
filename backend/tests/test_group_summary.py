"""Group spending aggregates: they must cover every split, not a page of them."""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from httpx import AsyncClient

from app.models.transaction import Transaction
from app.models.transaction_split import TransactionSplit

pytestmark = pytest.mark.asyncio


async def _group_with_member(client: AsyncClient, auth_headers: dict):
    resp = await client.post(
        "/api/groups",
        headers=auth_headers,
        json={"name": "Flat", "kind": "social", "default_currency": "USD"},
    )
    assert resp.status_code == 201, resp.text
    group = resp.json()
    resp = await client.post(
        f"/api/groups/{group['id']}/members", headers=auth_headers, json={"name": "Alice"}
    )
    assert resp.status_code == 201, resp.text
    return group, resp.json()


async def test_summary_covers_every_split_not_just_the_recent_page(
    client, auth_headers, session, test_user, test_workspace, test_account, test_categories
):
    """Thirty debits of 10.00 across three months.

    A summary derived from the recent-transactions list would report 200.00
    over a single month, because that list stops at 20 rows.
    """
    group, member = await _group_with_member(client, auth_headers)

    for i in range(30):
        month = 1 + i // 10  # ten in each of January, February, March
        txn = Transaction(
            id=uuid.uuid4(),
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            account_id=test_account.id,
            category_id=test_categories[0].id,
            description=f"Shared {i}",
            amount=Decimal("10.00"),
            date=date(2026, month, 1 + i % 10),
            type="debit",
            source="manual",
        )
        session.add(txn)
        session.add(
            TransactionSplit(
                id=uuid.uuid4(),
                transaction_id=txn.id,
                group_member_id=uuid.UUID(member["id"]),
                workspace_id=test_workspace.id,
                share_amount=Decimal("5.00"),
                share_type="exact",
            )
        )
    await session.commit()

    resp = await client.get(f"/api/groups/{group['id']}/summary", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert Decimal(body["total_moved"]) == Decimal("300.00")
    assert [m["month"] for m in body["months"]] == ["2026-01", "2026-02", "2026-03"]
    assert all(Decimal(m["total"]) == Decimal("100.00") for m in body["months"])
    assert len(body["categories"]) == 1
    assert Decimal(body["categories"][0]["total"]) == Decimal("300.00")

    # The list it used to be derived from is still capped.
    resp = await client.get(f"/api/groups/{group['id']}/transactions", headers=auth_headers)
    assert len(resp.json()) == 20


async def test_summary_counts_debits_only_in_the_category_split(
    client, auth_headers, session, test_user, test_workspace, test_account, test_categories
):
    """Money coming back isn't spend: it lifts the total, not the breakdown."""
    group, member = await _group_with_member(client, auth_headers)

    for amount, kind in ((Decimal("40.00"), "debit"), (Decimal("10.00"), "credit")):
        txn = Transaction(
            id=uuid.uuid4(),
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            account_id=test_account.id,
            category_id=test_categories[0].id,
            description=kind,
            amount=amount,
            date=date(2026, 5, 4),
            type=kind,
            source="manual",
        )
        session.add(txn)
        session.add(
            TransactionSplit(
                id=uuid.uuid4(),
                transaction_id=txn.id,
                group_member_id=uuid.UUID(member["id"]),
                workspace_id=test_workspace.id,
                share_amount=amount / 2,
                share_type="exact",
            )
        )
    await session.commit()

    body = (await client.get(
        f"/api/groups/{group['id']}/summary", headers=auth_headers
    )).json()
    assert Decimal(body["total_moved"]) == Decimal("50.00")
    assert Decimal(body["categories"][0]["total"]) == Decimal("40.00")


async def test_summary_empty_group(client, auth_headers, test_user):
    resp = await client.post(
        "/api/groups",
        headers=auth_headers,
        json={"name": "Empty", "kind": "social", "default_currency": "EUR"},
    )
    group = resp.json()
    body = (await client.get(
        f"/api/groups/{group['id']}/summary", headers=auth_headers
    )).json()
    assert Decimal(body["total_moved"]) == Decimal("0")
    assert body["months"] == []
    assert body["categories"] == []
    assert body["default_currency"] == "EUR"


async def test_summary_unknown_group_is_404(client, auth_headers, test_user):
    resp = await client.get(f"/api/groups/{uuid.uuid4()}/summary", headers=auth_headers)
    assert resp.status_code == 404
