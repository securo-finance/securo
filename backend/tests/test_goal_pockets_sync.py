"""Bank sync never leaves a pocket assignment the synced row no longer fits."""

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.models.goal import GoalAllocation
from app.models.transaction import Transaction
from app.providers.base import AccountData, TransactionData
from app.services import connection_service, recurring_match_service
from app.services.goal_service import _pocket_current_amount
from tests.test_goal_pockets import create_pocket, post_transaction

pytestmark = pytest.mark.asyncio


def _provider(account, *transactions):
    provider = AsyncMock()
    provider.rereads_by_date = False
    provider.refresh_credentials = AsyncMock(return_value={"token": "t"})
    provider.get_institution_logo = AsyncMock(return_value=None)
    provider.get_accounts = AsyncMock(return_value=[
        AccountData(
            external_id=account.external_id, name=account.name, type=account.type,
            balance=Decimal("1500"), currency=account.currency,
        ),
    ])
    provider.get_bills = AsyncMock(return_value=[])
    provider.get_holdings = AsyncMock(return_value=[])
    provider.get_transactions = AsyncMock(return_value=list(transactions))
    provider.get_transactions_by_date = AsyncMock(return_value=[])
    return provider


async def _sync(session, account, provider):
    with patch("app.services.connection_service.get_provider", return_value=provider), \
         patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock), \
         patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock), \
         patch("app.services.connection_service.apply_rules_to_transaction", new_callable=AsyncMock):
        await connection_service.sync_connection(
            session, account.connection_id, account.workspace_id, account.user_id
        )


async def _funded_deposit(client, auth_headers, account):
    """A +100 deposit in a pocket, 60 of which a later purchase spent."""
    pocket = await create_pocket(client, auth_headers, account)
    deposit = await post_transaction(
        client, auth_headers, account, "Salary", "100", "credit",
        allocations=[(pocket["id"], "100")],
    )
    assert deposit.status_code == 201, deposit.text
    spending = await post_transaction(
        client, auth_headers, account, "Laptop", "60", "debit",
        allocations=[(pocket["id"], "60")],
    )
    assert spending.status_code == 201, spending.text
    return uuid.UUID(pocket["id"]), uuid.UUID(deposit.json()["id"])


async def test_a_placeholder_whose_pocket_money_was_spent_is_kept_and_the_charge_added(
    client, auth_headers, session, test_account, monkeypatch
):
    goal_id, deposit_id = await _funded_deposit(client, auth_headers, test_account)
    placeholder = await session.get(Transaction, deposit_id)
    assert placeholder is not None
    placeholder.source = "recurring"
    await session.commit()
    monkeypatch.setattr(
        recurring_match_service,
        "find_placeholder_for_incoming",
        AsyncMock(return_value=placeholder),
    )
    # Matched under the tolerance, but 80 cannot carry the 100 assigned.
    charge = TransactionData(
        external_id="salary-1", description="SALARY", amount=Decimal("80"),
        date=date.today(), type="credit", currency=test_account.currency, status="posted",
    )

    await _sync(session, test_account, _provider(test_account, charge))

    session.expire_all()
    kept = await session.get(Transaction, deposit_id)
    assert kept is not None
    assert (kept.amount, kept.source, kept.external_id) == (Decimal("100.00"), "recurring", None)
    added = await session.scalar(select(Transaction).where(Transaction.external_id == "salary-1"))
    assert added is not None and added.amount == Decimal("80.00")
    assert added.recurring_transaction_id is None
    assert await _pocket_current_amount(session, goal_id) == Decimal("40.00")
