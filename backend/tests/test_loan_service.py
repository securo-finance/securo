from decimal import Decimal
from app.services.loan_service import (
    calculate_flat_interest_emi,
    calculate_reducing_balance_emi,
    compute_repayment_breakdown,
    build_loan_summary,
)
from app.models.account import Account


def test_calculate_flat_interest_emi():
    # Principal: 10000, Rate: 10%, Term: 12 months (1 year)
    # Total Interest = 10000 * 0.10 * 1 = 1000
    # Total Payable = 11000
    # Monthly EMI = 11000 / 12 = 916.67
    emi, total_interest, total_payable = calculate_flat_interest_emi(Decimal("10000.00"), Decimal("10.00"), 12)
    assert emi == Decimal("916.67")
    assert total_interest == Decimal("1000.00")
    assert total_payable == Decimal("11000.00")


def test_calculate_reducing_balance_emi():
    # Principal: 12000, Rate: 12%, Term: 12 months
    # Monthly rate r = 0.01
    emi, total_interest, total_payable = calculate_reducing_balance_emi(Decimal("12000.00"), Decimal("12.00"), 12)
    assert emi > Decimal("1000.00")
    assert total_payable > Decimal("12000.00")
    assert total_interest == total_payable - Decimal("12000.00")


def test_compute_repayment_breakdown_flat():
    # Flat interest breakdown for repayment of 916.67 on 10000 principal
    principal_p, interest_p = compute_repayment_breakdown(
        principal=Decimal("10000.00"),
        current_balance=Decimal("10000.00"),
        rate_percent=Decimal("10.00"),
        interest_type="flat",
        term_months=12,
        repayment_amount=Decimal("916.67"),
    )
    assert principal_p + interest_p == Decimal("916.67")
    assert interest_p > Decimal("0.00")


def test_compute_repayment_breakdown_reducing():
    # Reducing balance interest breakdown for payment of 1000 on 10000 balance at 12% annual rate
    # Monthly interest = 10000 * (0.12 / 12) = 100.00
    # Principal portion = 1000 - 100 = 900.00
    principal_p, interest_p = compute_repayment_breakdown(
        principal=Decimal("10000.00"),
        current_balance=Decimal("10000.00"),
        rate_percent=Decimal("12.00"),
        interest_type="reducing",
        term_months=12,
        repayment_amount=Decimal("1000.00"),
    )
    assert interest_p == Decimal("100.00")
    assert principal_p == Decimal("900.00")


def test_build_loan_summary():
    account = Account(
        original_principal=Decimal("5000.00"),
        balance=Decimal("5000.00"),
        interest_rate=Decimal("8.00"),
        interest_type="reducing",
        loan_term_months=24,
    )
    summary = build_loan_summary(account, Decimal("4000.00"))
    assert summary["original_principal"] == 5000.0
    assert summary["current_balance"] == 4000.0
    assert summary["principal_paid"] == 1000.0
