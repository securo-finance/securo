from decimal import Decimal
import math
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from app.models.account import Account


def calculate_flat_interest_emi(principal: Decimal, rate_percent: Decimal, term_months: int) -> tuple[Decimal, Decimal, Decimal]:
    """Calculate (monthly_emi, total_interest, total_payable) using Flat Interest method.

    Total Interest = Principal * (Rate / 100) * (Term / 12)
    Total Payable = Principal + Total Interest
    Monthly EMI = Total Payable / Term
    """
    if term_months <= 0:
        return Decimal("0.00"), Decimal("0.00"), principal

    r_annual = rate_percent / Decimal("100")
    years = Decimal(str(term_months)) / Decimal("12")
    total_interest = principal * r_annual * years
    total_payable = principal + total_interest
    monthly_emi = total_payable / Decimal(str(term_months))
    return round(monthly_emi, 2), round(total_interest, 2), round(total_payable, 2)


def calculate_reducing_balance_emi(principal: Decimal, rate_percent: Decimal, term_months: int) -> tuple[Decimal, Decimal, Decimal]:
    """Calculate (monthly_emi, total_interest, total_payable) using Reducing Balance method.

    r = (rate_percent / 100) / 12
    EMI = P * r * (1+r)^n / ((1+r)^n - 1)
    """
    if term_months <= 0 or principal <= Decimal("0.00"):
        return Decimal("0.00"), Decimal("0.00"), principal

    r_monthly = (rate_percent / Decimal("100")) / Decimal("12")
    if r_monthly == Decimal("0.00"):
        monthly_emi = principal / Decimal(str(term_months))
        return round(monthly_emi, 2), Decimal("0.00"), round(principal, 2)

    # Use float for exponential math then convert back
    r_float = float(r_monthly)
    p_float = float(principal)
    n = term_months

    emi_float = p_float * (r_float * ((1 + r_float) ** n)) / (((1 + r_float) ** n) - 1)
    monthly_emi = Decimal(str(round(emi_float, 2)))

    # Compute amortized total interest and payable over term_months
    balance = p_float
    accumulated_interest = 0.0
    for _ in range(n):
        interest_for_month = balance * r_float
        accumulated_interest += interest_for_month
        principal_for_month = emi_float - interest_for_month
        balance = max(0.0, balance - principal_for_month)

    total_interest = Decimal(str(round(accumulated_interest, 2)))
    total_payable = Decimal(str(round(p_float + accumulated_interest, 2)))

    return monthly_emi, total_interest, total_payable


def compute_repayment_breakdown(
    principal: Decimal,
    current_balance: Decimal,
    rate_percent: Decimal,
    interest_type: str,
    term_months: int,
    repayment_amount: Decimal,
) -> tuple[Decimal, Decimal]:
    """Compute (principal_portion, interest_portion) for a given repayment amount."""
    if repayment_amount <= Decimal("0.00"):
        return Decimal("0.00"), Decimal("0.00")

    itype = (interest_type or "reducing").lower()

    if itype == "flat":
        _, total_interest, total_payable = calculate_flat_interest_emi(principal, rate_percent, term_months)
        if total_payable > Decimal("0.00"):
            interest_ratio = total_interest / total_payable
            interest_portion = round(repayment_amount * interest_ratio, 2)
            principal_portion = repayment_amount - interest_portion
        else:
            principal_portion = repayment_amount
            interest_portion = Decimal("0.00")
    else:  # reducing balance
        r_monthly = (rate_percent / Decimal("100")) / Decimal("12")
        interest_for_period = round(current_balance * r_monthly, 2)
        interest_portion = min(repayment_amount, interest_for_period)
        principal_portion = repayment_amount - interest_portion

    return max(Decimal("0.00"), principal_portion), max(Decimal("0.00"), interest_portion)


def build_loan_summary(account: "Account", current_balance_val: Decimal) -> dict:
    """Build calculated loan metrics dictionary from account model and current balance."""
    principal = account.original_principal or account.balance or Decimal("0.00")
    rate = account.interest_rate or Decimal("0.00")
    itype = (account.interest_type or "reducing").lower()
    term = account.loan_term_months or 12

    if itype == "flat":
        calc_emi, total_interest, total_payable = calculate_flat_interest_emi(principal, rate, term)
    else:
        calc_emi, total_interest, total_payable = calculate_reducing_balance_emi(principal, rate, term)

    monthly_emi = account.monthly_emi if (account.monthly_emi and account.monthly_emi > Decimal("0.00")) else calc_emi
    next_p, next_i = compute_repayment_breakdown(principal, current_balance_val, rate, itype, term, monthly_emi)

    # Current outstanding balance on debt:
    # If balance is positive, that's current debt owed
    cur_bal = current_balance_val if current_balance_val >= Decimal("0.00") else abs(current_balance_val)
    principal_paid = max(Decimal("0.00"), principal - cur_bal)

    # Approximate interest paid so far based on principal paid ratio or transactions
    if principal > Decimal("0.00"):
        progress_ratio = principal_paid / principal
        interest_paid = round(total_interest * progress_ratio, 2)
    else:
        interest_paid = Decimal("0.00")

    return {
        "account_id": account.id,
        "original_principal": float(principal),
        "current_balance": float(cur_bal),
        "interest_rate": float(rate),
        "interest_type": itype,
        "loan_term_months": term,
        "monthly_emi": float(monthly_emi),
        "total_interest": float(total_interest),
        "total_payable": float(total_payable),
        "principal_paid": float(principal_paid),
        "interest_paid": float(interest_paid),
        "next_payment_principal": float(next_p),
        "next_payment_interest": float(next_i),
    }
