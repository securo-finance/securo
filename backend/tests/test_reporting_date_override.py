from copy import deepcopy
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.budget import Budget
from app.models.transaction import Transaction
from app.schemas.transaction import TransactionUpdate
from app.services import budget_service, dashboard_service
from app.services.transaction_service import get_transactions, update_transaction


def _month_before(value: date) -> date:
    return (value.replace(day=1) - timedelta(days=1)).replace(day=1)


async def _synced_transaction(
    session: AsyncSession,
    *,
    user_id,
    workspace_id,
    account_id,
    bank_date: date,
    reporting_date: date | None = None,
    category_id=None,
    amount: Decimal = Decimal("100.00"),
    transfer_pair_id=None,
    source: str = "sync",
) -> Transaction:
    transaction = Transaction(
        id=uuid.uuid4(),
        user_id=user_id,
        workspace_id=workspace_id,
        account_id=account_id,
        category_id=category_id,
        external_id=f"provider-{uuid.uuid4()}",
        description="Resgate - Global Account",
        amount=amount,
        amount_primary=amount,
        fx_rate_used=Decimal("1"),
        currency="BRL",
        date=bank_date,
        effective_date=bank_date,
        reporting_date_override=reporting_date,
        type="credit",
        source=source,
        status="posted",
        raw_data={"provider": "bank-truth"},
        transfer_pair_id=transfer_pair_id,
        created_at=datetime.now(timezone.utc),
    )
    session.add(transaction)
    await session.commit()
    await session.refresh(transaction)
    return transaction


@pytest.mark.asyncio
async def test_override_moves_synced_row_and_reset_preserves_bank_truth(
    session: AsyncSession,
    test_user,
    test_workspace,
    test_account,
):
    bank_date = date.today().replace(day=1)
    reporting_date = bank_date - timedelta(days=1)
    transaction = await _synced_transaction(
        session,
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=test_account.id,
        bank_date=bank_date,
    )
    immutable = {
        "date": transaction.date,
        "external_id": transaction.external_id,
        "amount": transaction.amount,
        "amount_primary": transaction.amount_primary,
        "fx_rate_used": transaction.fx_rate_used,
        "raw_data": deepcopy(transaction.raw_data),
        "status": transaction.status,
        "account_id": transaction.account_id,
    }

    with patch(
        "app.services.transaction_service.stamp_primary_amount",
        new_callable=AsyncMock,
    ) as stamp:
        updated = await update_transaction(
            session,
            transaction.id,
            test_workspace.id,
            test_user.id,
            TransactionUpdate(reporting_date_override=reporting_date),
        )
    stamp.assert_not_awaited()
    assert updated is not None
    assert updated.reporting_date_override == reporting_date
    assert {key: getattr(updated, key) for key in immutable} == immutable

    previous_rows, _, _ = await get_transactions(
        session,
        test_workspace.id,
        test_user.id,
        from_date=reporting_date.replace(day=1),
        to_date=reporting_date,
        accounting_mode="cash",
    )
    current_rows, _, _ = await get_transactions(
        session,
        test_workspace.id,
        test_user.id,
        from_date=bank_date,
        to_date=date.today(),
        accounting_mode="cash",
    )
    assert [row.id for row in previous_rows] == [transaction.id]
    assert transaction.id not in {row.id for row in current_rows}

    reset = await update_transaction(
        session,
        transaction.id,
        test_workspace.id,
        test_user.id,
        TransactionUpdate(reporting_date_override=None),
    )
    assert reset is not None
    assert reset.reporting_date_override is None
    assert reset.date == bank_date

    current_rows, _, _ = await get_transactions(
        session,
        test_workspace.id,
        test_user.id,
        from_date=bank_date,
        to_date=date.today(),
        accounting_mode="cash",
    )
    assert transaction.id in {row.id for row in current_rows}

    with pytest.raises(ValueError, match="Bank date cannot be changed"):
        await update_transaction(
            session,
            transaction.id,
            test_workspace.id,
            test_user.id,
            TransactionUpdate(date=bank_date - timedelta(days=2)),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source", "account_type"),
    [("manual", "checking"), ("sync", "credit_card")],
)
async def test_override_rejects_unsupported_transactions(
    session: AsyncSession,
    test_user,
    test_workspace,
    test_account,
    source,
    account_type,
):
    test_account.type = account_type
    await session.commit()
    transaction = await _synced_transaction(
        session,
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=test_account.id,
        bank_date=date.today(),
        source=source,
    )

    with pytest.raises(
        ValueError,
        match="only supported for synchronized non-credit-card transactions",
    ):
        await update_transaction(
            session,
            transaction.id,
            test_workspace.id,
            test_user.id,
            TransactionUpdate(reporting_date_override=date.today() - timedelta(days=1)),
        )

    await session.refresh(transaction)
    assert transaction.reporting_date_override is None


@pytest.mark.asyncio
async def test_override_rejects_move_to_credit_card(
    session: AsyncSession,
    test_user,
    test_workspace,
    test_account,
):
    bank_date = date.today()
    reporting_date = bank_date - timedelta(days=1)
    transaction = await _synced_transaction(
        session,
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=test_account.id,
        bank_date=bank_date,
        reporting_date=reporting_date,
    )
    card = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Unsupported reporting-date card",
        type="credit_card",
        balance=Decimal("0"),
        currency="BRL",
    )
    session.add(card)
    await session.commit()

    with pytest.raises(
        ValueError,
        match="only supported for synchronized non-credit-card transactions",
    ):
        await update_transaction(
            session,
            transaction.id,
            test_workspace.id,
            test_user.id,
            TransactionUpdate(account_id=card.id),
        )

    await session.refresh(transaction)
    assert transaction.account_id == test_account.id
    assert transaction.reporting_date_override == reporting_date


@pytest.mark.asyncio
async def test_override_does_not_cascade_to_transfer_counterpart(
    session: AsyncSession,
    test_user,
    test_workspace,
    test_account,
):
    pair_id = uuid.uuid4()
    bank_date = date.today().replace(day=1)
    reporting_date = bank_date - timedelta(days=1)
    anchor = await _synced_transaction(
        session,
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=test_account.id,
        bank_date=bank_date,
        transfer_pair_id=pair_id,
    )
    counterpart = await _synced_transaction(
        session,
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=test_account.id,
        bank_date=bank_date,
        transfer_pair_id=pair_id,
    )

    await update_transaction(
        session,
        anchor.id,
        test_workspace.id,
        test_user.id,
        TransactionUpdate(reporting_date_override=reporting_date),
    )
    await session.refresh(counterpart)

    assert anchor.reporting_date_override == reporting_date
    assert counterpart.reporting_date_override is None


@pytest.mark.asyncio
async def test_dashboard_and_budget_share_reporting_override(
    session: AsyncSession,
    test_user,
    test_workspace,
    test_account,
    test_categories,
):
    bank_date = date.today().replace(day=1)
    reporting_date = bank_date - timedelta(days=1)
    previous_month = _month_before(bank_date)
    category = test_categories[0]
    transaction = await _synced_transaction(
        session,
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=test_account.id,
        bank_date=bank_date,
        reporting_date=reporting_date,
        category_id=category.id,
    )
    transaction.type = "debit"
    session.add(
        Budget(
            id=uuid.uuid4(),
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            category_id=category.id,
            amount=Decimal("500.00"),
            amount_primary=Decimal("500.00"),
            currency="BRL",
            month=previous_month,
            is_recurring=False,
        )
    )
    await session.commit()

    previous_summary = await dashboard_service.get_summary(
        session,
        test_workspace.id,
        test_user.id,
        month=previous_month,
    )
    current_summary = await dashboard_service.get_summary(
        session,
        test_workspace.id,
        test_user.id,
        month=bank_date,
    )
    assert previous_summary.monthly_expenses == pytest.approx(100.0)
    assert current_summary.monthly_expenses == pytest.approx(0.0)

    spending = await dashboard_service.get_spending_by_category(
        session,
        test_workspace.id,
        test_user.id,
        month=previous_month,
    )
    assert {item.category_id: item.total for item in spending}[str(category.id)] == pytest.approx(
        100.0
    )

    budgets = await budget_service.get_budget_vs_actual(
        session,
        test_workspace.id,
        test_user.id,
        month=previous_month,
    )
    category_budget = next(item for item in budgets if item.category_id == category.id)
    assert float(category_budget.actual_amount) == pytest.approx(100.0)
