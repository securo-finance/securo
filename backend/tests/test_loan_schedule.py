from datetime import date
from decimal import Decimal

import pytest

from app.services.loan_schedule import (
    add_months,
    build_schedule,
    monthly_rate,
    summarize,
)


def test_monthly_rate_follows_the_rate_period():
    assert monthly_rate(Decimal("1.5"), "monthly") == Decimal("0.015")
    assert monthly_rate(Decimal("12"), "yearly_nominal") == Decimal("0.01")
    # 12.6825% a year compounded monthly is 1% a month.
    assert monthly_rate(Decimal("12.682503"), "yearly_effective").quantize(Decimal("0.000001")) == Decimal(
        "0.010000"
    )


def test_add_months_clamps_to_the_end_of_a_short_month():
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2026, 1, 31), 2) == date(2026, 3, 31)
    assert add_months(date(2026, 11, 15), 3) == date(2027, 2, 15)


def test_price_schedule_has_a_constant_payment_and_pays_off_exactly():
    schedule = build_schedule(
        principal=Decimal("10000"),
        rate_percent=Decimal("1"),
        rate_period="monthly",
        term_months=12,
        first_payment_date=date(2026, 1, 10),
        method="price",
    )

    assert len(schedule) == 12
    # 10,000 at 1% a month over 12 months: 888.49 a month.
    assert {row.payment for row in schedule[:-1]} == {Decimal("888.49")}
    assert schedule[0].interest == Decimal("100.00")
    assert schedule[0].principal == Decimal("788.49")
    assert schedule[-1].balance == Decimal("0.00")
    assert sum(row.principal for row in schedule) == Decimal("10000.00")
    assert schedule[-1].due_date == date(2026, 12, 10)


def test_sac_schedule_has_constant_principal_and_falling_payments():
    schedule = build_schedule(
        principal=Decimal("12000"),
        rate_percent=Decimal("1"),
        rate_period="monthly",
        term_months=12,
        first_payment_date=date(2026, 1, 10),
        method="sac",
    )

    assert {row.principal for row in schedule} == {Decimal("1000.00")}
    assert schedule[0].payment == Decimal("1120.00")
    assert schedule[1].payment == Decimal("1110.00")
    assert schedule[-1].payment == Decimal("1010.00")
    assert schedule[-1].balance == Decimal("0.00")


def test_a_zero_rate_loan_splits_the_principal_evenly():
    schedule = build_schedule(
        principal=Decimal("1000"),
        rate_percent=Decimal("0"),
        rate_period="monthly",
        term_months=3,
        first_payment_date=date(2026, 1, 1),
        method="price",
    )

    assert [row.payment for row in schedule] == [Decimal("333.33"), Decimal("333.33"), Decimal("333.34")]
    assert schedule[-1].balance == Decimal("0.00")


def _price_12():
    return build_schedule(
        principal=Decimal("10000"),
        rate_percent=Decimal("1"),
        rate_period="monthly",
        term_months=12,
        first_payment_date=date(2026, 1, 10),
        method="price",
    )


def test_summary_compares_the_contract_with_the_real_balance():
    schedule = _price_12()

    # 6,000 owed against about 7,623 scheduled: more than a full payment ahead.
    summary = summarize(schedule, actual_balance=Decimal("6000"), today=date(2026, 4, 1), method="price", rate=Decimal("0.01"))

    # Three installments were due before April 1st.
    assert summary.payments_due == 3
    assert summary.remaining_payments == 9
    assert summary.next_payment == schedule[3]
    assert summary.scheduled_balance == schedule[2].balance
    # Owing less than the contract says means the loan is ahead of schedule.
    assert summary.ahead_by == schedule[2].balance - Decimal("6000")
    assert summary.total_interest == sum(row.interest for row in schedule)
    assert summary.payoff_date == date(2026, 12, 10)
    assert summary.projected_payoff_date is not None
    assert summary.projected_payoff_date == date(2026, 11, 10)


def test_summary_of_a_paid_off_loan():
    summary = summarize(_price_12(), actual_balance=Decimal("0"), today=date(2026, 6, 1), method="price", rate=Decimal("0.01"))

    assert summary.projected_payoff_date is None
    assert summary.paid_off is True


@pytest.mark.parametrize("method", ["price", "sac"])
def test_projection_from_the_real_balance_keeps_the_contract_payment(method: str):
    schedule = build_schedule(
        principal=Decimal("12000"),
        rate_percent=Decimal("1"),
        rate_period="monthly",
        term_months=12,
        first_payment_date=date(2026, 1, 10),
        method=method,
    )

    # Nothing paid yet and the first installment is next: the projection is the contract.
    summary = summarize(schedule, actual_balance=Decimal("12000"), today=date(2026, 1, 1), method=method, rate=Decimal("0.01"))

    assert summary.projected_payoff_date == summary.payoff_date


def test_a_balance_above_the_contract_is_behind_schedule():
    schedule = _price_12()

    summary = summarize(schedule, actual_balance=Decimal("9500"), today=date(2026, 4, 1), method="price", rate=Decimal("0.01"))

    assert summary.ahead_by < 0


def test_sac_projection_ignores_the_cents_lost_to_rounding():
    # 400,000 over 360 months amortizes 1,111.11 a month, 0.40 short over the
    # contract; one payment behind must project one month late, not two.
    schedule = build_schedule(
        principal=Decimal("400000"),
        rate_percent=Decimal("11.5"),
        rate_period="yearly_effective",
        term_months=360,
        first_payment_date=date(2025, 11, 10),
        method="sac",
    )
    behind_one_payment = schedule[11].balance + schedule[11].principal

    summary = summarize(
        schedule,
        actual_balance=behind_one_payment,
        today=date(2026, 10, 22),
        method="sac",
        rate=monthly_rate(Decimal("11.5"), "yearly_effective"),
    )

    assert summary.payoff_date == date(2055, 10, 10)
    assert summary.projected_payoff_date == date(2055, 11, 10)
