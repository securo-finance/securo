from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.user import User
from app.models.workspace import Workspace
from app.schemas.debt import DebtCreate, DebtInstallmentPay, DebtPlanCreate
from app.services import debt_installment_service, debt_service


async def _make_debt_with_plan(session, workspace: Workspace, user: User, *, collection_mode: str):
    debt = await debt_service.create_debt(
        session,
        workspace.id,
        user.id,
        DebtCreate(
            kind="payroll_loan" if collection_mode == "payroll_deduction" else "loan",
            creditor_name="Banco Teste",
            original_principal=Decimal("1000.00"),
            current_balance=Decimal("1000.00"),
            currency="BRL",
            opened_date=date(2026, 1, 1),
        ),
    )
    plan = await debt_service.create_debt_plan(
        session,
        debt.id,
        workspace.id,
        DebtPlanCreate(
            kind="original_contract",
            collection_mode=collection_mode,
            interest_rate=Decimal("0"),
            installment_amount=Decimal("250.00"),
            num_installments=4,
            first_due_date=date(2026, 1, 10),
            frequency="monthly",
            activate=True,
        ),
    )
    return debt, plan


@pytest.mark.asyncio
async def test_mark_manual_installment_paid_decrements_balance(
    session: AsyncSession, test_user: User, test_workspace: Workspace
):
    debt, plan = await _make_debt_with_plan(session, test_workspace, test_user, collection_mode="manual")
    installment = plan.installments[0]

    paid = await debt_installment_service.mark_installment_paid(
        session,
        installment.id,
        test_workspace.id,
        DebtInstallmentPay(paid_date=date(2026, 1, 10)),
    )
    assert paid.status == "paid"
    assert paid.paid_amount == Decimal("250.00")
    assert paid.transaction_id is None

    refreshed = await debt_service.get_debt(session, debt.id, test_workspace.id)
    assert refreshed.current_balance == Decimal("750.00")


@pytest.mark.asyncio
async def test_mark_manual_installment_paid_with_linked_transaction(
    session: AsyncSession, test_user: User, test_workspace: Workspace, test_account: Account
):
    from app.models.transaction import Transaction
    import uuid
    from datetime import datetime, timezone

    debt, plan = await _make_debt_with_plan(session, test_workspace, test_user, collection_mode="manual")
    tx = Transaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=test_account.id,
        description="Pagamento parcela",
        amount=Decimal("250.00"),
        currency="BRL",
        date=date(2026, 1, 10),
        type="debit",
        source="manual",
        created_at=datetime.now(timezone.utc),
    )
    session.add(tx)
    await session.commit()

    installment = plan.installments[0]
    paid = await debt_installment_service.mark_installment_paid(
        session,
        installment.id,
        test_workspace.id,
        DebtInstallmentPay(paid_date=date(2026, 1, 10), transaction_id=tx.id),
    )
    assert paid.transaction_id == tx.id


@pytest.mark.asyncio
async def test_payroll_installment_rejects_transaction_link(
    session: AsyncSession, test_user: User, test_workspace: Workspace, test_account: Account
):
    import uuid

    debt, plan = await _make_debt_with_plan(
        session, test_workspace, test_user, collection_mode="payroll_deduction"
    )
    installment = plan.installments[0]
    with pytest.raises(ValueError, match="cannot be linked"):
        await debt_installment_service.mark_installment_paid(
            session,
            installment.id,
            test_workspace.id,
            DebtInstallmentPay(paid_date=date(2026, 1, 10), transaction_id=uuid.uuid4()),
        )


@pytest.mark.asyncio
async def test_auto_settle_payroll_installments_only_marks_due_ones(
    session: AsyncSession, test_user: User, test_workspace: Workspace
):
    debt, plan = await _make_debt_with_plan(
        session, test_workspace, test_user, collection_mode="payroll_deduction"
    )
    settled = await debt_installment_service.auto_settle_payroll_installments(session, date(2026, 1, 15))
    assert settled == 1  # only the Jan 10 installment is due

    refreshed = await debt_service.get_debt(session, debt.id, test_workspace.id)
    assert refreshed.current_balance == Decimal("750.00")

    await session.refresh(plan.installments[0])
    await session.refresh(plan.installments[1])
    assert plan.installments[0].status == "paid"
    assert plan.installments[0].transaction_id is None
    assert plan.installments[1].status == "pending"


@pytest.mark.asyncio
async def test_paying_last_installment_marks_debt_paid_off(
    session: AsyncSession, test_user: User, test_workspace: Workspace
):
    debt = await debt_service.create_debt(
        session,
        test_workspace.id,
        test_user.id,
        DebtCreate(
            kind="loan",
            creditor_name="Banco Teste",
            original_principal=Decimal("250.00"),
            current_balance=Decimal("250.00"),
            currency="BRL",
            opened_date=date(2026, 1, 1),
        ),
    )
    plan = await debt_service.create_debt_plan(
        session,
        debt.id,
        test_workspace.id,
        DebtPlanCreate(
            kind="original_contract",
            collection_mode="manual",
            interest_rate=Decimal("0"),
            installment_amount=Decimal("250.00"),
            num_installments=1,
            first_due_date=date(2026, 2, 1),
            frequency="monthly",
            activate=True,
        ),
    )
    await debt_installment_service.mark_installment_paid(
        session,
        plan.installments[0].id,
        test_workspace.id,
        DebtInstallmentPay(paid_date=date(2026, 2, 1)),
    )
    refreshed = await debt_service.get_debt(session, debt.id, test_workspace.id)
    assert refreshed.current_balance == Decimal("0.00")
    assert refreshed.status == "paid_off"
