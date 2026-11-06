import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.budget import Budget
from app.models.recurring_transaction import RecurringTransaction
from app.models.user import User
from app.models.workspace import WorkspaceMember
from app.schemas.budget import BudgetCopyMonthRequest
from app.services.budget_service import (
    copy_monthly_budgets,
    get_budget_rollover_summary,
    get_budgets,
    get_multi_month_forecast,
)


def _budget(user_id, workspace_id, category_id, amount, month, is_recurring=False):
    return Budget(
        user_id=user_id,
        workspace_id=workspace_id,
        category_id=category_id,
        amount=Decimal(amount),
        month=month,
        is_recurring=is_recurring,
    )


async def _effective_amount(session, workspace_id, category_id, month):
    budgets = await get_budgets(session, workspace_id, month)
    matches = [b for b in budgets if b.category_id == category_id]
    assert len(matches) == 1, matches
    return matches[0].amount


async def _rows(session, workspace_id, category_id):
    result = await session.execute(
        select(Budget)
        .where(Budget.workspace_id == workspace_id, Budget.category_id == category_id)
        .order_by(Budget.month, Budget.is_recurring)
    )
    return [(b.month, b.is_recurring, b.amount) for b in result.scalars().all()]


async def _second_member(session, workspace_id) -> User:
    member = User(
        id=uuid.uuid4(),
        email="member@example.com",
        hashed_password="x",
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    session.add(member)
    await session.flush()
    session.add(WorkspaceMember(
        id=uuid.uuid4(), workspace_id=workspace_id, user_id=member.id, role="editor",
    ))
    await session.commit()
    return member


def test_copy_request_rejects_adjustment_below_minus_100():
    with pytest.raises(ValueError):
        BudgetCopyMonthRequest(
            source_month=date(2026, 8, 1),
            target_month=date(2026, 9, 1),
            adjustment_percentage=Decimal("-150"),
        )


def test_copy_request_rejects_adjustment_above_1000():
    with pytest.raises(ValueError):
        BudgetCopyMonthRequest(
            source_month=date(2026, 8, 1),
            target_month=date(2026, 9, 1),
            adjustment_percentage=Decimal("1000.01"),
        )


@pytest.mark.asyncio
async def test_copy_rejects_adjusted_amount_beyond_column_range(
    session: AsyncSession, test_user, test_workspace, test_categories
):
    cat = test_categories[0].id
    session.add(_budget(test_user.id, test_workspace.id, cat, "9999999999999.00", date(2026, 8, 1)))
    await session.commit()

    with pytest.raises(ValueError):
        await copy_monthly_budgets(
            session, test_workspace.id, test_user.id,
            source_month=date(2026, 8, 1), target_month=date(2026, 9, 1),
            adjustment_percentage=Decimal("10"),
        )


@pytest.mark.asyncio
async def test_copy_rounds_half_cent_up(
    session: AsyncSession, test_user, test_workspace, test_categories
):
    cat = test_categories[0].id
    session.add(_budget(test_user.id, test_workspace.id, cat, "10.15", date(2026, 8, 1)))
    await session.commit()

    # 10.15 * 1.10 = 11.165: half-even would give 11.16.
    copied = await copy_monthly_budgets(
        session, test_workspace.id, test_user.id,
        source_month=date(2026, 8, 1), target_month=date(2026, 9, 1),
        adjustment_percentage=Decimal("10"),
    )

    assert [b.amount for b in copied] == [Decimal("11.17")]


@pytest.mark.asyncio
async def test_copy_with_overwrite_keeps_target_on_recurring_when_amount_is_unchanged(
    session: AsyncSession, test_user, test_workspace, test_categories
):
    cat = test_categories[0].id
    session.add_all([
        _budget(test_user.id, test_workspace.id, cat, "100.00", date(2026, 1, 1), is_recurring=True),
        _budget(test_user.id, test_workspace.id, cat, "100.00", date(2026, 2, 1)),
    ])
    await session.commit()

    copied = await copy_monthly_budgets(
        session, test_workspace.id, test_user.id,
        source_month=date(2026, 2, 1), target_month=date(2026, 3, 1),
        overwrite_existing=True,
    )

    # March already resolves to 100.00, so no override pins it.
    assert copied == []
    assert await _rows(session, test_workspace.id, cat) == [
        (date(2026, 1, 1), True, Decimal("100.00")),
        (date(2026, 2, 1), False, Decimal("100.00")),
    ]


@pytest.mark.asyncio
async def test_copy_rejects_same_source_and_target_month(
    session: AsyncSession, test_user, test_workspace, test_categories
):
    with pytest.raises(ValueError):
        await copy_monthly_budgets(
            session, test_workspace.id, test_user.id,
            source_month=date(2026, 8, 1), target_month=date(2026, 8, 15),
        )


@pytest.mark.asyncio
async def test_copy_uses_resolved_amount_when_category_has_recurring_and_override(
    session: AsyncSession, test_user, test_workspace, test_categories
):
    cat = test_categories[0].id
    session.add_all([
        _budget(test_user.id, test_workspace.id, cat, "10.00", date(2026, 8, 1), is_recurring=True),
        _budget(test_user.id, test_workspace.id, cat, "40.00", date(2026, 8, 1)),
        _budget(test_user.id, test_workspace.id, cat, "30.00", date(2026, 9, 1)),
    ])
    await session.commit()

    await copy_monthly_budgets(
        session, test_workspace.id, test_user.id,
        source_month=date(2026, 8, 1), target_month=date(2026, 9, 1),
        overwrite_existing=True,
    )

    # The target takes what August shows (the 40.00 override), and the 10.00
    # recurring default is still there for the months after September.
    assert await _rows(session, test_workspace.id, cat) == [
        (date(2026, 8, 1), False, Decimal("40.00")),
        (date(2026, 8, 1), True, Decimal("10.00")),
        (date(2026, 9, 1), False, Decimal("40.00")),
    ]
    assert await _effective_amount(session, test_workspace.id, cat, date(2026, 10, 1)) == Decimal("10.00")


@pytest.mark.asyncio
async def test_copy_adjusts_inherited_recurring_without_touching_later_months(
    session: AsyncSession, test_user, test_workspace, test_categories
):
    cat = test_categories[0].id
    session.add(_budget(test_user.id, test_workspace.id, cat, "100.00", date(2026, 1, 1), is_recurring=True))
    await session.commit()

    # February only inherits January's recurring default.
    await copy_monthly_budgets(
        session, test_workspace.id, test_user.id,
        source_month=date(2026, 2, 1), target_month=date(2026, 3, 1),
        adjustment_percentage=Decimal("10"), overwrite_existing=True,
    )

    assert await _effective_amount(session, test_workspace.id, cat, date(2026, 3, 1)) == Decimal("110.00")
    assert await _effective_amount(session, test_workspace.id, cat, date(2026, 4, 1)) == Decimal("100.00")
    assert await _rows(session, test_workspace.id, cat) == [
        (date(2026, 1, 1), True, Decimal("100.00")),
        (date(2026, 3, 1), False, Decimal("110.00")),
    ]


@pytest.mark.asyncio
async def test_copy_without_overwrite_skips_categories_already_budgeted_in_target(
    session: AsyncSession, test_user, test_workspace, test_categories
):
    cat_a, cat_b = test_categories[0].id, test_categories[1].id
    session.add_all([
        _budget(test_user.id, test_workspace.id, cat_a, "50.00", date(2026, 5, 1)),
        _budget(test_user.id, test_workspace.id, cat_b, "70.00", date(2026, 5, 1)),
        # cat_a is budgeted in June through a recurring default.
        _budget(test_user.id, test_workspace.id, cat_a, "20.00", date(2026, 6, 1), is_recurring=True),
    ])
    await session.commit()

    copied = await copy_monthly_budgets(
        session, test_workspace.id, test_user.id,
        source_month=date(2026, 5, 1), target_month=date(2026, 6, 1),
    )

    assert [(b.category_id, b.amount) for b in copied] == [(cat_b, Decimal("70.00"))]
    assert await _effective_amount(session, test_workspace.id, cat_a, date(2026, 6, 1)) == Decimal("20.00")
    assert await _effective_amount(session, test_workspace.id, cat_b, date(2026, 6, 1)) == Decimal("70.00")


@pytest.mark.asyncio
async def test_copy_updates_another_members_override_instead_of_adding_a_second_row(
    session: AsyncSession, test_user, test_workspace, test_categories
):
    cat = test_categories[0].id
    member = await _second_member(session, test_workspace.id)
    session.add_all([
        _budget(member.id, test_workspace.id, cat, "80.00", date(2026, 7, 1)),
        _budget(test_user.id, test_workspace.id, cat, "30.00", date(2026, 8, 1)),
    ])
    await session.commit()

    # Overwrite off: the owner's August budget stays as it is.
    assert await copy_monthly_budgets(
        session, test_workspace.id, member.id,
        source_month=date(2026, 7, 1), target_month=date(2026, 8, 1),
    ) == []
    assert await _effective_amount(session, test_workspace.id, cat, date(2026, 8, 1)) == Decimal("30.00")

    # Overwrite on: the existing August row is updated, no second row is added.
    await copy_monthly_budgets(
        session, test_workspace.id, member.id,
        source_month=date(2026, 7, 1), target_month=date(2026, 8, 1),
        overwrite_existing=True,
    )
    august = [r for r in await _rows(session, test_workspace.id, cat) if r[0] == date(2026, 8, 1)]
    assert august == [(date(2026, 8, 1), False, Decimal("80.00"))]


@pytest.mark.asyncio
async def test_rollover_and_forecast_use_projected_spend_for_future_months(
    session: AsyncSession, test_user, test_workspace, test_categories
):
    cat = test_categories[0].id
    session.add(_budget(test_user.id, test_workspace.id, cat, "500.00", date(2030, 1, 1), is_recurring=True))
    session.add(RecurringTransaction(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        category_id=cat,
        description="Gym",
        amount=Decimal("120.00"),
        currency="BRL",
        type="debit",
        frequency="monthly",
        start_date=date(2030, 1, 10),
        next_occurrence=date(2030, 1, 10),
        is_active=True,
    ))
    await session.commit()

    summary = await get_budget_rollover_summary(session, test_workspace.id, test_user.id, date(2030, 2, 1))
    gym = next(c for c in summary["categories"] if c["category_id"] == cat)
    assert gym["actual_spent"] == Decimal("0")
    assert gym["projected_spent"] == Decimal("120.00")
    assert gym["remaining"] == Decimal("380.00")
    assert summary["net_surplus"] == Decimal("380.00")

    forecast = await get_multi_month_forecast(
        session, test_workspace.id, test_user.id, start_month=date(2030, 1, 1), num_months=3,
    )
    assert [(i["month"], i["projected_spend"], i["projected_variance"]) for i in forecast["items"]] == [
        (date(2030, 1, 1), Decimal("120.00"), Decimal("380.00")),
        (date(2030, 2, 1), Decimal("120.00"), Decimal("380.00")),
        (date(2030, 3, 1), Decimal("120.00"), Decimal("380.00")),
    ]


@pytest.mark.asyncio
async def test_rollover_net_surplus_counts_only_budgeted_categories(
    session: AsyncSession, test_user, test_workspace, test_categories
):
    budgeted, unbudgeted = test_categories[0].id, test_categories[1].id
    session.add(_budget(test_user.id, test_workspace.id, budgeted, "500.00", date(2030, 1, 1), is_recurring=True))
    for cat, description, amount in ((budgeted, "Gym", "120.00"), (unbudgeted, "Rent", "900.00")):
        session.add(RecurringTransaction(
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            category_id=cat,
            description=description,
            amount=Decimal(amount),
            currency="BRL",
            type="debit",
            frequency="monthly",
            start_date=date(2030, 1, 10),
            next_occurrence=date(2030, 1, 10),
            is_active=True,
        ))
    await session.commit()

    summary = await get_budget_rollover_summary(session, test_workspace.id, test_user.id, date(2030, 1, 1))

    # The 900.00 in the unbudgeted category would turn this into a deficit.
    assert [c["category_id"] for c in summary["categories"]] == [budgeted]
    assert summary["total_projected_spent"] == Decimal("120.00")
    assert summary["net_surplus"] == Decimal("380.00")
