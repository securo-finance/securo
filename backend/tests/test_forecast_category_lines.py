"""Forecast totals follow reportable category parts, as reports do (#425)."""
import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.models.account import Account
from app.models.category import Category
from app.models.fx_rate import FxRate
from app.models.transaction import Transaction
from app.models.transaction_category_line import TransactionCategoryLine
from app.models.workspace import Workspace
from app.services.cashflow_forecast_service import generate_cashflow_forecast
from app.services.recurring_detector_service import detect_recurring_patterns


async def _scene(session, user_id, excluded_flag, currency):
    workspace = Workspace(id=uuid.uuid4(), name="Category forecast", default_currency="USD")
    session.add(workspace)
    await session.flush()
    account = Account(id=uuid.uuid4(), user_id=user_id, workspace_id=workspace.id,
                      name="Checking", type="checking", currency=currency, balance=Decimal("50000"))
    ordinary = Category(id=uuid.uuid4(), user_id=user_id, workspace_id=workspace.id, name="Food")
    other = Category(id=uuid.uuid4(), user_id=user_id, workspace_id=workspace.id, name="Other",
                     **({excluded_flag: True} if excluded_flag != "none" else {}))
    session.add_all([account, ordinary, other])
    if currency != "USD":
        session.add(FxRate(base_currency="USD", quote_currency=currency, date=date(2026, 5, 1),
                           rate=Decimal("0.5"), source="test"))
    await session.flush()
    return workspace, account, ordinary, other


async def _charge(session, user_id, workspace, account, ordinary, other, day, amount):
    tx = Transaction(id=uuid.uuid4(), user_id=user_id, workspace_id=workspace.id,
                     account_id=account.id, description="Monthly Gym", payee="Monthly Gym",
                     amount=amount, amount_primary=None, currency=account.currency,
                     date=day, effective_date=day, type="debit", source="manual", status="posted",
                     has_category_lines=True)
    session.add(tx)
    await session.flush()
    for position, category, share in [(0, ordinary, Decimal("0.4")), (1, other, Decimal("0.6"))]:
        session.add(TransactionCategoryLine(id=uuid.uuid4(), workspace_id=workspace.id,
                    transaction_id=tx.id, category_id=category.id, amount=amount * share,
                    position=position))


@pytest.mark.asyncio
@pytest.mark.parametrize("excluded_flag", ["none", "treat_as_transfer", "is_ignored"])
@pytest.mark.parametrize("currency", ["USD", "DKK"])
async def test_detector_counts_reportable_parts_once(session, test_user, excluded_flag, currency):
    workspace, account, ordinary, other = await _scene(session, test_user.id, excluded_flag, currency)
    for month in (5, 6, 7, 8):
        await _charge(session, test_user.id, workspace, account, ordinary, other,
                      date(2026, month, 20), Decimal("100"))
    await session.commit()
    result = await detect_recurring_patterns(session, workspace.id, reference_date=date(2026, 9, 1))
    assert len(result.items) == 1
    item = result.items[0]
    expected = Decimal("100") if excluded_flag == "none" else Decimal("40")
    if currency == "DKK":
        expected *= 2
    assert item.average_amount == expected
    assert item.occurrence_count == 4
    assert len(set(item.sample_transaction_ids)) == 4
    assert item.currency == "USD"


@pytest.mark.asyncio
@pytest.mark.parametrize("excluded_flag", ["none", "treat_as_transfer", "is_ignored"])
@pytest.mark.parametrize("currency", ["USD", "DKK"])
async def test_burn_counts_reportable_parts_once(session, test_user, excluded_flag, currency):
    workspace, account, ordinary, other = await _scene(session, test_user.id, excluded_flag, currency)
    await _charge(session, test_user.id, workspace, account, ordinary, other,
                  date(2026, 8, 20), Decimal("9000"))
    await session.commit()
    result = await generate_cashflow_forecast(session, workspace.id, horizon_days=7,
                                             reference_date=date(2026, 9, 1))
    expected_daily = Decimal("100") if excluded_flag == "none" else Decimal("40")
    if currency == "DKK":
        expected_daily *= 2
    assert result.summary.total_projected_discretionary == expected_daily * 7
