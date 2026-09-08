import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.models.account import Account
from app.models.transaction import Transaction
from app.models.workspace import Workspace
from app.schemas.forecast import CadenceEnum, FlowDirectionEnum, SubscriptionStatusEnum
from app.services.recurring_detector_service import (
    calculate_cadence,
    compute_next_occurrence,
    detect_recurring_patterns,
    is_likely_subscription,
    normalize_merchant_name,
)


def test_normalize_merchant_name():
    assert normalize_merchant_name("POS DEBIT NETFLIX.COM 12/24 CA USA") == "NETFLIX.COM"
    assert normalize_merchant_name("ACH DEBIT SPOTIFY USA #987654") == "SPOTIFY USA"
    assert normalize_merchant_name("SQ *COFFEE SHOP 2026-08-01") == "COFFEE SHOP"
    assert normalize_merchant_name("DIRECT DEBIT GYM MEMBERSHIP") == "GYM MEMBERSHIP"
    assert normalize_merchant_name("AMZN MKTP US*1A2B3C4D") != ""
    assert normalize_merchant_name(None) == "Unknown"
    assert normalize_merchant_name("") == "Unknown"



def test_calculate_cadence():
    assert calculate_cadence(7.1)[0] == CadenceEnum.WEEKLY
    assert calculate_cadence(14.2)[0] == CadenceEnum.BI_WEEKLY
    assert calculate_cadence(30.1)[0] == CadenceEnum.MONTHLY
    assert calculate_cadence(91.0)[0] == CadenceEnum.QUARTERLY
    assert calculate_cadence(182.0)[0] == CadenceEnum.SEMI_ANNUAL
    assert calculate_cadence(365.0)[0] == CadenceEnum.ANNUAL
    assert calculate_cadence(45.0)[0] == CadenceEnum.IRREGULAR


def test_is_likely_subscription():
    assert is_likely_subscription("Netflix", FlowDirectionEnum.OUTFLOW) is True
    assert is_likely_subscription("Spotify Premium", FlowDirectionEnum.OUTFLOW) is True
    assert is_likely_subscription("Amazon Prime", FlowDirectionEnum.OUTFLOW) is True
    assert is_likely_subscription("Employer Salary", FlowDirectionEnum.INFLOW) is False
    assert is_likely_subscription("Planet Fitness Gym", FlowDirectionEnum.OUTFLOW) is True


def test_compute_next_occurrence():
    base = date(2026, 1, 15)
    assert compute_next_occurrence(base, CadenceEnum.WEEKLY, 7.0) == date(2026, 1, 22)
    assert compute_next_occurrence(base, CadenceEnum.BI_WEEKLY, 14.0) == date(2026, 1, 29)
    assert compute_next_occurrence(base, CadenceEnum.MONTHLY, 30.0) == date(2026, 2, 15)
    assert compute_next_occurrence(base, CadenceEnum.ANNUAL, 365.0) == date(2027, 1, 15)


@pytest.mark.asyncio
async def test_detect_recurring_patterns_empty(session):
    ws_id = uuid.uuid4()
    res = await detect_recurring_patterns(session, ws_id)
    assert res.total_detected == 0
    assert res.active_subscriptions_count == 0
    assert res.items == []


@pytest.mark.asyncio
async def test_detect_monthly_subscription_pattern(session):
    # Setup test workspace, account, and monthly transactions
    user_id = uuid.uuid4()
    ws = Workspace(id=uuid.uuid4(), name="Forecast Test Workspace", default_currency="USD")
    session.add(ws)
    await session.flush()

    acc = Account(
        id=uuid.uuid4(),
        user_id=user_id,
        workspace_id=ws.id,
        name="Checking",
        type="checking",
        balance=Decimal("5000.00"),
        currency="USD",
    )
    session.add(acc)
    await session.flush()


    ref_date = date(2026, 8, 1)

    # Insert 4 monthly Netflix charges
    for i in range(4):
        tx_date = date(2026, 4 + i, 15)
        tx = Transaction(
            id=uuid.uuid4(),
            user_id=user_id,
            workspace_id=ws.id,
            account_id=acc.id,
            description="Netflix Subscription",
            payee="Netflix",
            amount=Decimal("19.99"),
            amount_primary=Decimal("19.99"),
            currency="USD",
            date=tx_date,
            effective_date=tx_date,
            type="debit",
            source="manual",
            status="posted",
        )
        session.add(tx)

    # Insert 4 bi-weekly payroll deposits
    for i in range(4):
        tx_date = date(2026, 6, 1) + timedelta(days=i * 14)
        tx = Transaction(
            id=uuid.uuid4(),
            user_id=user_id,
            workspace_id=ws.id,
            account_id=acc.id,
            description="TechCorp Payroll Deposit",
            payee="TechCorp Inc",
            amount=Decimal("3500.00"),
            amount_primary=Decimal("3500.00"),
            currency="USD",
            date=tx_date,
            effective_date=tx_date,
            type="credit",
            source="manual",
            status="posted",
        )
        session.add(tx)

    await session.commit()

    res = await detect_recurring_patterns(
        session, ws.id, min_occurrences=3, lookback_days=180, reference_date=ref_date
    )


    assert res.total_detected >= 2
    # Verify Netflix
    netflix_item = next((item for item in res.items if "netflix" in item.normalized_key.lower()), None)
    assert netflix_item is not None
    assert netflix_item.cadence == CadenceEnum.MONTHLY
    assert netflix_item.direction == FlowDirectionEnum.OUTFLOW
    assert netflix_item.average_amount == Decimal("19.99")
    assert netflix_item.is_subscription is True
    assert netflix_item.confidence_score >= 0.70
    assert netflix_item.status == SubscriptionStatusEnum.ACTIVE

    # Verify Payroll
    payroll_item = next((item for item in res.items if "techcorp" in item.normalized_key.lower()), None)
    assert payroll_item is not None
    assert payroll_item.cadence == CadenceEnum.BI_WEEKLY
    assert payroll_item.direction == FlowDirectionEnum.INFLOW
    assert payroll_item.average_amount == Decimal("3500.00")
