"""Edges of the recurring schedule rules found in review of #1242.

Matching under a bill's own policy (tolerance, grace, keywords), the first
occurrence of a business-day schedule, holiday shifts longer than a weekend,
and the month summary of schedules whose first occurrence is not their start.
"""
import uuid
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.schemas.recurring_transaction import RecurringTransactionCreate
from app.services import dashboard_service, reconciliation_rule_service
from app.services import recurring_match_service as rms
from app.services.recurring_transaction_service import (
    create_recurring_transaction,
    effective_date_for,
    generate_pending,
    get_month_summary,
    get_occurrences_in_range,
    grace_days_for,
    nearest_occurrence,
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
    data = RecurringTransactionCreate.model_validate({
        "description": "Aluguel",
        "amount": Decimal("1200.00"),
        "currency": "BRL",
        "type": "debit",
        "frequency": "monthly",
        "start_date": date(2026, 10, 5),
        "account_id": account.id,
        **overrides,
    })
    return await create_recurring_transaction(session, test_workspace.id, test_user.id, data)


async def _add_tx(session, test_user, test_workspace, account, **kw):
    tx = Transaction(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=account.id,
        description=kw.get("description", "Pix enviado"),
        amount=kw.get("amount", Decimal("1200.00")),
        currency="BRL",
        date=kw["date"],
        type=kw.get("type", "debit"),
        source=kw.get("source", "sync"),
        status="posted",
        external_id=kw.get("external_id", str(uuid.uuid4())),
        recurring_transaction_id=kw.get("recurring_transaction_id"),
    )
    session.add(tx)
    await session.commit()
    await session.refresh(tx)
    return tx


def _bill(**fields):
    """A stand-in row for the pure helpers."""
    base = dict(
        frequency="monthly", grace_days=0, amount_tolerance_percent=None,
        match_keywords=None, weekend_adjustment="none", business_day=None,
        count_saturdays=False, holiday_calendar=None, day_of_month=None,
        end_date=None,
    )
    base.update(fields)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------------------
# A bill's policy
# ---------------------------------------------------------------------------


def test_tolerance_keeps_the_workspace_limits_on_the_amount():
    policy = {"strategies": [{
        "id": "capped",
        "when": {
            "amount": {"match": "exact", "min": "10", "max": "100"},
            "date": {"before_days": 3, "after_days": 5},
        },
    }]}
    adapted = rms._bill_policy(policy, _bill(amount_tolerance_percent=Decimal("10")))

    amount = adapted["strategies"][0]["when"]["amount"]
    assert amount == {"match": "tolerance", "percent": "10", "min": "10", "max": "100"}
    assert adapted["strategies"][0]["when"]["unique_candidate"] is True
    # The rule as resolved is left alone.
    assert policy["strategies"][0]["when"]["amount"]["match"] == "exact"


@pytest.mark.parametrize(
    ("frequency", "stored", "applied"),
    [
        ("weekly", 10, 6),
        ("weekly", 3, 3),
        ("biweekly", 15, 13),
        ("monthly", 15, 15),
        ("yearly", 15, 15),
    ],
)
def test_grace_stays_shorter_than_the_period(frequency, stored, applied):
    assert grace_days_for(_bill(frequency=frequency, grace_days=stored)) == applied


def test_an_on_day_charge_of_a_weekly_bill_is_not_taken_for_last_week_paid_late():
    """Weekly bill, grace 10: a charge on the 15th is the 15th's, not the 8th's."""
    bill = _bill(
        frequency="weekly", grace_days=10, start_date=date(2027, 3, 1),
        next_occurrence=date(2027, 3, 8),
    )
    nominal, _ = nearest_occurrence(bill, date(2027, 3, 15))
    assert nominal == date(2027, 3, 15)


@pytest.mark.asyncio
async def test_a_keyword_hit_adds_the_grace_once(
    session, test_user, test_workspace, account, monkeypatch
):
    """Five days to auto-link, five of grace, and a 20-day rule that only
    suggests: the query reaches a charge 13 days late, but no rule may link it."""
    policy = {
        "strategies": [
            {
                "id": "auto",
                "enabled": True,
                "outcome": "link",
                "trigger": "money_arrives",
                "when": {
                    "same_account": True,
                    "amount": {"match": "exact"},
                    "date": {"before_days": 3, "after_days": 5},
                    "description_similarity": {"min": "0.6"},
                },
            },
            {
                "id": "wide",
                "enabled": True,
                "outcome": "suggest",
                "trigger": "money_arrives",
                "when": {
                    "same_account": True,
                    "amount": {"match": "exact"},
                    "date": {"before_days": 3, "after_days": 20},
                },
            },
        ],
        "on_ambiguity": "suggest",
    }

    async def resolve_recurring(*_args, **_kwargs):
        return policy

    monkeypatch.setattr(reconciliation_rule_service, "resolve_recurring", resolve_recurring)
    bill = await _make_bill(
        session, test_workspace, test_user, account,
        day_of_month=5, grace_days=5, match_keywords="Pix enviado", auto_generate=False,
    )
    await _add_tx(
        session, test_user, test_workspace, account,
        description="PIX ENVIADO|PROPRIETARIO", date=date(2026, 10, 18),
    )

    assert await rms.find_real_tx_for_occurrence(session, bill, date(2026, 10, 5)) is None

    # Within the five days plus five of grace it still links.
    near = await _add_tx(
        session, test_user, test_workspace, account,
        description="PIX ENVIADO|PROPRIETARIO", date=date(2026, 10, 15),
    )
    found = await rms.find_real_tx_for_occurrence(session, bill, date(2026, 10, 5))
    assert found is not None and found.id == near.id


@pytest.mark.asyncio
async def test_two_placeholders_a_tolerant_charge_fits_are_left_to_a_person(
    session, test_user, test_workspace, account
):
    """Placeholders of 1200 and 1210 on one day, both within 3% of 1205: the
    charge must not rewrite either of them."""
    for name, amount in (("Aluguel", "1200.00"), ("Garagem", "1210.00")):
        await _make_bill(
            session, test_workspace, test_user, account,
            description=name, amount=Decimal(amount), day_of_month=5,
            match_keywords="Proprietaria", amount_tolerance_percent=Decimal("3"),
        )
    await generate_pending(session, test_user.id, up_to=date(2026, 10, 5))
    placeholders = (await session.execute(
        select(Transaction).where(Transaction.source == "recurring")
    )).scalars().all()
    assert len(placeholders) == 2

    assert await rms.find_placeholder_for_incoming(
        session, account.id, Decimal("1205.00"), "BRL", "debit",
        date(2026, 10, 5), "Pix|Proprietaria",
    ) is None

    # A charge only one of them fits (1240 is within 3% of 1210, not of
    # 1200) still finds it.
    found = await rms.find_placeholder_for_incoming(
        session, account.id, Decimal("1240.00"), "BRL", "debit",
        date(2026, 10, 5), "Pix|Proprietaria",
    )
    assert found is not None and Decimal(found.amount) == Decimal("1210.00")


def test_advance_past_reaches_every_occurrence_nearest_occurrence_can_find():
    start = date(2010, 1, 4)
    bill = _bill(frequency="weekly", start_date=start, next_occurrence=start)
    charge = start + timedelta(weeks=550)

    target, _ = nearest_occurrence(bill, charge)
    assert target == charge
    rms.advance_past(bill, charge)
    assert bill.next_occurrence == charge + timedelta(weeks=1)


# ---------------------------------------------------------------------------
# Schedule dates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_business_day_still_ahead_in_the_start_month_is_the_first_occurrence(
    session, test_user, test_workspace, account
):
    """Starts 10 Feb on the 15th business day: 19 Feb is still ahead."""
    bill = await _make_bill(
        session, test_workspace, test_user, account,
        start_date=date(2027, 2, 10), day_of_month=1, business_day=15,
    )
    assert bill.next_occurrence == date(2027, 2, 1)
    assert effective_date_for(bill, bill.next_occurrence) == date(2027, 2, 19)


def test_a_holiday_shift_longer_than_a_week_stays_in_range():
    """China's Spring Festival moves 28 January 2025 to 5 February."""
    assert get_occurrences_in_range(
        start=date(2025, 1, 28), frequency="monthly", end_date=None,
        range_start=date(2025, 2, 5), range_end=date(2025, 2, 6),
        intended_day=28, weekend_adjustment="next_monday", holiday_calendar="CN",
    ) == [date(2025, 2, 5)]


# ---------------------------------------------------------------------------
# Month summary and projections
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_summary_does_not_count_a_business_day_before_the_start(
    session, test_user, test_workspace, account
):
    """Starts 20 Jan on the first business day: due from February on."""
    bill = await _make_bill(
        session, test_workspace, test_user, account,
        start_date=date(2027, 1, 20), business_day=1,
    )
    assert bill.next_occurrence == date(2027, 2, 20)

    january = await get_month_summary(session, test_workspace.id, date(2027, 1, 1), "BRL")
    february = await get_month_summary(session, test_workspace.id, date(2027, 2, 1), "BRL")
    assert january.expenses == Decimal("0")
    assert february.expenses == Decimal("1200.00")


@pytest.mark.asyncio
async def test_a_bill_moved_into_the_previous_month_by_a_holiday_is_projected_there(
    session, test_user, test_workspace, account
):
    """Starts 4 Mar 2025, on Carnival: paid the Friday before, 28 Feb."""
    bill = await _make_bill(
        session, test_workspace, test_user, account,
        start_date=date(2025, 3, 4), day_of_month=4,
        holiday_calendar="BR", weekend_adjustment="previous_friday",
    )
    assert effective_date_for(bill, bill.next_occurrence) == date(2025, 2, 28)

    february = await get_month_summary(session, test_workspace.id, date(2025, 2, 1), "BRL")
    assert february.expenses == Decimal("1200.00")
    projections = await dashboard_service._get_recurring_projections(
        session, test_workspace.id, date(2025, 2, 1), date(2025, 3, 1),
    )
    assert [p["amount"] for p in projections] == [pytest.approx(1200.0)]


@pytest.mark.asyncio
async def test_a_paused_bill_keeps_a_charge_linked_within_its_grace(
    session, test_user, test_workspace, account
):
    """Due 1 Feb with ten days of grace, paid 13 Feb, then paused: February
    still counts it, though the rule alone reaches five days."""
    bill = RecurringTransaction(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        account_id=account.id, description="Aluguel", amount=Decimal("1200.00"),
        currency="BRL", type="debit", frequency="monthly",
        start_date=date(2027, 1, 1), day_of_month=1, grace_days=10,
        next_occurrence=date(2027, 3, 1), is_active=False,
    )
    session.add(bill)
    await session.commit()
    await _add_tx(
        session, test_user, test_workspace, account,
        date=date(2027, 2, 13), recurring_transaction_id=bill.id,
    )

    february = await get_month_summary(session, test_workspace.id, date(2027, 2, 1), "BRL")
    assert february.expenses == Decimal("1200.00")


# ---------------------------------------------------------------------------
# The whole late window stays shorter than a weekly or biweekly period
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("frequency", "rule_after", "grace", "applied"),
    [
        # The default rules: two days late for a weekly bill, five otherwise.
        ("weekly", 2, 5, 6),
        ("weekly", 2, 3, 5),
        ("biweekly", 5, 9, 13),
        ("biweekly", 5, 4, 9),
        ("monthly", 5, 15, 20),
        # A window the workspace itself set that wide is not narrowed.
        ("weekly", 8, 5, 8),
    ],
)
def test_the_rule_and_the_grace_together_stay_shorter_than_the_period(
    frequency, rule_after, grace, applied
):
    policy = {"strategies": [{
        "id": "auto",
        "when": {"date": {"before_days": 2, "after_days": rule_after}},
    }]}
    adapted = rms._bill_policy(policy, _bill(frequency=frequency, grace_days=grace))

    assert adapted["strategies"][0]["when"]["date"] == {
        "before_days": 2, "after_days": applied,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("frequency", "grace", "period"), [("weekly", 5, 7), ("biweekly", 9, 14)]
)
async def test_an_on_day_charge_is_not_linked_to_the_unpaid_occurrence_before_it(
    session, test_user, test_workspace, account, frequency, grace, period
):
    """With the default rules, an unpaid occurrence must not take the next
    occurrence's on-day charge: the rule's lateness plus the grace would
    otherwise reach exactly one period."""
    bill = await _make_bill(
        session, test_workspace, test_user, account,
        frequency=frequency, start_date=date(2026, 10, 5), grace_days=grace,
        auto_generate=False,
    )
    unpaid = date(2026, 10, 5)
    next_due = unpaid + timedelta(days=period)
    on_day = await _add_tx(
        session, test_user, test_workspace, account,
        description="Aluguel", date=next_due,
    )

    assert await rms.find_real_tx_for_occurrence(session, bill, unpaid) is None
    found = await rms.find_real_tx_for_occurrence(session, bill, next_due)
    assert found is not None and found.id == on_day.id


# ---------------------------------------------------------------------------
# Keywords
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "keywords",
    # An acute accent, padded, a lone combining acute, and two spacing marks.
    ["´", " ´ , ", "́", "´,¨"],
)
def test_a_keyword_folding_empties_matches_nothing(keywords):
    assert not rms.keyword_hit(_bill(match_keywords=keywords), "Compra no mercado")


def test_an_empty_keyword_does_not_hide_a_real_one():
    bill = _bill(match_keywords="´, Transferência Recebida")

    assert rms.keyword_hit(bill, "TRANSFERENCIA RECEBIDA|EMPRESA")
    assert not rms.keyword_hit(bill, "Compra no mercado")
