"""Deleting a group settlement never leaves a pocket's spending unfunded."""

import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.models.account import Account
from app.models.group_settlement import GroupSettlement
from app.models.transaction import Transaction
from app.schemas.group import GroupCreate, GroupMemberCreate
from app.schemas.group_settlement import GroupSettlementCreate
from app.services import group_service, settlement_service
from app.services.goal_service import _pocket_current_amount
from tests.test_goal_pockets import create_pocket, post_transaction

pytestmark = pytest.mark.asyncio


async def _received_settlement(session, user, workspace, test_account):
    group = await group_service.create_group(
        session, workspace.id, user.id, GroupCreate(name="Trip")
    )
    me = await group_service.create_member(
        session, group.id, workspace.id, GroupMemberCreate(name="Me", is_self=True)
    )
    friend = await group_service.create_member(
        session, group.id, workspace.id, GroupMemberCreate(name="Friend")
    )
    assert me is not None and friend is not None
    settlement = await settlement_service.create_settlement(
        session, group.id, workspace.id, user.id,
        GroupSettlementCreate(
            from_member_id=friend.id, to_member_id=me.id,
            amount=Decimal("100.00"), currency=test_account.currency, date=date.today(),
        ),
    )
    assert settlement is not None and settlement.receiver_transaction_id is not None
    credit = await session.get(Transaction, settlement.receiver_transaction_id)
    assert credit is not None
    account = await session.get(Account, credit.account_id)
    return group.id, settlement.id, credit.id, account


async def _fund_pocket(client, auth_headers, account, credit_id, spend: str | None):
    pocket = await create_pocket(client, auth_headers, account)
    assigned = await client.patch(
        f"/api/transactions/{credit_id}",
        headers=auth_headers,
        json={"goal_allocations": [{"goal_id": pocket["id"], "amount": "100"}]},
    )
    assert assigned.status_code == 200, assigned.text
    if spend is not None:
        spending = await post_transaction(
            client, auth_headers, account, "Dinner", spend, "debit",
            allocations=[(pocket["id"], spend)],
        )
        assert spending.status_code == 201, spending.text
    return uuid.UUID(pocket["id"])


async def test_a_settlement_whose_pocket_money_was_spent_cannot_be_deleted(
    client, auth_headers, session, test_user, test_workspace, test_account
):
    group_id, settlement_id, credit_id, account = await _received_settlement(
        session, test_user, test_workspace, test_account
    )
    goal_id = await _fund_pocket(client, auth_headers, account, credit_id, "60")

    response = await client.delete(
        f"/api/groups/{group_id}/settlements/{settlement_id}", headers=auth_headers
    )

    assert response.status_code == 400, response.text
    session.expire_all()
    assert await session.get(GroupSettlement, settlement_id) is not None
    assert await session.get(Transaction, credit_id) is not None
    assert await _pocket_current_amount(session, goal_id) == Decimal("40.00")


async def test_deleting_a_settlement_releases_its_unspent_pocket_money(
    client, auth_headers, session, test_user, test_workspace, test_account
):
    group_id, settlement_id, credit_id, account = await _received_settlement(
        session, test_user, test_workspace, test_account
    )
    goal_id = await _fund_pocket(client, auth_headers, account, credit_id, None)

    response = await client.delete(
        f"/api/groups/{group_id}/settlements/{settlement_id}", headers=auth_headers
    )

    assert response.status_code == 204, response.text
    session.expire_all()
    assert await session.get(Transaction, credit_id) is None
    assert await _pocket_current_amount(session, goal_id) == Decimal("0.00")
