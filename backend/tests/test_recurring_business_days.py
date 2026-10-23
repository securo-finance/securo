"""Recurring schedule rules: business days, holidays, grace days, match keywords.

The case these were written for: a salary paid on the fifth business day
(Saturdays counting, as the CLT has it, and paid the Friday before when the
fifth lands on one), synced as "Transferência Recebida", and a rent due on the
5th that may be paid a day late.
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.transaction import Transaction
from app.schemas.recurring_transaction import (
    RecurringTransactionCreate,
    RecurringTransactionUpdate,
)
from app.services import recurring_match_service as rms
from app.services.business_days import (
    is_business_day,
    nth_business_day,
    previous_banking_day,
)
from app.services.recurring_transaction_service import (
    adjust_weekend_date,
    create_recurring_transaction,
    effective_date_for,
    generate_pending,
    get_occurrences_in_range,
    update_recurring_transaction,
)


@pytest_asyncio.fixture
async def account(session: AsyncSession, test_user, test_workspace) -> Account:
    acc = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Conta",
        type="checking",
        balance=Decimal("10000"),
        currency="BRL",
    )
    session.add(acc)
    await session.commit()
    await session.refresh(acc)
    return acc


async def _make_bill(session, test_workspace, test_user, account, **overrides):
    data = RecurringTransactionCreate(
        description=overrides.pop("description", "Salário"),
        amount=overrides.pop("amount", Decimal("3000.00")),
        currency="BRL",
        type=overrides.pop("type", "credit"),
        frequency=overrides.pop("frequency", "monthly"),
        start_date=overrides.pop("start_date", date(2026, 8, 1)),
        account_id=account.id,
        **overrides,
    )
    return await create_recurring_transaction(session, test_workspace.id, test_user.id, data)


async def _add_tx(session, test_user, test_workspace, account, **kw):
    tx = Transaction(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=account.id,
        description=kw.get("description", "Transferência Recebida"),
        amount=kw.get("amount", Decimal("3000.00")),
        currency="BRL",
        date=kw["date"],
        type=kw.get("type", "credit"),
        source="sync",
        status="posted",
        external_id=kw.get("external_id", str(uuid.uuid4())),
    )
    session.add(tx)
    await session.commit()
    await session.refresh(tx)
    return tx


_SALARY = {
    "business_day": 5,
    "count_saturdays": True,
    "holiday_calendar": "BR",
    "weekend_adjustment": "previous_friday",
    "match_keywords": "Transferência Recebida",
}


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------


def test_brazilian_holidays_are_not_business_days():
    assert not is_business_day(date(2026, 9, 7), "BR")  # Independência, Monday
    assert not is_business_day(date(2026, 4, 3), "BR")  # Sexta-feira Santa
    assert not is_business_day(date(2026, 2, 16), "BR")  # Carnaval
    assert not is_business_day(date(2026, 6, 4), "BR")  # Corpus Christi
    assert not is_business_day(date(2026, 11, 20), "BR")  # Consciência Negra
    assert is_business_day(date(2026, 9, 7))  # no calendar: just a Monday
    assert not is_business_day(date(2026, 8, 1), "BR")  # Saturday
    assert is_business_day(date(2026, 8, 1), "BR", count_saturdays=True)
    assert not is_business_day(date(2026, 8, 2), "BR", count_saturdays=True)  # Sunday


def test_subdivision_adds_its_own_holidays_to_the_national_ones():
    # 9 Jul 2026 (Thursday), Revolução Constitucionalista: São Paulo only.
    assert not is_business_day(date(2026, 7, 9), "BR-SP")
    assert is_business_day(date(2026, 7, 9), "BR")
    # The bank calendar still applies with a subdivision.
    assert not is_business_day(date(2026, 2, 16), "BR-SP")  # Carnaval


def test_other_countries_use_their_own_calendar():
    assert not is_business_day(date(2026, 7, 3), "US")  # Independence Day, observed
    assert is_business_day(date(2026, 9, 7), "US-NY") is False  # Labor Day
    assert is_business_day(date(2026, 9, 7), "PT")  # a Monday in Portugal
    assert not is_business_day(date(2026, 7, 1), "PT-30")  # Dia da Madeira


def test_holiday_calendar_codes_are_normalized_and_checked():
    base = dict(description="x", amount=1, type="debit", frequency="monthly",
                start_date=date(2026, 1, 1), account_id=uuid.uuid4())
    assert RecurringTransactionCreate(**base, holiday_calendar=" br-sp ").holiday_calendar == "BR-SP"
    assert RecurringTransactionCreate(**base, holiday_calendar="pt").holiday_calendar == "PT"
    assert RecurringTransactionCreate(**base, holiday_calendar="").holiday_calendar is None
    for bad in ("XX", "BR-ZZ", "Brazil"):
        with pytest.raises(ValidationError):
            RecurringTransactionCreate(**base, holiday_calendar=bad)


def test_nth_business_day_skips_weekends_and_holidays():
    # September 2026: 1-4 Tue-Fri, 5-6 weekend, 7 holiday, so the 5th is the 8th.
    assert nth_business_day(2026, 9, 5, "BR") == date(2026, 9, 8)
    assert nth_business_day(2026, 9, 5) == date(2026, 9, 7)
    # Clamps to the last business day instead of spilling into October.
    assert nth_business_day(2026, 9, 23, "BR") == date(2026, 9, 30)


def test_previous_banking_day_steps_over_a_holiday_weekend():
    # Sunday 5 Apr 2026, Easter: Saturday, then Good Friday, so Thursday.
    assert previous_banking_day(date(2026, 4, 5), "BR") == date(2026, 4, 2)


@pytest.mark.parametrize(
    "month, paid_on",
    [
        # Saturdays count; a fifth that lands on one is paid the Friday before.
        (8, date(2026, 8, 6)),
        (9, date(2026, 9, 4)),  # the 5th is Saturday 5 Sep: paid Friday
        (10, date(2026, 10, 6)),
        (11, date(2026, 11, 6)),  # Saturday 7 Nov: paid Friday
    ],
)
def test_clt_fifth_business_day(month, paid_on):
    assert (
        adjust_weekend_date(
            date(2026, month, 1),
            "previous_friday",
            business_day=5,
            count_saturdays=True,
            holiday_calendar="BR",
        )
        == paid_on
    )


def test_weekend_adjustment_without_calendar_is_unchanged():
    saturday, sunday = date(2026, 8, 1), date(2026, 8, 2)
    assert adjust_weekend_date(saturday, "previous_friday") == date(2026, 7, 31)
    assert adjust_weekend_date(sunday, "previous_friday") == date(2026, 7, 31)
    assert adjust_weekend_date(saturday, "next_monday") == date(2026, 8, 3)
    assert adjust_weekend_date(sunday, "none") == sunday


def test_weekend_adjustment_moves_off_a_holiday_with_a_calendar():
    # 7 Sep 2026 is a Monday and a holiday.
    assert adjust_weekend_date(date(2026, 9, 7), "next_monday", holiday_calendar="BR") == date(2026, 9, 8)
    assert adjust_weekend_date(date(2026, 9, 7), "previous_friday", holiday_calendar="BR") == date(2026, 9, 4)
    assert adjust_weekend_date(date(2026, 9, 7), "previous_friday") == date(2026, 9, 7)


def test_occurrences_in_range_follow_business_days():
    occurrences = get_occurrences_in_range(
        start=date(2026, 8, 1),
        frequency="monthly",
        end_date=None,
        range_start=date(2026, 8, 1),
        range_end=date(2027, 1, 1),
        intended_day=1,
        weekend_adjustment="previous_friday",
        business_day=5,
        count_saturdays=True,
        holiday_calendar="BR",
    )
    assert occurrences == [
        date(2026, 8, 6),
        date(2026, 9, 4),
        date(2026, 10, 6),
        date(2026, 11, 6),
        date(2026, 12, 4),
    ]


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_type_aliases_are_normalized():
    base = {
        "description": "Gym",
        "amount": "99.90",
        "frequency": "monthly",
        "start_date": "2026-09-01",
        "account_id": str(uuid.uuid4()),
    }
    assert RecurringTransactionCreate(**base, type="expense").type == "debit"
    assert RecurringTransactionCreate(**base, type="Income").type == "credit"
    with pytest.raises(ValidationError):
        RecurringTransactionCreate(**base, type="refund")


def test_match_keywords_are_cleaned():
    assert RecurringTransactionUpdate(match_keywords=" a , ,b ").match_keywords == "a, b"
    assert RecurringTransactionUpdate(match_keywords="  ").match_keywords is None
    assert RecurringTransactionUpdate(match_keywords=["x", "y"]).match_keywords == "x, y"


# ---------------------------------------------------------------------------
# Creating and editing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_occurrence_snaps_to_day_of_month(session, test_user, test_workspace, account):
    """Start 1 Aug, day 17: the first occurrence is the 17th, not the 1st."""
    bill = await _make_bill(
        session, test_workspace, test_user, account,
        description="Streaming", amount=Decimal("50.00"), type="debit",
        day_of_month=17,
    )
    assert bill.next_occurrence == date(2026, 8, 17)


@pytest.mark.asyncio
async def test_first_occurrence_moves_to_next_month_when_day_already_passed(
    session, test_user, test_workspace, account
):
    bill = await _make_bill(
        session, test_workspace, test_user, account, start_date=date(2026, 8, 20), day_of_month=5,
    )
    assert bill.next_occurrence == date(2026, 9, 5)


@pytest.mark.asyncio
async def test_business_day_bill_starting_after_the_day_starts_next_month(
    session, test_user, test_workspace, account
):
    bill = await _make_bill(
        session, test_workspace, test_user, account, start_date=date(2026, 10, 20), **_SALARY,
    )
    assert effective_date_for(bill, bill.next_occurrence) == date(2026, 11, 6)


@pytest.mark.asyncio
async def test_business_day_requires_a_month_based_frequency(
    session, test_user, test_workspace, account
):
    with pytest.raises(ValueError):
        await _make_bill(
            session, test_workspace, test_user, account, frequency="weekly", business_day=5,
        )
    bill = await _make_bill(session, test_workspace, test_user, account, frequency="weekly")
    with pytest.raises(ValueError):
        await update_recurring_transaction(
            session, bill.id, test_workspace.id, RecurringTransactionUpdate(business_day=5),
        )


@pytest.mark.asyncio
async def test_switching_to_business_day_keeps_the_pointer_from_going_back(
    session, test_user, test_workspace, account
):
    bill = await _make_bill(session, test_workspace, test_user, account, day_of_month=5)
    bill.next_occurrence = date(2026, 10, 5)
    await session.commit()
    updated = await update_recurring_transaction(
        session, bill.id, test_workspace.id, RecurringTransactionUpdate(**_SALARY),
    )
    assert updated.business_day == 5
    assert updated.match_keywords == "Transferência Recebida"
    assert effective_date_for(updated, updated.next_occurrence) == date(2026, 10, 6)


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_keyword_links_a_salary_the_description_never_could(
    session, test_user, test_workspace, account
):
    without = await _make_bill(
        session, test_workspace, test_user, account,
        description="Salário sem palavra", business_day=5, count_saturdays=True,
        holiday_calendar="BR", weekend_adjustment="previous_friday",
    )
    assert await rms.find_bill_for_incoming(
        session, test_user.id, account.id, Decimal("3000.00"), "BRL", "credit",
        date(2026, 8, 6), "Transferência Recebida",
    ) is None

    without.is_active = False
    await session.commit()
    bill = await _make_bill(session, test_workspace, test_user, account, **_SALARY)
    match = await rms.find_bill_for_incoming(
        session, test_user.id, account.id, Decimal("3000.00"), "BRL", "credit",
        date(2026, 8, 6), "TRANSFERENCIA RECEBIDA|EMPRESA LTDA",
    )
    assert match is not None and match.id == bill.id


@pytest.mark.asyncio
async def test_keyword_does_not_relax_the_amount(session, test_user, test_workspace, account):
    await _make_bill(session, test_workspace, test_user, account, **_SALARY)
    assert await rms.find_bill_for_incoming(
        session, test_user.id, account.id, Decimal("13.00"), "BRL", "credit",
        date(2026, 8, 6), "Transferência Recebida|AMIGO",
    ) is None


@pytest.mark.asyncio
async def test_grace_days_extend_the_late_side_only(session, test_user, test_workspace, account):
    """Rent due on the 5th; the shipped window allows five days late."""
    rent = dict(
        description="Aluguel", amount=Decimal("1200.00"), type="debit",
        start_date=date(2026, 10, 5), day_of_month=5,
    )
    plain = await _make_bill(session, test_workspace, test_user, account, **rent)
    late = date(2026, 10, 12)  # seven days late
    assert await rms.find_bill_for_incoming(
        session, test_user.id, account.id, Decimal("1200.00"), "BRL", "debit", late, "Aluguel",
    ) is None
    plain.grace_days = 2
    await session.commit()
    match = await rms.find_bill_for_incoming(
        session, test_user.id, account.id, Decimal("1200.00"), "BRL", "debit", late, "Aluguel",
    )
    assert match is not None and match.id == plain.id
    # Early stays where it was: three days before.
    assert await rms.find_bill_for_incoming(
        session, test_user.id, account.id, Decimal("1200.00"), "BRL", "debit",
        date(2026, 9, 30), "Aluguel",
    ) is None


@pytest.mark.asyncio
async def test_a_pointer_that_fell_behind_still_meets_the_next_charge(
    session, test_user, test_workspace, account
):
    """Nothing matched in August; September's charge still links and the
    pointer moves past September rather than to it."""
    bill = await _make_bill(
        session, test_workspace, test_user, account,
        description="Google One", amount=Decimal("49.99"), type="debit", day_of_month=16,
    )
    assert bill.next_occurrence == date(2026, 8, 16)
    match = await rms.find_bill_for_incoming(
        session, test_user.id, account.id, Decimal("49.99"), "BRL", "debit",
        date(2026, 9, 16), "Google One",
    )
    assert match is not None and match.id == bill.id
    rms.advance_past(bill, date(2026, 9, 16))
    assert bill.next_occurrence == date(2026, 10, 16)


@pytest.mark.asyncio
async def test_placeholder_keyword_and_grace(session, test_user, test_workspace, account):
    bill = await _make_bill(
        session, test_workspace, test_user, account,
        description="Aluguel", amount=Decimal("1200.00"), type="debit",
        start_date=date(2026, 10, 5), day_of_month=5, grace_days=3,
        match_keywords="Pix enviado",
    )
    await generate_pending(session, test_user.id, up_to=date(2026, 10, 5))
    placeholder = (
        await session.execute(
            select(Transaction).where(Transaction.recurring_transaction_id == bill.id)
        )
    ).scalar_one()
    found = await rms.find_placeholder_for_incoming(
        session, account.id, Decimal("1200.00"), "BRL", "debit",
        date(2026, 10, 13), "PIX ENVIADO|PROPRIETARIO",
    )
    assert found is not None and found.id == placeholder.id


# ---------------------------------------------------------------------------
# generate_pending for bills that are not generated
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unattended_run_links_a_late_salary_without_writing_a_placeholder(
    session, test_user, test_workspace, account
):
    bill = await _make_bill(
        session, test_workspace, test_user, account,
        start_date=date(2026, 10, 1), auto_generate=False, **_SALARY,
    )
    tx = await _add_tx(session, test_user, test_workspace, account, date=date(2026, 10, 6))

    count = await generate_pending(session, test_user.id, up_to=date(2026, 10, 23))

    assert count == 0
    await session.refresh(tx)
    await session.refresh(bill)
    assert tx.recurring_transaction_id == bill.id
    assert effective_date_for(bill, bill.next_occurrence) == date(2026, 11, 6)
    rows = (
        await session.execute(select(Transaction).where(Transaction.account_id == account.id))
    ).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_unattended_run_leaves_an_unpaid_bill_projected(
    session, test_user, test_workspace, account
):
    bill = await _make_bill(
        session, test_workspace, test_user, account,
        description="Aluguel", amount=Decimal("1200.00"), type="debit",
        start_date=date(2026, 10, 5), day_of_month=5, auto_generate=False,
    )
    await generate_pending(session, test_user.id, up_to=date(2026, 10, 23))
    await session.refresh(bill)
    assert bill.next_occurrence == date(2026, 10, 5)


@pytest.mark.asyncio
async def test_unattended_run_catches_up_past_an_unmatched_month(
    session, test_user, test_workspace, account
):
    bill = await _make_bill(
        session, test_workspace, test_user, account,
        description="Google One", amount=Decimal("49.99"), type="debit",
        day_of_month=16, auto_generate=False,
    )
    # August's charge printed something else; October's names the bill.
    await _add_tx(
        session, test_user, test_workspace, account,
        description="Google XYZ0001", amount=Decimal("49.99"), type="debit",
        date=date(2026, 9, 16),
    )
    october = await _add_tx(
        session, test_user, test_workspace, account,
        description="Google One", amount=Decimal("49.99"), type="debit",
        date=date(2026, 10, 16),
    )
    await generate_pending(session, test_user.id, up_to=date(2026, 10, 23))
    await session.refresh(bill)
    await session.refresh(october)
    assert october.recurring_transaction_id == bill.id
    assert bill.next_occurrence == date(2026, 11, 16)


@pytest.mark.asyncio
async def test_amount_tolerance_links_a_rent_that_varies(
    session, test_user, test_workspace, account
):
    """Rent of 1200 paid as 1191.50 by Pix to the landlord, a day late."""
    bill = await _make_bill(
        session, test_workspace, test_user, account,
        description="Aluguel", amount=Decimal("1200.00"), type="debit",
        start_date=date(2026, 10, 5), day_of_month=5, grace_days=1,
        match_keywords="Maria Proprietaria", amount_tolerance_percent=Decimal("3"),
        auto_generate=False,
    )
    tx = await _add_tx(
        session, test_user, test_workspace, account,
        description="Transferência enviada pelo Pix|Maria Proprietaria Souza",
        amount=Decimal("1191.50"), type="debit", date=date(2026, 10, 6),
    )
    await generate_pending(session, test_user.id, up_to=date(2026, 10, 23))
    await session.refresh(tx)
    await session.refresh(bill)
    assert tx.recurring_transaction_id == bill.id
    assert bill.next_occurrence == date(2026, 11, 5)


@pytest.mark.asyncio
async def test_amount_tolerance_does_not_reach_past_its_percent(
    session, test_user, test_workspace, account
):
    await _make_bill(
        session, test_workspace, test_user, account,
        description="Aluguel", amount=Decimal("1200.00"), type="debit",
        start_date=date(2026, 10, 5), day_of_month=5,
        match_keywords="Proprietaria", amount_tolerance_percent=Decimal("3"),
    )
    assert await rms.find_bill_for_incoming(
        session, test_user.id, account.id, Decimal("1100.00"), "BRL", "debit",
        date(2026, 10, 5), "Transferência enviada|Maria Proprietaria Souza",
    ) is None


@pytest.mark.asyncio
async def test_amount_tolerance_refuses_to_pick_between_two_close_charges(
    session, test_user, test_workspace, account
):
    bill = await _make_bill(
        session, test_workspace, test_user, account,
        description="Aluguel", amount=Decimal("1200.00"), type="debit",
        start_date=date(2026, 10, 5), day_of_month=5,
        match_keywords="Proprietaria", amount_tolerance_percent=Decimal("3"),
        auto_generate=False,
    )
    for amount in ("1191.50", "1203.00"):
        await _add_tx(
            session, test_user, test_workspace, account,
            description="Pix|Proprietaria", amount=Decimal(amount), type="debit",
            date=date(2026, 10, 5),
        )
    assert await rms.find_real_tx_for_occurrence(session, bill, date(2026, 10, 5)) is None


@pytest.mark.asyncio
async def test_api_round_trips_the_rules_and_reports_the_due_date(
    client, auth_headers, test_account
):
    response = await client.post(
        "/api/recurring-transactions",
        json={
            "description": "Salário",
            "amount": 3000.00,
            "currency": "BRL",
            "type": "income",
            "frequency": "monthly",
            "start_date": "2026-10-01",
            "account_id": str(test_account.id),
            "auto_generate": False,
            **_SALARY,
        },
        headers=auth_headers,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["type"] == "credit"
    assert body["business_day"] == 5
    assert body["count_saturdays"] is True
    assert body["holiday_calendar"] == "BR"
    assert body["match_keywords"] == "Transferência Recebida"
    assert body["next_due_date"] == "2026-10-06"

    response = await client.post(
        "/api/recurring-transactions",
        json={
            "description": "Semanal",
            "amount": 10,
            "type": "debit",
            "frequency": "weekly",
            "business_day": 5,
            "start_date": "2026-10-01",
            "account_id": str(test_account.id),
        },
        headers=auth_headers,
    )
    assert response.status_code in (400, 422)


@pytest.mark.asyncio
async def test_api_lists_holiday_calendars_with_subdivision_names(client, auth_headers):
    response = await client.get("/api/recurring-transactions/holiday-calendars", headers=auth_headers)
    assert response.status_code == 200, response.text
    by_country = {row["country"]: row["subdivisions"] for row in response.json()}
    assert {"code": "SP", "name": "São Paulo"} in by_country["BR"]
    assert "US" in by_country and "PT" in by_country


@pytest.mark.asyncio
async def test_editing_the_amount_restamps_the_primary_amount(
    session, test_user, test_workspace, account
):
    """A primary amount stamped for the old figure must not survive an edit."""
    bill = await _make_bill(
        session, test_workspace, test_user, account,
        description="Streaming", amount=Decimal("50.00"), type="debit",
    )
    bill.amount_primary = Decimal("50.00")
    await session.commit()
    updated = await update_recurring_transaction(
        session, bill.id, test_workspace.id,
        RecurringTransactionUpdate(amount=Decimal("21.69")),
    )
    assert updated.amount_primary != Decimal("50.00")
