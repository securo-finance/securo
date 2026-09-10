import uuid
from datetime import date
from decimal import Decimal
from app.models.budget import Budget
from app.schemas.budget import BudgetCopyMonthRequest, BudgetRolloverCategory


def test_budget_copy_request_schema_defaults():
    req = BudgetCopyMonthRequest(
        source_month=date(2026, 8, 1),
        target_month=date(2026, 9, 1),
    )
    assert req.adjustment_percentage == Decimal("0.0")
    assert req.overwrite_existing is False


def test_budget_model_active_helper():
    b = Budget(
        workspace_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        category_id=uuid.uuid4(),
        amount=Decimal("500.00"),
        month=date(2026, 1, 1),
        is_recurring=True,
    )
    assert b.is_active_for_month(date(2026, 9, 1)) is True
    assert b.is_active_for_month(date(2025, 12, 1)) is False


def test_budget_rollover_item_computation():
    item = BudgetRolloverCategory(
        category_id=uuid.uuid4(),
        category_name="Groceries",
        budgeted=Decimal("500.00"),
        actual_spent=Decimal("350.00"),
        remaining=Decimal("150.00"),
        carryover_eligible=True,
    )
    assert item.remaining == Decimal("150.00")
    assert item.carryover_eligible is True
