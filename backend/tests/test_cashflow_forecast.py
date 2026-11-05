import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.models.account import Account
from app.models.category import Category
from app.models.fx_rate import FxRate
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.models.workspace import Workspace
from app.services.cashflow_forecast_service import _date_matches_cadence, generate_cashflow_forecast
from app.services.recurring_detector_service import detect_recurring_patterns
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
    # Runway starts from cash alone; the $1,000 card debt is reported beside it.
    assert forecast.summary.starting_liquid_balance == Decimal("13000.00")
    assert forecast.summary.credit_card_balance == Decimal("-1000.00")
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


async def _forecast_workspace(session, name):
    user_id = uuid.uuid4()
    ws = Workspace(id=uuid.uuid4(), name=name, default_currency="USD")
    session.add(ws)
    await session.flush()
    checking = Account(
        id=uuid.uuid4(),
        user_id=user_id,
        workspace_id=ws.id,
        name="Main Checking",
        type="checking",
        balance=Decimal("5000.00"),
        currency="USD",
    )
    session.add(checking)
    await session.flush()
    session.add(_opening_balance_tx(user_id, ws.id, checking.id, "5000.00", "credit", date(2026, 8, 1)))
    return user_id, ws, checking


def _manual_recurring(user_id, workspace_id, account_id, description, frequency, next_occurrence, **kwargs):
    return RecurringTransaction(
        id=uuid.uuid4(),
        user_id=user_id,
        workspace_id=workspace_id,
        account_id=account_id,
        description=description,
        amount=kwargs.pop("amount", Decimal("100.00")),
        currency=kwargs.pop("currency", "USD"),
        type="debit",
        frequency=frequency,
        start_date=kwargs.pop("start_date", next_occurrence),
        next_occurrence=next_occurrence,
        is_active=True,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_cashflow_forecast_bills_semiannual_manual_recurring_once(session):
    user_id, ws, checking = await _forecast_workspace(session, "Forecast Semiannual WS")
    session.add(_manual_recurring(
        user_id, ws.id, checking.id, "Car Insurance", "semiannual", date(2026, 9, 10)
    ))
    await session.commit()

    forecast = await generate_cashflow_forecast(
        session, ws.id, horizon_days=90, include_discretionary_burn=False,
        reference_date=date(2026, 9, 1),
    )

    charge_days = [p.date for p in forecast.daily_trajectory if p.recurring_outflow > 0]
    assert charge_days == [date(2026, 9, 10)]
    assert forecast.summary.total_projected_outflow == Decimal("100.00")


@pytest.mark.asyncio
async def test_cashflow_forecast_stops_manual_recurring_at_end_date(session):
    user_id, ws, checking = await _forecast_workspace(session, "Forecast End Date WS")
    session.add(_manual_recurring(
        user_id, ws.id, checking.id, "Gym", "monthly", date(2026, 9, 5),
        end_date=date(2026, 10, 15),
    ))
    await session.commit()

    forecast = await generate_cashflow_forecast(
        session, ws.id, horizon_days=90, include_discretionary_burn=False,
        reference_date=date(2026, 9, 1),
    )

    charge_days = [p.date for p in forecast.daily_trajectory if p.recurring_outflow > 0]
    assert charge_days == [date(2026, 9, 5), date(2026, 10, 5)]


@pytest.mark.asyncio
async def test_cashflow_forecast_leaves_out_balances_without_fx_rate(session):
    user_id, ws, checking = await _forecast_workspace(session, "Forecast Missing FX WS")
    franc = Account(
        id=uuid.uuid4(),
        user_id=user_id,
        workspace_id=ws.id,
        name="Franc Savings",
        type="savings",
        balance=Decimal("0.00"),
        currency="CHF",
    )
    session.add(franc)
    await session.flush()
    session.add(Transaction(
        id=uuid.uuid4(),
        user_id=user_id,
        workspace_id=ws.id,
        account_id=franc.id,
        description="Deposit",
        amount=Decimal("1000.00"),
        currency="CHF",
        date=date(2026, 8, 10),
        effective_date=date(2026, 8, 10),
        type="credit",
        source="manual",
        status="posted",
    ))
    session.add(_manual_recurring(
        user_id, ws.id, franc.id, "Zurich Rent", "monthly", date(2026, 9, 3), currency="CHF",
    ))
    await session.commit()

    forecast = await generate_cashflow_forecast(
        session, ws.id, horizon_days=10, include_discretionary_burn=False,
        reference_date=date(2026, 9, 1),
    )

    # No CHF rate: the CHF balance and the CHF bill are left out rather than
    # counted as if they were dollars, and the currency is reported.
    assert forecast.summary.starting_liquid_balance == Decimal("5000.00")
    assert forecast.summary.total_projected_outflow == Decimal("0.00")
    assert forecast.summary.unconverted_currencies == ["CHF"]


@pytest.mark.asyncio
async def test_cashflow_forecast_counts_shared_credit_line_once(session):
    user_id, ws, checking = await _forecast_workspace(session, "Forecast Shared Line WS")
    cards = [
        Account(
            id=uuid.uuid4(),
            user_id=user_id,
            workspace_id=ws.id,
            name=name,
            type="credit_card",
            balance=Decimal("800.00"),
            currency="USD",
            shared_balance_group="line-1",
        )
        for name in ("Card A", "Card B")
    ]
    session.add_all(cards)
    await session.flush()
    for card in cards:
        session.add(_opening_balance_tx(user_id, ws.id, card.id, "800.00", "debit", date(2026, 8, 1)))
    await session.commit()

    forecast = await generate_cashflow_forecast(
        session, ws.id, horizon_days=10, include_discretionary_burn=False,
        reference_date=date(2026, 9, 1),
    )

    # Both cards report the same 800 of debt on one line, so it is counted once.
    assert forecast.summary.credit_card_balance == Decimal("-800.00")
    assert forecast.summary.starting_liquid_balance == Decimal("5000.00")


async def _category(session, user_id, workspace_id, name):
    category = Category(
        id=uuid.uuid4(),
        user_id=user_id,
        workspace_id=workspace_id,
        name=name,
        icon="circle",
        color="#000000",
    )
    session.add(category)
    await session.flush()
    return category


def _posted_debit(user_id, workspace_id, account_id, payee, amount, tx_date, **kwargs):
    return Transaction(
        id=uuid.uuid4(),
        user_id=user_id,
        workspace_id=workspace_id,
        account_id=account_id,
        description=payee,
        payee=payee,
        amount=Decimal(amount),
        amount_primary=kwargs.pop("amount_primary", Decimal(amount)),
        currency=kwargs.pop("currency", "USD"),
        date=tx_date,
        effective_date=tx_date,
        type=kwargs.pop("type", "debit"),
        source=kwargs.pop("source", "manual"),
        status="posted",
        **kwargs,
    )


@pytest.mark.asyncio
async def test_cashflow_forecast_keeps_pending_detected_outflow_scheduled(session):
    user_id, ws, checking = await _forecast_workspace(session, "Forecast Pending WS")
    # Billed on the 1st for three months, then July's charge was the last one:
    # 47 days later the pattern is pending (late), not lapsed.
    for tx_date in (date(2026, 5, 1), date(2026, 6, 1), date(2026, 7, 1)):
        session.add(_posted_debit(user_id, ws.id, checking.id, "Streamly", "15.00", tx_date))
    await session.commit()

    forecast = await generate_cashflow_forecast(
        session, ws.id, horizon_days=30, include_discretionary_burn=True,
        reference_date=date(2026, 8, 17),
    )

    charge_day = next(p for p in forecast.daily_trajectory if p.date == date(2026, 9, 1))
    assert charge_day.recurring_outflow == Decimal("15.00")
    assert charge_day.events == ["-15.00 Streamly (pending)"]
    assert forecast.summary.total_projected_outflow == Decimal("15.00")


@pytest.mark.asyncio
async def test_cashflow_forecast_counts_manual_bill_once_when_bank_rows_use_another_payee(session):
    user_id, ws, checking = await _forecast_workspace(session, "Forecast Same Bill WS")
    utilities = await _category(session, user_id, ws.id, "Utilities")
    session.add(_manual_recurring(
        user_id, ws.id, checking.id, "Electricity", "monthly", date(2026, 9, 15),
        amount=Decimal("120.00"), category_id=utilities.id,
    ))
    # The same bill imported from the bank, not linked to the rule and under
    # the utility's own name.
    for tx_date, amount in (
        (date(2026, 6, 15), "118.00"),
        (date(2026, 7, 15), "121.00"),
        (date(2026, 8, 15), "122.00"),
    ):
        session.add(_posted_debit(
            user_id, ws.id, checking.id, "PG&E Web Online", amount, tx_date,
            source="sync", category_id=utilities.id,
        ))
    await session.commit()

    forecast = await generate_cashflow_forecast(
        session, ws.id, horizon_days=60, include_discretionary_burn=False,
        reference_date=date(2026, 9, 1),
    )

    assert forecast.summary.total_projected_outflow == Decimal("240.00")
    bill_day = next(p for p in forecast.daily_trajectory if p.date == date(2026, 9, 15))
    assert bill_day.events == ["-120.00 Electricity"]


@pytest.mark.asyncio
async def test_cashflow_forecast_keeps_unrelated_bill_on_same_cadence(session):
    user_id, ws, checking = await _forecast_workspace(session, "Forecast Other Bill WS")
    session.add(_manual_recurring(
        user_id, ws.id, checking.id, "Electricity", "monthly", date(2026, 9, 15),
        amount=Decimal("120.00"),
    ))
    # Monthly too, same account, but a different amount: a separate bill.
    for tx_date in (date(2026, 6, 20), date(2026, 7, 20), date(2026, 8, 20)):
        session.add(_posted_debit(
            user_id, ws.id, checking.id, "City Water Dept", "45.00", tx_date, source="sync"
        ))
    await session.commit()

    forecast = await generate_cashflow_forecast(
        session, ws.id, horizon_days=30, include_discretionary_burn=False,
        reference_date=date(2026, 9, 1),
    )

    assert forecast.summary.total_projected_outflow == Decimal("165.00")


@pytest.mark.asyncio
async def test_cashflow_forecast_starts_on_the_workspace_calendar_day(session):
    from zoneinfo import ZoneInfo

    from app.core.app_clock import today_in, use_resolved_timezone

    _, ws, _ = await _forecast_workspace(session, "Forecast Clock WS")
    await session.commit()

    # UTC+14 and UTC-11 are always on different calendar days.
    starts = {}
    for name in ("Pacific/Kiritimati", "Pacific/Pago_Pago"):
        zone = ZoneInfo(name)
        with use_resolved_timezone(zone):
            forecast = await generate_cashflow_forecast(
                session, ws.id, horizon_days=7, include_discretionary_burn=False,
            )
        starts[name] = (forecast.summary.start_date, today_in(zone))

    for start_date, expected in starts.values():
        assert start_date == expected
    assert starts["Pacific/Kiritimati"][0] != starts["Pacific/Pago_Pago"][0]


@pytest.mark.asyncio
async def test_cashflow_forecast_keeps_similar_bill_in_another_category(session):
    user_id, ws, checking = await _forecast_workspace(session, "Forecast Near Amount WS")
    utilities = await _category(session, user_id, ws.id, "Utilities")
    fitness = await _category(session, user_id, ws.id, "Fitness")
    session.add(_manual_recurring(
        user_id, ws.id, checking.id, "Electricity", "monthly", date(2026, 9, 15),
        amount=Decimal("120.00"), category_id=utilities.id,
    ))
    # Same account, same cadence, within 10% of the rule: still another bill.
    for tx_date in (date(2026, 6, 20), date(2026, 7, 20), date(2026, 8, 20)):
        session.add(_posted_debit(
            user_id, ws.id, checking.id, "Iron Gym", "115.00", tx_date, category_id=fitness.id,
        ))
    await session.commit()

    forecast = await generate_cashflow_forecast(
        session, ws.id, horizon_days=30, include_discretionary_burn=False,
        reference_date=date(2026, 9, 1),
    )

    assert forecast.summary.total_projected_outflow == Decimal("235.00")


@pytest.mark.asyncio
async def test_cashflow_forecast_rule_name_inside_another_bill_keeps_that_bill(session):
    user_id, ws, checking = await _forecast_workspace(session, "Forecast Name Inside WS")
    session.add(_manual_recurring(
        user_id, ws.id, checking.id, "Gas", "monthly", date(2026, 9, 15), amount=Decimal("40.00"),
    ))
    # The rule's name sits inside this payee, but the amount says another bill.
    for tx_date in (date(2026, 6, 20), date(2026, 7, 20), date(2026, 8, 20)):
        session.add(_posted_debit(user_id, ws.id, checking.id, "Gas Station Club", "200.00", tx_date))
    await session.commit()

    forecast = await generate_cashflow_forecast(
        session, ws.id, horizon_days=30, include_discretionary_burn=False,
        reference_date=date(2026, 9, 1),
    )

    assert forecast.summary.total_projected_outflow == Decimal("240.00")


@pytest.mark.asyncio
async def test_cashflow_forecast_name_match_needs_a_whole_word(session):
    user_id, ws, checking = await _forecast_workspace(session, "Forecast Whole Word WS")
    session.add(_manual_recurring(
        user_id, ws.id, checking.id, "Rent", "monthly", date(2026, 9, 15), amount=Decimal("500.00"),
    ))
    # "rent" is inside "parent", same amount and cadence, unrelated bill.
    for tx_date in (date(2026, 6, 20), date(2026, 7, 20), date(2026, 8, 20)):
        session.add(_posted_debit(user_id, ws.id, checking.id, "Parent Care", "500.00", tx_date))
    await session.commit()

    forecast = await generate_cashflow_forecast(
        session, ws.id, horizon_days=30, include_discretionary_burn=False,
        reference_date=date(2026, 9, 1),
    )

    assert forecast.summary.total_projected_outflow == Decimal("1000.00")


@pytest.mark.asyncio
async def test_cashflow_forecast_name_match_within_guards_counts_bill_once(session):
    user_id, ws, checking = await _forecast_workspace(session, "Forecast Name Guarded WS")
    session.add(_manual_recurring(
        user_id, ws.id, checking.id, "Netflix", "monthly", date(2026, 9, 15), amount=Decimal("15.00"),
    ))
    for tx_date in (date(2026, 6, 15), date(2026, 7, 15), date(2026, 8, 15)):
        session.add(_posted_debit(user_id, ws.id, checking.id, "POS DEBIT NETFLIX.COM", "15.00", tx_date))
    await session.commit()

    forecast = await generate_cashflow_forecast(
        session, ws.id, horizon_days=30, include_discretionary_burn=False,
        reference_date=date(2026, 9, 1),
    )

    assert forecast.summary.total_projected_outflow == Decimal("15.00")


@pytest.mark.asyncio
async def test_cashflow_forecast_burn_keeps_one_off_named_like_a_rule(session):
    user_id, ws, checking = await _forecast_workspace(session, "Forecast Burn Name WS")
    session.add(_manual_recurring(
        user_id, ws.id, checking.id, "Gas", "monthly", date(2026, 9, 15), amount=Decimal("40.00"),
    ))
    session.add(_posted_debit(user_id, ws.id, checking.id, "Gas Station", "900.00", date(2026, 8, 20)))
    await session.commit()

    forecast = await generate_cashflow_forecast(
        session, ws.id, horizon_days=7, reference_date=date(2026, 9, 1),
    )

    assert forecast.daily_trajectory[1].discretionary_burn == Decimal("10.00")


@pytest.mark.asyncio
async def test_cashflow_forecast_burn_keeps_debit_named_like_a_detected_inflow(session):
    user_id, ws, checking = await _forecast_workspace(session, "Forecast Burn Inflow WS")
    for tx_date in (date(2026, 6, 1), date(2026, 7, 1), date(2026, 8, 1)):
        session.add(_posted_debit(
            user_id, ws.id, checking.id, "Venmo", "300.00", tx_date, type="credit",
        ))
    session.add(_posted_debit(user_id, ws.id, checking.id, "Venmo", "900.00", date(2026, 8, 20)))
    await session.commit()

    forecast = await generate_cashflow_forecast(
        session, ws.id, horizon_days=7, reference_date=date(2026, 9, 1),
    )

    assert forecast.daily_trajectory[1].discretionary_burn == Decimal("10.00")


@pytest.mark.asyncio
async def test_cashflow_forecast_burn_divides_by_the_full_window(session):
    user_id, ws, checking = await _forecast_workspace(session, "Forecast Burn Window WS")
    # One purchase three days ago is 900 over 90 days, not 900 over 3 days.
    session.add(_posted_debit(user_id, ws.id, checking.id, "Sofa Shop", "900.00", date(2026, 8, 29)))
    await session.commit()

    forecast = await generate_cashflow_forecast(
        session, ws.id, horizon_days=7, reference_date=date(2026, 9, 1),
    )

    assert forecast.daily_trajectory[1].discretionary_burn == Decimal("10.00")


@pytest.mark.asyncio
async def test_cashflow_forecast_leaves_out_foreign_history_without_fx_rate(session):
    user_id, ws, checking = await _forecast_workspace(session, "Forecast History FX WS")
    for tx_date in (date(2026, 6, 20), date(2026, 7, 20), date(2026, 8, 20)):
        session.add(_posted_debit(
            user_id, ws.id, checking.id, "Zurich Gym", "100.00", tx_date,
            currency="CHF", amount_primary=None,
        ))
    session.add(_posted_debit(
        user_id, ws.id, checking.id, "Bern Cafe", "900.00", date(2026, 8, 29),
        currency="CHF", amount_primary=None,
    ))
    await session.commit()

    forecast = await generate_cashflow_forecast(
        session, ws.id, horizon_days=30, reference_date=date(2026, 9, 1),
    )

    assert forecast.summary.total_projected_outflow == Decimal("0.00")
    assert forecast.summary.total_projected_discretionary == Decimal("0.00")
    assert forecast.summary.unconverted_currencies == ["CHF"]


@pytest.mark.asyncio
async def test_cashflow_forecast_converts_foreign_history_with_a_rate(session):
    user_id, ws, checking = await _forecast_workspace(session, "Forecast History Rate WS")
    # USD/SEK of 0.5: 100 kronor are 200 dollars.
    session.add(FxRate(
        base_currency="USD", quote_currency="SEK", date=date(2026, 6, 1),
        rate=Decimal("0.5"), source="test",
    ))
    for tx_date in (date(2026, 6, 20), date(2026, 7, 20), date(2026, 8, 20)):
        session.add(_posted_debit(
            user_id, ws.id, checking.id, "Malmo Gym", "100.00", tx_date,
            currency="SEK", amount_primary=None,
        ))
    session.add(_posted_debit(
        user_id, ws.id, checking.id, "Lund Cafe", "450.00", date(2026, 8, 29),
        currency="SEK", amount_primary=None,
    ))
    await session.commit()

    forecast = await generate_cashflow_forecast(
        session, ws.id, horizon_days=30, reference_date=date(2026, 9, 1),
    )

    assert forecast.summary.total_projected_outflow == Decimal("200.00")
    assert forecast.daily_trajectory[1].discretionary_burn == Decimal("10.00")
    assert forecast.summary.unconverted_currencies == []


@pytest.mark.asyncio
async def test_detector_reports_amounts_in_workspace_currency(session):
    user_id, ws, checking = await _forecast_workspace(session, "Detector FX WS")
    session.add(FxRate(
        base_currency="USD", quote_currency="DKK", date=date(2026, 6, 1),
        rate=Decimal("0.5"), source="test",
    ))
    for tx_date in (date(2026, 6, 20), date(2026, 7, 20), date(2026, 8, 20)):
        session.add(_posted_debit(
            user_id, ws.id, checking.id, "Aarhus Gym", "100.00", tx_date,
            currency="DKK", amount_primary=None,
        ))
    for tx_date in (date(2026, 6, 21), date(2026, 7, 21), date(2026, 8, 21)):
        session.add(_posted_debit(
            user_id, ws.id, checking.id, "Oslo Gym", "100.00", tx_date,
            currency="NOK", amount_primary=None,
        ))
    await session.commit()

    detected = await detect_recurring_patterns(
        session, ws.id, reference_date=date(2026, 9, 1),
    )

    assert [(i.normalized_key, i.average_amount, i.currency) for i in detected.items] == [
        ("aarhus gym", Decimal("200.00"), "USD"),
    ]
    assert detected.unconverted_currencies == ["NOK"]


@pytest.mark.asyncio
async def test_cashflow_forecast_card_debt_does_not_end_the_runway(session):
    user_id, ws, checking = await _forecast_workspace(session, "Forecast Card Runway WS")
    card = Account(
        id=uuid.uuid4(),
        user_id=user_id,
        workspace_id=ws.id,
        name="Card",
        type="credit_card",
        balance=Decimal("0.00"),
        currency="USD",
    )
    session.add(card)
    await session.flush()
    session.add(_opening_balance_tx(user_id, ws.id, card.id, "8000.00", "debit", date(2026, 8, 1)))
    await session.commit()

    forecast = await generate_cashflow_forecast(
        session, ws.id, horizon_days=10, include_discretionary_burn=False,
        reference_date=date(2026, 9, 1),
    )

    # 5,000 in checking against 8,000 on a card is not out of money today.
    assert forecast.summary.starting_liquid_balance == Decimal("5000.00")
    assert forecast.summary.credit_card_balance == Decimal("-8000.00")
    assert forecast.summary.has_shortfall_risk is False
    assert forecast.summary.runway_days is None


@pytest.mark.asyncio
async def test_cashflow_forecast_liquid_balance_counts_spendable_account_types(session):
    user_id, ws, checking = await _forecast_workspace(session, "Forecast Liquid Types WS")
    for name, account_type, amount, tx_type in (
        ("Savings", "savings", "1000.00", "credit"),
        ("Wallet", "wallet", "200.00", "credit"),
        ("Brokerage", "investment", "9000.00", "credit"),
        ("Car Loan", "loan", "7000.00", "debit"),
    ):
        account = Account(
            id=uuid.uuid4(),
            user_id=user_id,
            workspace_id=ws.id,
            name=name,
            type=account_type,
            balance=Decimal("0.00"),
            currency="USD",
        )
        session.add(account)
        await session.flush()
        session.add(_opening_balance_tx(user_id, ws.id, account.id, amount, tx_type, date(2026, 8, 1)))
    await session.commit()

    forecast = await generate_cashflow_forecast(
        session, ws.id, horizon_days=7, include_discretionary_burn=False,
        reference_date=date(2026, 9, 1),
    )

    assert forecast.summary.starting_liquid_balance == Decimal("6200.00")
