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


async def _sync(session, account, provider, workspace_id=None):
    with patch("app.services.connection_service.get_provider", return_value=provider), \
         patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock), \
         patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock), \
         patch("app.services.connection_service.apply_rules_to_transaction", new_callable=AsyncMock):
        await connection_service.sync_connection(
            session, account.connection_id, workspace_id or account.workspace_id, account.user_id
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


async def _bank_correction(session, transaction_id, amount, type_):
    """Run the provider's correction of a synced row the user never edited."""
    from unittest.mock import MagicMock

    from app.providers.base import BankProvider
    from app.services.connection_service import _refresh_provider_values

    row = await session.get(Transaction, transaction_id)
    assert row is not None
    row.source = "sync"
    row.raw_data = {"id": "bank-1"}
    await session.flush()
    provider = MagicMock(spec=BankProvider)
    provider.parse_raw_transaction.return_value = TransactionData(
        external_id="bank-1", description=row.description, amount=Decimal(row.amount),
        date=row.date, type=row.type, currency=row.currency,
    )
    await _refresh_provider_values(
        session, provider, row,
        TransactionData(
            external_id="bank-1", description=row.description, amount=Decimal(amount),
            date=row.date, type=type_, currency=row.currency,
        ),
        payee_source="auto", account_currency=row.currency, user_currency=row.currency,
    )
    await session.commit()
    session.expire_all()
    return await session.get(Transaction, transaction_id)


async def _assignments(session, transaction_id):
    rows = await session.execute(
        select(GoalAllocation.goal_id, GoalAllocation.amount)
        .where(GoalAllocation.transaction_id == transaction_id)
    )
    return dict(rows.all())


async def test_a_lowered_bank_amount_shrinks_its_assignments_pro_rata(
    client, auth_headers, session, test_account
):
    first = await create_pocket(client, auth_headers, test_account, name="Trip")
    second = await create_pocket(client, auth_headers, test_account, name="Car")
    deposit = await post_transaction(
        client, auth_headers, test_account, "Salary", "100", "credit",
        allocations=[(first["id"], "33.33"), (second["id"], "16.67")],
    )
    assert deposit.status_code == 201, deposit.text
    deposit_id = uuid.UUID(deposit.json()["id"])

    corrected = await _bank_correction(session, deposit_id, "40", "credit")

    assert corrected.amount == Decimal("40.00")
    assert await _assignments(session, deposit_id) == {
        uuid.UUID(first["id"]): Decimal("26.67"),
        uuid.UUID(second["id"]): Decimal("13.33"),
    }


async def test_a_lowered_bank_amount_sets_a_spent_pocket_to_zero(
    client, auth_headers, session, test_account
):
    goal_id, deposit_id = await _funded_deposit(client, auth_headers, test_account)

    corrected = await _bank_correction(session, deposit_id, "50", "credit")

    assert corrected.amount == Decimal("50.00")
    assert await _assignments(session, deposit_id) == {goal_id: Decimal("50.00")}
    assert await _pocket_current_amount(session, goal_id) == Decimal("0.00")
    adjustment = await session.scalar(
        select(GoalAllocation.amount).where(
            GoalAllocation.goal_id == goal_id, GoalAllocation.source == "adjustment"
        )
    )
    assert adjustment == Decimal("10.00")


async def test_a_flipped_bank_type_drops_its_assignments(
    client, auth_headers, session, test_account
):
    goal_id, deposit_id = await _funded_deposit(client, auth_headers, test_account)

    corrected = await _bank_correction(session, deposit_id, "100", "debit")

    assert corrected.type == "debit"
    assert await _assignments(session, deposit_id) == {}
    assert await _pocket_current_amount(session, goal_id) == Decimal("0.00")


async def test_a_raised_bank_amount_keeps_its_assignments(
    client, auth_headers, session, test_account
):
    goal_id, deposit_id = await _funded_deposit(client, auth_headers, test_account)

    await _bank_correction(session, deposit_id, "120", "credit")

    assert await _assignments(session, deposit_id) == {goal_id: Decimal("100.00")}
    assert await _pocket_current_amount(session, goal_id) == Decimal("40.00")


async def test_pockets_follow_an_account_the_sync_moves_to_its_connection_workspace(
    client, auth_headers, session, test_user, test_workspace, test_account
):
    from sqlalchemy import update

    from app.models.account import Account
    from app.models.goal import Goal
    from app.models.workspace import Workspace

    goal_id, _ = await _funded_deposit(client, auth_headers, test_account)
    home_id, account_id = test_workspace.id, test_account.id
    old_home = Workspace(
        id=uuid.uuid4(), name="Old Home", kind="personal",
        created_by_user_id=test_user.id, default_currency="BRL", locale="en-US",
    )
    session.add(old_home)
    await session.flush()
    # The account drifted away from its connection's workspace, pockets with it.
    await session.execute(
        update(Account).where(Account.id == account_id).values(workspace_id=old_home.id)
    )
    await session.execute(
        update(Goal).where(Goal.id == goal_id).values(workspace_id=old_home.id)
    )
    await session.execute(
        update(GoalAllocation)
        .where(GoalAllocation.goal_id == goal_id)
        .values(workspace_id=old_home.id)
    )
    await session.commit()
    session.expire_all()
    account = await session.get(Account, account_id)
    assert account is not None

    await _sync(session, account, _provider(account), home_id)

    session.expire_all()
    assert (await session.get(Goal, goal_id)).workspace_id == home_id
    workspaces = set(
        (
            await session.scalars(
                select(GoalAllocation.workspace_id).where(GoalAllocation.goal_id == goal_id)
            )
        ).all()
    )
    assert workspaces == {home_id}
