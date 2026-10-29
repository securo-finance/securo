"""Loan payment schedule computed from the contract terms, on read.

Nothing here writes to the ledger. The schedule is what the contract says,
the account's transactions are what actually happened, and the summary
compares the two (ahead or behind, projected payoff from the real balance).
"""

import calendar
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

LOAN_KINDS = ("mortgage", "auto", "personal", "student", "other")
# How the contract quotes its rate. Brazilian contracts usually quote a monthly
# rate or an effective yearly one; US loans quote a nominal yearly rate (APR).
RATE_PERIODS = ("monthly", "yearly_effective", "yearly_nominal")
# price: constant payment (French amortization). sac: constant principal, the
# payment falls every month (Sistema de Amortização Constante).
AMORTIZATION_METHODS = ("price", "sac")
MAX_TERM_MONTHS = 600

_CENT = Decimal("0.01")
# A projection that ends within this much of zero is treated as paid off: the
# contract payment is rounded to cents, so a remainder of a few cents is noise.
_PAYOFF_TOLERANCE = Decimal("1.00")
_MAX_PROJECTION_MONTHS = 1200


def _cents(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def monthly_rate(rate_percent: Decimal, rate_period: str) -> Decimal:
    rate = Decimal(rate_percent) / 100
    if rate_period == "monthly":
        return rate
    if rate_period == "yearly_nominal":
        return rate / 12
    # Effective yearly rate: the monthly rate that compounds to it over a year.
    return (1 + rate) ** (Decimal(1) / Decimal(12)) - 1


def add_months(start: date, months: int) -> date:
    month_index = start.month - 1 + months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, min(start.day, calendar.monthrange(year, month)[1]))


@dataclass(frozen=True)
class Installment:
    number: int
    due_date: date
    payment: Decimal
    interest: Decimal
    principal: Decimal
    balance: Decimal


def build_schedule(
    *,
    principal: Decimal,
    rate_percent: Decimal,
    rate_period: str,
    term_months: int,
    first_payment_date: date,
    method: str,
) -> list[Installment]:
    rate = monthly_rate(rate_percent, rate_period)
    principal = Decimal(principal)
    if method == "price":
        if rate == 0:
            fixed_payment = _cents(principal / term_months)
        else:
            fixed_payment = _cents(principal * rate / (1 - (1 + rate) ** -term_months))
    else:
        fixed_principal = _cents(principal / term_months)

    schedule: list[Installment] = []
    balance = principal
    for number in range(1, term_months + 1):
        interest = _cents(balance * rate)
        if number == term_months:
            # The last installment absorbs the rounding of the earlier ones.
            principal_part = balance
        elif method == "price":
            principal_part = fixed_payment - interest
        else:
            principal_part = fixed_principal
        balance = balance - principal_part
        schedule.append(
            Installment(
                number=number,
                due_date=add_months(first_payment_date, number - 1),
                payment=interest + principal_part,
                interest=interest,
                principal=principal_part,
                balance=_cents(balance),
            )
        )
    return schedule


@dataclass(frozen=True)
class LoanSummary:
    payments_due: int
    remaining_payments: int
    next_payment: Optional[Installment]
    scheduled_balance: Decimal
    # Positive when the real balance is below the contract's: ahead of schedule.
    ahead_by: Decimal
    total_interest: Decimal
    total_paid: Decimal
    payoff_date: date
    projected_payoff_date: Optional[date]
    paid_off: bool


def _projected_payoff(
    schedule: list[Installment],
    actual_balance: Decimal,
    start: date,
    method: str,
    rate: Decimal,
) -> Optional[date]:
    """When the real balance reaches zero paying what the contract asks."""
    if method == "sac":
        amortization = schedule[0].principal
        if amortization <= 0:
            return None
        remaining = max(actual_balance - _PAYOFF_TOLERANCE, Decimal("0"))
        months = int((remaining / amortization).to_integral_value(rounding="ROUND_CEILING"))
        return add_months(start, max(months, 1) - 1)

    payment = schedule[0].payment
    balance = actual_balance
    for month in range(_MAX_PROJECTION_MONTHS):
        interest = balance * rate
        if payment <= interest:
            return None  # the payment does not even cover the interest
        balance = balance + interest - payment
        if balance <= _PAYOFF_TOLERANCE:
            return add_months(start, month)
    return None


def summarize(
    schedule: list[Installment],
    *,
    actual_balance: Decimal,
    today: date,
    method: str,
    rate: Decimal,
) -> LoanSummary:
    due = [row for row in schedule if row.due_date < today]
    upcoming = [row for row in schedule if row.due_date >= today]
    original = schedule[0].balance + schedule[0].principal
    scheduled_balance = due[-1].balance if due else original
    paid_off = actual_balance <= 0

    projected = None
    if not paid_off:
        start = upcoming[0].due_date if upcoming else add_months(schedule[-1].due_date, 1)
        projected = _projected_payoff(schedule, actual_balance, start, method, rate)

    return LoanSummary(
        payments_due=len(due),
        remaining_payments=len(upcoming),
        next_payment=upcoming[0] if upcoming and not paid_off else None,
        scheduled_balance=scheduled_balance,
        ahead_by=scheduled_balance - actual_balance,
        total_interest=sum((row.interest for row in schedule), Decimal("0")),
        total_paid=sum((row.payment for row in schedule), Decimal("0")),
        payoff_date=schedule[-1].due_date,
        projected_payoff_date=projected,
        paid_off=paid_off,
    )
