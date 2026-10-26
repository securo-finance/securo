import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.models.account import Account
from app.models.fx_rate import FxRate
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.models.workspace import Workspace
from app.services.cashflow_forecast_service import _date_matches_cadence, generate_cashflow_forecast
from app.schemas.forecast import CadenceEnum


def _opening_balance_tx(user_id, workspace_id, account_id, amount, tx_type, tx_date):
    """Mirror the opening_balance row account_service.create_account writes."""
    return Transaction(
        id=uuid.uuid4(),
        user_id=user_id,
        workspace_id=workspace_id,
        account_id=account_id,
        description="Opening balance",
        amount=Decimal(amount),
        amount_primary=Decimal(amount),
        currency="USD",
        date=tx_date,
        effective_date=tx_date,
        type=tx_type,
        source="opening_balance",
        status="posted",
    )


def test_date_matches_cadence():
    anchor = date(2026, 1, 1)
    assert _date_matches_cadence(date(2026, 1, 8), anchor, CadenceEnum.WEEKLY) is True
    assert _date_matches_cadence(date(2026, 1, 9), anchor, CadenceEnum.WEEKLY) is False
    assert _date_matches_cadence(date(2026, 1, 15), anchor, CadenceEnum.BI_WEEKLY) is True
    assert _date_matches_cadence(date(2026, 2, 1), anchor, CadenceEnum.MONTHLY) is True
    assert _date_matches_cadence(date(2026, 2, 2), anchor, CadenceEnum.MONTHLY) is False


def test_date_matches_calendar_cadence_at_month_end():
    assert _date_matches_cadence(
        date(2026, 4, 30), date(2026, 1, 31), CadenceEnum.QUARTERLY
    )
    assert _date_matches_cadence(
        date(2027, 2, 28), date(2024, 2, 29), CadenceEnum.ANNUAL
    )
    assert not _date_matches_cadence(
        date(2026, 4, 29), date(2026, 1, 31), CadenceEnum.QUARTERLY
    )


@pytest.mark.asyncio
async def test_cashflow_forecast_simulation(session):
    user_id = uuid.uuid4()
    ws = Workspace(id=uuid.uuid4(), name="Forecast Simulation WS", default_currency="USD")
    session.add(ws)
    await session.flush()

    # Checking account with $10,000 balance
    checking = Account(
        id=uuid.uuid4(),
        user_id=user_id,
        workspace_id=ws.id,
        name="Main Checking",
        type="checking",
        balance=Decimal("10000.00"),
        currency="USD",
    )
    # Credit card with $1,000 debt
    cc = Account(
        id=uuid.uuid4(),
        user_id=user_id,
        workspace_id=ws.id,
        name="Credit Card",
        type="credit_card",
        balance=Decimal("1000.00"),
        currency="USD",
    )
    session.add_all([checking, cc])
    await session.flush()
    session.add_all([
        _opening_balance_tx(user_id, ws.id, checking.id, "10000.00", "credit", date(2026, 5, 1)),
        _opening_balance_tx(user_id, ws.id, cc.id, "1000.00", "debit", date(2026, 5, 1)),
    ])

    ref_date = date(2026, 9, 1)

    # Add 3 historical recurring monthly rent payments ($2,000) on 1st of month
    for m in range(6, 9):
        tx_date = date(2026, m, 1)
        tx = Transaction(
            id=uuid.uuid4(),
            user_id=user_id,
            workspace_id=ws.id,
            account_id=checking.id,
            description="Apartment Rent",
            payee="Landlord LLC",
            amount=Decimal("2000.00"),
            amount_primary=Decimal("2000.00"),
            currency="USD",
            date=tx_date,
            effective_date=tx_date,
            type="debit",
            source="manual",
            status="posted",
        )
        session.add(tx)

    # Add 3 historical bi-weekly salaries ($3,000)
    for i in range(3):
        tx_date = date(2026, 8, 1) + timedelta(days=i * 14)
        tx = Transaction(
            id=uuid.uuid4(),
            user_id=user_id,
            workspace_id=ws.id,
            account_id=checking.id,
            description="Employer Direct Deposit",
            payee="Acme Corp",
            amount=Decimal("3000.00"),
            amount_primary=Decimal("3000.00"),
            currency="USD",
            date=tx_date,
            effective_date=tx_date,
            type="credit",
            source="manual",
            status="posted",
        )
        session.add(tx)

    await session.commit()

    # Generate 60-day forecast
    forecast = await generate_cashflow_forecast(
        session,
        ws.id,
        horizon_days=60,
        include_discretionary_burn=False,
        reference_date=ref_date,
    )


    assert forecast.workspace_id == ws.id
    assert forecast.summary.horizon_days == 60
    # Checking: $10,000 opening - 3 x $2,000 rent + 3 x $3,000 salary = $13,000
    # Net liquid = $13,000 checking - $1,000 CC = $12,000
    assert forecast.summary.starting_liquid_balance == Decimal("12000.00")
    assert len(forecast.daily_trajectory) == 61  # day 0 to day 60
    assert forecast.summary.total_projected_inflow > Decimal("0.00")
    assert forecast.summary.total_projected_outflow > Decimal("0.00")
    assert forecast.summary.has_shortfall_risk is False


@pytest.mark.asyncio
async def test_cashflow_forecast_skips_transactions_linked_to_manual_recurring(session):
    user_id = uuid.uuid4()
    ws = Workspace(id=uuid.uuid4(), name="Forecast Linked WS", default_currency="USD")
    session.add(ws)
    await session.flush()

    checking = Account(
        id=uuid.uuid4(),
        user_id=user_id,
        workspace_id=ws.id,
        name="Main Checking",
        type="checking",
        balance=Decimal("10000.00"),
        currency="USD",
    )
    session.add(checking)
    await session.flush()

    rent = RecurringTransaction(
        id=uuid.uuid4(),
        user_id=user_id,
        workspace_id=ws.id,
        account_id=checking.id,
        description="Apartment Rent",
        amount=Decimal("2000.00"),
        currency="USD",
        type="debit",
        frequency="monthly",
        start_date=date(2026, 6, 1),
        next_occurrence=date(2026, 10, 1),
        is_active=True,
    )
    session.add(rent)
    await session.flush()

    # Posted rent payments generated from the manual rule carry a payee that
    # differs from the rule's description.
    for m in range(6, 10):
        tx_date = date(2026, m, 1)
        session.add(
            Transaction(
                id=uuid.uuid4(),
                user_id=user_id,
                workspace_id=ws.id,
                account_id=checking.id,
                recurring_transaction_id=rent.id,
                description="Apartment Rent",
                payee="Landlord LLC",
                amount=Decimal("2000.00"),
                amount_primary=Decimal("2000.00"),
                currency="USD",
                date=tx_date,
                effective_date=tx_date,
                type="debit",
                source="recurring",
                status="posted",
            )
        )
    await session.commit()

    forecast = await generate_cashflow_forecast(
        session,
        ws.id,
        horizon_days=45,
        include_discretionary_burn=True,
        reference_date=date(2026, 9, 2),
    )

    rent_day = next(p for p in forecast.daily_trajectory if p.date == date(2026, 10, 1))
    assert rent_day.recurring_outflow == Decimal("2000.00")
    assert forecast.summary.total_projected_discretionary == Decimal("0.00")


@pytest.mark.asyncio
async def test_cashflow_forecast_starts_from_current_balance_of_manual_accounts(session):
    user_id = uuid.uuid4()
    ws = Workspace(id=uuid.uuid4(), name="Forecast Current Balance WS", default_currency="USD")
    session.add(ws)
    await session.flush()

    # Manual account: the stored balance column keeps the opening balance,
    # the current balance comes from the transactions.
    checking = Account(
        id=uuid.uuid4(),
        user_id=user_id,
        workspace_id=ws.id,
        name="Manual Checking",
        type="checking",
        balance=Decimal("1000.00"),
        currency="USD",
    )
    euro_savings = Account(
        id=uuid.uuid4(),
        user_id=user_id,
        workspace_id=ws.id,
        name="Euro Savings",
        type="savings",
        balance=Decimal("0.00"),
        currency="EUR",
    )
    session.add_all([checking, euro_savings])
    await session.flush()

    ref_date = date(2026, 9, 1)
    session.add(FxRate(base_currency="USD", quote_currency="EUR", date=ref_date,
                       rate=Decimal("0.5"), source="test"))
    session.add(_opening_balance_tx(user_id, ws.id, checking.id, "1000.00", "credit", date(2026, 8, 1)))
    for tx_date, amount, tx_type in [
        (date(2026, 8, 5), "250.00", "credit"),
        (date(2026, 8, 12), "400.00", "debit"),
        (date(2026, 8, 20), "75.50", "debit"),
    ]:
        session.add(
            Transaction(
                id=uuid.uuid4(),
                user_id=user_id,
                workspace_id=ws.id,
                account_id=checking.id,
                description="Groceries" if tx_type == "debit" else "Refund",
                amount=Decimal(amount),
                amount_primary=Decimal(amount),
                currency="USD",
                date=tx_date,
                effective_date=tx_date,
                type=tx_type,
                source="manual",
                status="posted",
            )
        )
    session.add(
        Transaction(
            id=uuid.uuid4(),
            user_id=user_id,
            workspace_id=ws.id,
            account_id=euro_savings.id,
            description="Transfer in",
            amount=Decimal("100.00"),
            currency="EUR",
            date=date(2026, 8, 10),
            effective_date=date(2026, 8, 10),
            type="credit",
            source="manual",
            status="posted",
        )
    )
    await session.commit()

    forecast = await generate_cashflow_forecast(
        session,
        ws.id,
        horizon_days=30,
        include_discretionary_burn=False,
        reference_date=ref_date,
    )

    # Checking: 1,000 opening + 250 - 400 - 75.50 = 774.50 (not the stored 1,000)
    # Savings: EUR 100 at 0.5 EUR per USD = USD 200
    assert forecast.summary.starting_liquid_balance == Decimal("974.50")
