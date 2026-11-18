"""Month totals on the Recurring page (#532)."""
import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.models.account import Account
from app.models.category import Category
from app.models.fx_rate import FxRate
from app.models.recurring_transaction import RecurringTransaction
from app.services.recurring_transaction_service import get_month_summary


def _category(session, user, workspace, name, **flags):
    cat = Category(
        id=uuid.uuid4(), user_id=user.id, workspace_id=workspace.id,
        name=name, icon="x", color="#000000", **flags,
    )
    session.add(cat)
    return cat


def _recurring(session, user, workspace, account, description, amount, kind, frequency,
               start, **fields):
    rec = RecurringTransaction(
        id=uuid.uuid4(), user_id=user.id, workspace_id=workspace.id,
        account_id=account.id if account else None, description=description,
        amount=Decimal(amount), currency=fields.pop("currency", "BRL"), type=kind,
        frequency=frequency, start_date=start, next_occurrence=start, **fields,
    )
    session.add(rec)
    return rec


async def _summary(session, workspace, month):
    return await get_month_summary(session, workspace.id, month, "BRL")


@pytest.mark.asyncio
async def test_counts_each_occurrence_in_the_month(
    session, test_user, test_workspace, test_account,
):
    # The weekly one falls on Fridays: four in December 2026, five in January
    # 2027. The quarterly and yearly ones count only in their own months.
    _recurring(session, test_user, test_workspace, test_account,
               "Salary", "5000", "credit", "monthly", date(2026, 1, 5))
    _recurring(session, test_user, test_workspace, test_account,
               "Groceries", "100", "debit", "weekly", date(2026, 11, 6))
    _recurring(session, test_user, test_workspace, test_account,
               "Water", "90", "debit", "quarterly", date(2026, 9, 10))
    _recurring(session, test_user, test_workspace, test_account,
               "Insurance", "1200", "debit", "yearly", date(2026, 3, 1))
    await session.commit()

    december = await _summary(session, test_workspace, date(2026, 12, 1))
    assert december.income == Decimal("5000.00")
    assert december.expenses == Decimal("490.00")  # 4 x 100 + water
    assert december.net == Decimal("4510.00")

    january = await _summary(session, test_workspace, date(2027, 1, 20))
    assert january.expenses == Decimal("500.00")  # 5 x 100

    march_2027 = await _summary(session, test_workspace, date(2027, 3, 31))
    assert march_2027.month == date(2027, 3, 1)
    # 4 Fridays + water (Mar 10) + insurance (Mar 1).
    assert march_2027.expenses == Decimal("1690.00")


@pytest.mark.asyncio
async def test_already_generated_occurrences_still_count(
    session, test_user, test_workspace, test_account,
):
    rec = _recurring(session, test_user, test_workspace, test_account,
                     "Rent", "2000", "debit", "monthly", date(2026, 1, 10))
    rec.next_occurrence = date(2027, 1, 10)
    await session.commit()

    summary = await _summary(session, test_workspace, date(2026, 12, 1))
    assert summary.expenses == Decimal("2000.00")


@pytest.mark.asyncio
async def test_respects_dates_status_and_closed_accounts(
    session, test_user, test_workspace, test_account,
):
    closed = Account(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        name="Old", type="checking", balance=Decimal("0"), currency="BRL", is_closed=True,
    )
    session.add(closed)
    _recurring(session, test_user, test_workspace, test_account,
               "Ended", "10", "debit", "monthly", date(2026, 1, 1), end_date=date(2026, 11, 30))
    _recurring(session, test_user, test_workspace, test_account,
               "Later", "20", "debit", "monthly", date(2027, 1, 1))
    _recurring(session, test_user, test_workspace, test_account,
               "Paused", "30", "debit", "monthly", date(2026, 1, 1), is_active=False)
    _recurring(session, test_user, test_workspace, closed,
               "Closed account", "40", "debit", "monthly", date(2026, 1, 1))
    _recurring(session, test_user, test_workspace, None,
               "No account", "50", "debit", "monthly", date(2026, 1, 1))
    await session.commit()

    summary = await _summary(session, test_workspace, date(2026, 12, 1))
    assert summary.expenses == Decimal("50.00")


@pytest.mark.asyncio
async def test_transfers_and_ignored_stay_out_of_the_totals(
    session, test_user, test_workspace, test_account,
):
    transfer = _category(session, test_user, test_workspace, "Savings", treat_as_transfer=True)
    ignored = _category(session, test_user, test_workspace, "Hidden", is_ignored=True)
    await session.flush()
    _recurring(session, test_user, test_workspace, test_account,
               "To savings", "500", "debit", "weekly", date(2026, 12, 4), category_id=transfer.id)
    _recurring(session, test_user, test_workspace, test_account,
               "Hidden", "70", "debit", "monthly", date(2026, 1, 1), category_id=ignored.id)
    _recurring(session, test_user, test_workspace, test_account,
               "Gym", "80", "debit", "monthly", date(2026, 1, 1))
    await session.commit()

    summary = await _summary(session, test_workspace, date(2026, 12, 1))
    assert summary.expenses == Decimal("80.00")
    assert summary.income == Decimal("0")
    assert summary.transfer_count == 4


@pytest.mark.asyncio
async def test_foreign_currency_converts_from_its_native_amount(
    session, test_user, test_workspace, test_account,
):
    """100 USD reads as ~500 BRL for a BRL viewer, not as the stamped 100.

    ``amount_primary`` is stamped in the primary currency of whoever created
    the definition (here USD, so it equals the native amount). The viewer's
    primary is BRL, and the month's first-day rate is 5: the total has to be
    500, not the 100 the stamp would give nor the 600 today's rate would.
    """
    session.add(FxRate(
        id=uuid.uuid4(), base_currency="USD", quote_currency="BRL",
        date=date(2026, 10, 1), rate=Decimal("5.0"), source="test",
    ))
    session.add(FxRate(
        id=uuid.uuid4(), base_currency="USD", quote_currency="BRL",
        date=date(2026, 11, 17), rate=Decimal("6.0"), source="test",
    ))
    _recurring(session, test_user, test_workspace, test_account,
               "Stamped", "100", "debit", "monthly", date(2026, 1, 1),
               currency="USD", amount_primary=Decimal("100.00"))
    await session.commit()

    summary = await _summary(session, test_workspace, date(2026, 10, 15))
    assert summary.expenses == Decimal("500.00")
    assert summary.unconverted_count == 0


@pytest.mark.asyncio
async def test_currency_without_a_rate_stays_out_never_1_to_1(
    session, test_user, test_workspace, test_account,
):
    _recurring(session, test_user, test_workspace, test_account,
               "Stamped", "10", "debit", "monthly", date(2026, 1, 1),
               currency="USD", amount_primary=Decimal("55.00"))
    _recurring(session, test_user, test_workspace, test_account,
               "No rate", "10", "debit", "monthly", date(2026, 1, 1), currency="EUR")
    await session.commit()

    summary = await _summary(session, test_workspace, date(2026, 10, 1))
    # Neither the USD stamp nor a 1:1 read of the EUR amount counts.
    assert summary.expenses == Decimal("0")
    assert summary.unconverted_count == 2


@pytest.mark.asyncio
async def test_summary_route(client, auth_headers, test_account, session, test_user, test_workspace):
    _recurring(session, test_user, test_workspace, test_account,
               "Salary", "3000", "credit", "monthly", date(2026, 1, 5))
    _recurring(session, test_user, test_workspace, test_account,
               "Rent", "1000", "debit", "monthly", date(2026, 1, 10))
    await session.commit()

    resp = await client.get(
        "/api/recurring-transactions/summary", params={"month": "2026-12-01"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["month"] == "2026-12-01"
    assert data["currency"] == "BRL"
    assert Decimal(data["income"]) == Decimal("3000")
    assert Decimal(data["expenses"]) == Decimal("1000")
    assert Decimal(data["net"]) == Decimal("2000")
