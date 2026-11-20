"""Backups carry every table that holds money, and refuse to run when one is missing.

A backup that silently drops category splits or mortgage breakdowns restores a
workspace whose totals no longer match the original, so these tests compare the
money before and after a round trip instead of only counting rows.
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.account_card import AccountCard
from app.models.account_reserved_balance import AccountReservedBalance
from app.models.asset import Asset
from app.models.category import Category
from app.models.expected_income import ExpectedIncome
from app.models.mortgage_payment_allocation import (
    MORTGAGE_PRINCIPAL_SOURCE,
    MortgagePaymentAllocation,
)
from app.models.recurring_transaction import RecurringTransaction
from app.models.recurring_transaction_category_line import RecurringTransactionCategoryLine
from app.models.transaction import Transaction
from app.models.transaction_category_line import TransactionCategoryLine
from app.models.user import User
from app.models.workspace import Workspace
from app.services import backup_service

pytestmark = pytest.mark.asyncio


async def _seed_money(
    client: AsyncClient,
    auth_headers,
    session: AsyncSession,
    user: User,
    workspace: Workspace,
    categories: list[Category],
) -> None:
    """A split purchase, a split recurring bill, a mortgage payment and a reserved balance."""
    food, transport, _ = categories
    home = Asset(
        id=uuid.uuid4(),
        user_id=user.id,
        workspace_id=workspace.id,
        name="Home",
        type="real_estate",
        currency="USD",
        valuation_method="manual",
    )
    session.add(home)
    await session.flush()
    checking = Account(
        id=uuid.uuid4(),
        user_id=user.id,
        workspace_id=workspace.id,
        name="Checking",
        type="checking",
        balance=Decimal("0"),
        currency="USD",
    )
    loan = Account(
        id=uuid.uuid4(),
        user_id=user.id,
        workspace_id=workspace.id,
        name="Mortgage",
        type="loan",
        balance=Decimal("48802.47"),
        currency="USD",
        secured_asset_id=home.id,
    )
    session.add_all([checking, loan])
    await session.flush()
    opening = Transaction(
        id=uuid.uuid4(),
        user_id=user.id,
        workspace_id=workspace.id,
        account_id=loan.id,
        description="Opening balance",
        amount=Decimal("48802.47"),
        currency="USD",
        date=date(2026, 1, 1),
        effective_date=date(2026, 1, 1),
        type="debit",
        source="opening_balance",
        status="posted",
    )
    payment = Transaction(
        id=uuid.uuid4(),
        user_id=user.id,
        workspace_id=workspace.id,
        account_id=checking.id,
        description="Mortgage payment",
        amount=Decimal("232.83"),
        currency="USD",
        date=date(2026, 6, 2),
        effective_date=date(2026, 6, 2),
        type="debit",
        source="manual",
        status="posted",
    )
    recurring = RecurringTransaction(
        user_id=user.id,
        workspace_id=workspace.id,
        account_id=checking.id,
        description="Groceries and bus pass",
        amount=Decimal("90.00"),
        currency="USD",
        type="debit",
        frequency="monthly",
        start_date=date(2026, 1, 1),
        next_occurrence=date(2026, 12, 1),
        has_category_lines=True,
    )
    reserved = AccountReservedBalance(
        workspace_id=workspace.id,
        account_id=checking.id,
        external_id="cofrinho-1",
        name="Emergency fund",
        currency="USD",
        amount=Decimal("1250.40"),
    )
    card = AccountCard(
        workspace_id=workspace.id,
        account_id=checking.id,
        masked_number="4242",
        label="Additional card",
    )
    session.add_all([opening, payment, recurring, reserved, card])
    await session.flush()
    session.add_all(
        [
            RecurringTransactionCategoryLine(
                workspace_id=workspace.id,
                recurring_transaction_id=recurring.id,
                category_id=food.id,
                amount=Decimal("60.00"),
                position=0,
            ),
            RecurringTransactionCategoryLine(
                workspace_id=workspace.id,
                recurring_transaction_id=recurring.id,
                category_id=transport.id,
                amount=Decimal("30.00"),
                position=1,
            ),
        ]
    )
    await session.commit()

    split = await client.post(
        "/api/transactions",
        headers=auth_headers,
        json={
            "account_id": str(checking.id),
            "description": "Warehouse",
            "amount": 150.01,
            "date": "2026-10-10",
            "type": "debit",
            "currency": "USD",
            "category_lines": {
                "share_type": "exact",
                "lines": [
                    {"category_id": str(food.id), "amount": 100.01},
                    {"category_id": str(transport.id), "amount": 50},
                ],
            },
        },
    )
    assert split.status_code == 201, split.text

    breakdown = await client.put(
        f"/api/mortgage-payments/{payment.id}",
        headers=auth_headers,
        json={
            "allocations": [
                {
                    "loan_account_id": str(loan.id),
                    "principal_amount": 167.76,
                    "interest_amount": 65.07,
                }
            ]
        },
    )
    assert breakdown.status_code == 200, breakdown.text


async def _money(session: AsyncSession, workspace_id: uuid.UUID) -> dict:
    """Everything that adds up to money in a workspace, keyed by names, not ids."""
    session.expire_all()
    accounts = {
        a.id: a.name
        for a in (
            await session.execute(select(Account).where(Account.workspace_id == workspace_id))
        ).scalars()
    }
    categories = {
        c.id: c.name
        for c in (
            await session.execute(select(Category).where(Category.workspace_id == workspace_id))
        ).scalars()
    }
    transactions = {
        t.id: t
        for t in (
            await session.execute(
                select(Transaction).where(Transaction.workspace_id == workspace_id)
            )
        ).scalars()
    }
    balances: dict[str, Decimal] = {}
    for t in transactions.values():
        signed = t.amount if t.type == "credit" else -t.amount
        name = accounts[t.account_id]
        balances[name] = balances.get(name, Decimal("0")) + signed

    lines = sorted(
        (transactions[line.transaction_id].description, categories[line.category_id], line.amount)
        for line in (
            await session.execute(
                select(TransactionCategoryLine).where(
                    TransactionCategoryLine.workspace_id == workspace_id
                )
            )
        ).scalars()
    )
    recurring = {
        r.id: r.description
        for r in (
            await session.execute(
                select(RecurringTransaction).where(
                    RecurringTransaction.workspace_id == workspace_id
                )
            )
        ).scalars()
    }
    recurring_lines = sorted(
        (recurring[line.recurring_transaction_id], categories[line.category_id], line.amount)
        for line in (
            await session.execute(
                select(RecurringTransactionCategoryLine).where(
                    RecurringTransactionCategoryLine.workspace_id == workspace_id
                )
            )
        ).scalars()
    )
    allocations = []
    for row in (
        await session.execute(
            select(MortgagePaymentAllocation).where(
                MortgagePaymentAllocation.workspace_id == workspace_id
            )
        )
    ).scalars():
        principal_entry = transactions.get(row.loan_transaction_id)
        allocations.append(
            (
                transactions[row.payment_transaction_id].description,
                accounts[row.loan_account_id],
                row.principal_amount,
                row.interest_amount,
                principal_entry.amount if principal_entry else None,
                principal_entry.source if principal_entry else None,
            )
        )
    reserved = sorted(
        (accounts[row.account_id], row.external_id, row.name, row.currency, row.amount)
        for row in (
            await session.execute(
                select(AccountReservedBalance).where(
                    AccountReservedBalance.workspace_id == workspace_id
                )
            )
        ).scalars()
    )
    cards = sorted(
        (accounts[row.account_id], row.masked_number, row.label)
        for row in (
            await session.execute(
                select(AccountCard).where(AccountCard.workspace_id == workspace_id)
            )
        ).scalars()
    )
    return {
        "balances": balances,
        "reserved": reserved,
        "cards": cards,
        "lines": lines,
        "recurring_lines": recurring_lines,
        "allocations": sorted(allocations),
    }


async def _expected_income_rows(session: AsyncSession, workspace_id: uuid.UUID) -> list[tuple]:
    """The month, figure and currency of every income row of the workspace, sorted by month."""
    rows = (
        await session.execute(
            select(ExpectedIncome).where(ExpectedIncome.workspace_id == workspace_id)
        )
    ).scalars().all()
    return sorted((row.month, row.amount, row.currency) for row in rows)


async def test_every_workspace_table_is_backed_up_or_excluded():
    assert backup_service.uncovered_workspace_tables() == set()


async def test_export_refuses_a_partial_archive(client: AsyncClient, auth_headers, monkeypatch):
    excluded = dict(backup_service.EXCLUDED_WORKSPACE_TABLES)
    excluded.pop("ui_layouts")
    monkeypatch.setattr(backup_service, "EXCLUDED_WORKSPACE_TABLES", excluded)

    plain = await client.get("/api/export/backup", headers=auth_headers)
    protected = await client.post("/api/export/backup", json={}, headers=auth_headers)

    for response in (plain, protected):
        assert response.status_code == 500
        assert "ui_layouts" in response.json()["detail"]


async def test_round_trip_into_a_new_workspace_keeps_every_amount(
    client: AsyncClient,
    auth_headers,
    session: AsyncSession,
    test_user: User,
    test_workspace: Workspace,
    test_categories,
):
    await _seed_money(client, auth_headers, session, test_user, test_workspace, test_categories)
    ws_id = test_workspace.id
    before = await _money(session, ws_id)
    await session.refresh(test_workspace)
    assert len(before["lines"]) == 2
    assert len(before["recurring_lines"]) == 2
    assert before["allocations"][0][4] == Decimal("167.76")
    assert before["allocations"][0][5] == MORTGAGE_PRINCIPAL_SOURCE
    assert before["cards"] == [("Checking", "4242", "Additional card")]
    assert before["reserved"] == [("Checking", "cofrinho-1", "Emergency fund", "USD", Decimal("1250.40"))]

    archive = await client.get("/api/export/backup", headers=auth_headers)
    assert archive.status_code == 200
    restored = await client.post(
        "/api/backups/restore-upload",
        files={"file": ("backup.zip", archive.content, "application/zip")},
        data={"content": "both", "mode": "new_workspace"},
        headers=auth_headers,
    )
    assert restored.status_code == 200, restored.text
    body = restored.json()
    assert body["restored_counts"]["transaction_category_lines"] == 2
    assert body["restored_counts"]["recurring_transaction_category_lines"] == 2
    assert body["restored_counts"]["mortgage_payment_allocations"] == 1

    after = await _money(session, uuid.UUID(body["workspace_id"]))
    assert after == before


async def test_round_trip_into_the_current_workspace_keeps_every_amount(
    client: AsyncClient,
    auth_headers,
    session: AsyncSession,
    test_user: User,
    test_workspace: Workspace,
    test_categories,
):
    await _seed_money(client, auth_headers, session, test_user, test_workspace, test_categories)
    ws_id = test_workspace.id
    user_id = test_user.id
    before = await _money(session, ws_id)
    await session.refresh(test_workspace)

    data = await backup_service.build_backup_zip(session, test_workspace)
    await backup_service.restore_backup_bytes(
        session,
        data=data,
        target_user_id=user_id,
        current_workspace=test_workspace,
        content=backup_service.BackupContent.both,
        mode=backup_service.BackupRestoreMode.current_workspace,
        confirmation="RESTORE",
    )

    assert await _money(session, ws_id) == before


async def test_expected_income_survives_a_round_trip_into_a_new_workspace(
    client: AsyncClient,
    auth_headers,
    session: AsyncSession,
    test_workspace: Workspace,
):
    session.add_all(
        [
            ExpectedIncome(
                workspace_id=test_workspace.id,
                month=date(2026, 4, 1),
                amount=Decimal("3450.00"),
                currency="USD",
            ),
            ExpectedIncome(
                workspace_id=test_workspace.id,
                month=date(2026, 5, 1),
                amount=Decimal("2210.55"),
                currency="EUR",
            ),
        ]
    )
    await session.flush()
    before = await _expected_income_rows(session, test_workspace.id)
    assert before == [
        (date(2026, 4, 1), Decimal("3450.00"), "USD"),
        (date(2026, 5, 1), Decimal("2210.55"), "EUR"),
    ]

    archive = await client.get("/api/export/backup", headers=auth_headers)
    assert archive.status_code == 200
    restored = await client.post(
        "/api/backups/restore-upload",
        files={"file": ("backup.zip", archive.content, "application/zip")},
        data={"content": "both", "mode": "new_workspace"},
        headers=auth_headers,
    )
    assert restored.status_code == 200, restored.text
    body = restored.json()
    assert body["restored_counts"]["expected_incomes"] == 2

    after = await _expected_income_rows(session, uuid.UUID(body["workspace_id"]))
    assert after == before


async def test_expected_income_survives_a_round_trip_into_the_current_workspace(
    client: AsyncClient,
    auth_headers,
    session: AsyncSession,
    test_user: User,
    test_workspace: Workspace,
):
    session.add(
        ExpectedIncome(
            workspace_id=test_workspace.id,
            month=date(2026, 4, 1),
            amount=Decimal("3450.00"),
            currency="USD",
        )
    )
    await session.flush()
    before = await _expected_income_rows(session, test_workspace.id)
    await session.refresh(test_workspace)

    data = await backup_service.build_backup_zip(session, test_workspace)
    await backup_service.restore_backup_bytes(
        session,
        data=data,
        target_user_id=test_user.id,
        current_workspace=test_workspace,
        content=backup_service.BackupContent.both,
        mode=backup_service.BackupRestoreMode.current_workspace,
        confirmation="RESTORE",
    )

    assert await _expected_income_rows(session, test_workspace.id) == before


async def test_configuration_restore_keeps_the_splits_of_existing_transactions(
    client: AsyncClient,
    auth_headers,
    session: AsyncSession,
    test_user: User,
    test_workspace: Workspace,
    test_categories,
):
    await _seed_money(client, auth_headers, session, test_user, test_workspace, test_categories)
    ws_id = test_workspace.id
    user_id = test_user.id
    before = await _money(session, ws_id)
    await session.refresh(test_workspace)

    data = await backup_service.build_backup_zip(
        session, test_workspace, content=backup_service.BackupContent.configuration
    )
    await backup_service.restore_backup_bytes(
        session,
        data=data,
        target_user_id=user_id,
        current_workspace=test_workspace,
        content=backup_service.BackupContent.configuration,
        mode=backup_service.BackupRestoreMode.current_workspace,
        confirmation="RESTORE",
    )

    assert await _money(session, ws_id) == before


async def test_configuration_restore_refuses_to_orphan_a_split(
    client: AsyncClient,
    auth_headers,
    session: AsyncSession,
    test_user: User,
    test_workspace: Workspace,
    test_categories,
):
    await _seed_money(client, auth_headers, session, test_user, test_workspace, test_categories)
    ws_id = test_workspace.id
    user_id = test_user.id
    data = await backup_service.build_backup_zip(
        session, test_workspace, content=backup_service.BackupContent.configuration
    )

    # A category created after the backup, used by a split the backup cannot know.
    food_id = test_categories[0].id
    newer = Category(user_id=user_id, workspace_id=ws_id, name="Newer")
    session.add(newer)
    await session.commit()
    checking_id = await session.scalar(
        select(Account.id).where(Account.workspace_id == ws_id, Account.name == "Checking")
    )
    split = await client.post(
        "/api/transactions",
        headers=auth_headers,
        json={
            "account_id": str(checking_id),
            "description": "Later split",
            "amount": 20,
            "date": "2026-10-12",
            "type": "debit",
            "currency": "USD",
            "category_lines": {
                "share_type": "exact",
                "lines": [
                    {"category_id": str(food_id), "amount": 10},
                    {"category_id": str(newer.id), "amount": 10},
                ],
            },
        },
    )
    assert split.status_code == 201, split.text
    before = await _money(session, ws_id)
    await session.refresh(test_workspace)

    with pytest.raises(Exception) as refused:
        await backup_service.restore_backup_bytes(
            session,
            data=data,
            target_user_id=user_id,
            current_workspace=test_workspace,
            content=backup_service.BackupContent.configuration,
            mode=backup_service.BackupRestoreMode.current_workspace,
            confirmation="RESTORE",
        )
    assert "split uses a category" in str(getattr(refused.value, "detail", refused.value))
    assert await _money(session, ws_id) == before
