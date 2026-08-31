import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.models.account import Account
from app.models.transaction import Transaction
from app.models.workspace import Workspace
from app.services.cashflow_forecast_service import _date_matches_cadence, generate_cashflow_forecast
from app.schemas.forecast import CadenceEnum


def test_date_matches_cadence():
    anchor = date(2026, 1, 1)
    assert _date_matches_cadence(date(2026, 1, 8), anchor, CadenceEnum.WEEKLY) is True
    assert _date_matches_cadence(date(2026, 1, 9), anchor, CadenceEnum.WEEKLY) is False
    assert _date_matches_cadence(date(2026, 1, 15), anchor, CadenceEnum.BI_WEEKLY) is True
    assert _date_matches_cadence(date(2026, 2, 1), anchor, CadenceEnum.MONTHLY) is True
    assert _date_matches_cadence(date(2026, 2, 2), anchor, CadenceEnum.MONTHLY) is False


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
    # Net liquid = $10,000 checking - $1,000 CC = $9,000
    assert forecast.summary.starting_liquid_balance == Decimal("9000.00")
    assert len(forecast.daily_trajectory) == 61  # day 0 to day 60
    assert forecast.summary.total_projected_inflow > Decimal("0.00")
    assert forecast.summary.total_projected_outflow > Decimal("0.00")
    assert forecast.summary.has_shortfall_risk is False
