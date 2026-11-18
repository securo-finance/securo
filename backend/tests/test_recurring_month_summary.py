"""Month totals on the Recurring page (#532)."""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.account import Account
from app.models.category import Category
from app.models.fx_rate import FxRate
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.models.workspace import Workspace
from app.services import reconciliation_policy, reconciliation_rule_service
from app.services.recurring_transaction_service import generate_pending, get_month_summary


def _category(session, user, workspace, name, **flags):
    cat = Category(
        id=uuid.uuid4(), user_id=user.id, workspace_id=workspace.id,
        name=name, icon="x", color="#000000", **flags,
    )
    session.add(cat)
    return cat


def _recurring(session, user, workspace, account, description, amount, kind, frequency,
               start, **fields):
    next_occurrence = fields.pop("next_occurrence_override", start)
    rec = RecurringTransaction(
        id=uuid.uuid4(), user_id=user.id, workspace_id=workspace.id,
        account_id=account.id if account else None, description=description,
        amount=Decimal(amount), currency=fields.pop("currency", "BRL"), type=kind,
        frequency=frequency, start_date=start, next_occurrence=next_occurrence, **fields,
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
async def test_a_schedule_completed_by_generation_keeps_its_last_month(
    session, test_user, test_workspace, test_account,
):
    """Generating the last occurrence turns ``is_active`` off; December keeps it.

    A paused definition with the same dates still stays out.
    """
    _recurring(session, test_user, test_workspace, test_account,
               "Last debit", "500", "debit", "monthly", date(2026, 1, 10),
               end_date=date(2026, 12, 10), auto_generate=True)
    _recurring(session, test_user, test_workspace, test_account,
               "Paused", "30", "debit", "monthly", date(2026, 1, 10),
               end_date=date(2026, 12, 10), is_active=False)
    await session.commit()

    before = await _summary(session, test_workspace, date(2026, 12, 1))
    assert before.expenses == Decimal("500.00")

    await generate_pending(
        session, test_user.id, up_to=date(2026, 12, 31), workspace_id=test_workspace.id,
    )
    completed = (await session.execute(
        select(RecurringTransaction).where(RecurringTransaction.description == "Last debit")
    )).scalar_one()
    assert completed.is_active is False

    after = await _summary(session, test_workspace, date(2026, 12, 1))
    assert after.expenses == Decimal("500.00")
    assert (await _summary(session, test_workspace, date(2027, 1, 1))).expenses == Decimal("0")


@pytest.mark.asyncio
async def test_a_paused_schedule_with_an_edited_end_date_stays_out(
    session, test_user, test_workspace, test_account,
):
    """Paused before December's charge, then given an end date: December is empty.

    The pointer already sits past the new end date, as on a completed schedule,
    but nothing was charged in December.
    """
    _recurring(session, test_user, test_workspace, test_account,
               "Paused", "30", "debit", "monthly", date(2026, 1, 10),
               end_date=date(2026, 12, 31), next_occurrence_override=date(2027, 1, 10),
               is_active=False)
    await session.commit()

    assert (await _summary(session, test_workspace, date(2026, 12, 1))).expenses == Decimal("0")


@pytest.mark.asyncio
async def test_a_schedule_paused_after_its_charge_keeps_that_month(
    session, test_user, test_workspace, test_account,
):
    """Paused after December's charge was linked: December keeps that one charge."""
    rec = _recurring(session, test_user, test_workspace, test_account,
                     "Paused late", "30", "debit", "weekly", date(2026, 11, 6),
                     next_occurrence_override=date(2026, 12, 11), is_active=False)
    session.add(Transaction(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        account_id=test_account.id, description="Paused late", amount=Decimal("30"),
        currency="BRL", date=date(2026, 12, 4), type="debit", source="recurring",
        status="posted", recurring_transaction_id=rec.id,
    ))
    await session.commit()

    assert (await _summary(session, test_workspace, date(2026, 12, 1))).expenses == Decimal("30.00")


@pytest.mark.asyncio
async def test_a_completed_schedule_keeps_its_month_when_paid_the_day_before(
    session, test_user, test_workspace, test_account,
):
    """December 1's last occurrence, paid by a November 30 charge, stays in December.

    Generation links the charge and completes the definition; the charge
    belongs to the December occurrence, not to November.
    """
    _recurring(session, test_user, test_workspace, test_account,
               "Rent", "500", "debit", "monthly", date(2026, 12, 1),
               end_date=date(2026, 12, 1), auto_generate=True)
    charge = Transaction(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        account_id=test_account.id, description="Rent", amount=Decimal("500"),
        currency="BRL", date=date(2026, 11, 30), type="debit", source="manual",
        status="posted",
    )
    session.add(charge)
    await session.commit()

    await generate_pending(
        session, test_user.id, up_to=date(2026, 12, 31), workspace_id=test_workspace.id,
    )
    await session.refresh(charge)
    rent = (await session.execute(
        select(RecurringTransaction).where(RecurringTransaction.description == "Rent")
    )).scalar_one()
    assert charge.recurring_transaction_id == rent.id
    assert rent.is_active is False

    assert (await _summary(session, test_workspace, date(2026, 12, 1))).expenses == Decimal("500.00")
    assert (await _summary(session, test_workspace, date(2026, 11, 1))).expenses == Decimal("0")


@pytest.mark.asyncio
async def test_a_paused_schedule_keeps_a_late_charge_on_the_occurrence_it_paid(
    session, test_user, test_workspace, test_account,
):
    """A slow bank's December 20 charge paid December 1, not January 1.

    With a 20-day window both ways, generation links it to December 1, the
    first occurrence it reached. January 1 is nearer (12 days against 19), but
    generation never reached it, so the paused definition keeps the charge in
    December.
    """
    await reconciliation_rule_service.upsert_override(
        session, test_workspace.id, test_user.id,
        reconciliation_policy.MATCH_RECURRING["node"], "same_account_exact",
        {"when": {"date": {"before_days": 20, "after_days": 20}}},
    )
    _recurring(session, test_user, test_workspace, test_account,
               "Gym", "80", "debit", "monthly", date(2026, 12, 1), auto_generate=True)
    charge = Transaction(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        account_id=test_account.id, description="Gym", amount=Decimal("80"),
        currency="BRL", date=date(2026, 12, 20), type="debit", source="manual",
        status="posted",
    )
    session.add(charge)
    await session.commit()

    await generate_pending(
        session, test_user.id, up_to=date(2026, 12, 20), workspace_id=test_workspace.id,
    )
    await session.refresh(charge)
    gym = (await session.execute(
        select(RecurringTransaction).where(RecurringTransaction.description == "Gym")
    )).scalar_one()
    assert charge.recurring_transaction_id == gym.id
    gym.is_active = False
    await session.commit()

    assert (await _summary(session, test_workspace, date(2026, 12, 1))).expenses == Decimal("80.00")
    assert (await _summary(session, test_workspace, date(2027, 1, 1))).expenses == Decimal("0")


@pytest.mark.asyncio
async def test_a_paused_schedule_skipping_its_first_keeps_the_charge_where_generation_put_it(
    session, test_user, test_workspace, test_account,
):
    """Created skipping December 1, the December 20 charge paid January 1.

    Generation starts at the pointer, January 1, and links the charge there.
    December 1 was never generated, so after the pause the charge stays in
    January rather than moving to the nearer-looking start date.
    """
    await reconciliation_rule_service.upsert_override(
        session, test_workspace.id, test_user.id,
        reconciliation_policy.MATCH_RECURRING["node"], "same_account_exact",
        {"when": {"date": {"before_days": 20, "after_days": 20}}},
    )
    _recurring(session, test_user, test_workspace, test_account,
               "Gym", "80", "debit", "monthly", date(2026, 12, 1),
               next_occurrence_override=date(2027, 1, 1), auto_generate=True)
    charge = Transaction(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        account_id=test_account.id, description="Gym", amount=Decimal("80"),
        currency="BRL", date=date(2026, 12, 20), type="debit", source="manual",
        status="posted",
    )
    session.add(charge)
    await session.commit()

    await generate_pending(
        session, test_user.id, up_to=date(2027, 1, 1), workspace_id=test_workspace.id,
    )
    await session.refresh(charge)
    gym = (await session.execute(
        select(RecurringTransaction).where(RecurringTransaction.description == "Gym")
    )).scalar_one()
    assert charge.recurring_transaction_id == gym.id
    gym.is_active = False
    await session.commit()

    assert (await _summary(session, test_workspace, date(2026, 12, 1))).expenses == Decimal("0")
    assert (await _summary(session, test_workspace, date(2027, 1, 1))).expenses == Decimal("80.00")


@pytest.mark.asyncio
async def test_generation_never_links_a_charge_from_another_workspace(
    session, test_user, test_workspace, test_account,
):
    """Sync can move an account, and its charges, to another workspace.

    Generation then writes this bill's own placeholder instead of stamping
    the other workspace's charge with a link to it.
    """
    other = Workspace(id=uuid.uuid4(), name="Other", created_by_user_id=test_user.id)
    session.add(other)
    rent = _recurring(session, test_user, test_workspace, test_account,
                      "Rent", "500", "debit", "monthly", date(2027, 1, 1), auto_generate=True)
    moved_charge = Transaction(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=other.id,
        account_id=test_account.id, description="Rent", amount=Decimal("500"),
        currency="BRL", date=date(2027, 1, 1), type="debit", source="sync",
        status="posted",
    )
    session.add(moved_charge)
    await session.commit()

    await generate_pending(
        session, test_user.id, up_to=date(2027, 1, 1), workspace_id=test_workspace.id,
    )
    await session.refresh(moved_charge)
    assert moved_charge.recurring_transaction_id is None
    placeholder = (await session.execute(
        select(Transaction).where(Transaction.recurring_transaction_id == rent.id)
    )).scalar_one()
    assert placeholder.workspace_id == test_workspace.id


@pytest.mark.asyncio
async def test_a_charge_moved_to_another_workspace_stays_out(
    session, test_user, test_workspace, test_account,
):
    """A linked charge whose account moved away no longer counts here."""
    other = Workspace(
        id=uuid.uuid4(), name="Other", kind="personal", default_currency="BRL", locale="en",
    )
    session.add(other)
    rec = _recurring(session, test_user, test_workspace, test_account,
                     "Paused", "30", "debit", "monthly", date(2026, 11, 4),
                     next_occurrence_override=date(2027, 1, 4), is_active=False)
    session.add(Transaction(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=other.id,
        account_id=test_account.id, description="Paused", amount=Decimal("30"),
        currency="BRL", date=date(2026, 12, 4), type="debit", source="recurring",
        status="posted", recurring_transaction_id=rec.id,
    ))
    await session.commit()

    assert (await _summary(session, test_workspace, date(2026, 12, 1))).expenses == Decimal("0")


@pytest.mark.asyncio
async def test_a_paused_schedule_after_a_frequency_edit_stays_out(
    session, test_user, test_workspace, test_account,
):
    """A paused monthly turned quarterly moves its pointer past the end date.

    October falls on the new schedule but was never charged, so it stays out.
    """
    _recurring(session, test_user, test_workspace, test_account,
               "Paused", "500", "debit", "quarterly", date(2026, 1, 10),
               end_date=date(2026, 12, 10), next_occurrence_override=date(2027, 1, 10),
               is_active=False)
    await session.commit()

    assert (await _summary(session, test_workspace, date(2026, 10, 1))).expenses == Decimal("0")


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
async def test_two_foreign_currencies_each_convert_at_their_own_rate(
    session, test_user, test_workspace, test_account,
):
    """100 USD at 5 plus 50 EUR at 10 is 1000 BRL, whatever the stamps say.

    Rates are stored USD-based, so EUR -> BRL is the cross rate 5 / 0.5.
    """
    for quote, rate in (("BRL", "5.0"), ("EUR", "0.5")):
        session.add(FxRate(
            id=uuid.uuid4(), base_currency="USD", quote_currency=quote,
            date=date(2026, 10, 1), rate=Decimal(rate), source="test",
        ))
    _recurring(session, test_user, test_workspace, test_account,
               "Cloud", "100", "debit", "monthly", date(2026, 1, 1),
               currency="USD", amount_primary=Decimal("100.00"))
    _recurring(session, test_user, test_workspace, test_account,
               "Streaming", "50", "debit", "monthly", date(2026, 1, 1),
               currency="EUR", amount_primary=Decimal("54.00"))
    _recurring(session, test_user, test_workspace, test_account,
               "Rent", "1000", "debit", "monthly", date(2026, 1, 1))
    await session.commit()

    summary = await _summary(session, test_workspace, date(2026, 10, 15))
    assert summary.expenses == Decimal("2000.00")  # 500 + 500 + 1000
    assert summary.unconverted_count == 0


@pytest.mark.asyncio
async def test_a_cross_rate_is_rounded_once_at_the_end(
    session, test_user, test_workspace, test_account,
):
    """EUR 1.01 at a EUR to BRL rate of 1.499999999975 is BRL 1.51.

    Rounding the cross rate first would read 1.5 and give 1.52.
    """
    for quote, rate in (("BRL", "5.9999999999"), ("EUR", "4")):
        session.add(FxRate(
            id=uuid.uuid4(), base_currency="USD", quote_currency=quote,
            date=date(2026, 10, 1), rate=Decimal(rate), source="test",
        ))
    _recurring(session, test_user, test_workspace, test_account,
               "Small", "1.01", "debit", "monthly", date(2026, 1, 1), currency="EUR")
    await session.commit()

    assert (await _summary(session, test_workspace, date(2026, 10, 1))).expenses == Decimal("1.51")


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


@pytest.mark.asyncio
@pytest.mark.parametrize("month", ["9999-12-01", "9998-12-01", "0002-01-01"])
async def test_summary_route_rejects_a_month_out_of_range(
    client, auth_headers, session, test_user, test_workspace, test_account, month,
):
    """Months the summary would read past the calendar from are a 422, not a 500."""
    await reconciliation_rule_service.upsert_override(
        session, test_workspace.id, test_user.id,
        reconciliation_policy.MATCH_RECURRING["node"], "same_account_exact",
        {"when": {"date": {"before_days": 200, "after_days": 200}}},
    )
    _recurring(session, test_user, test_workspace, test_account,
               "Far", "10", "debit", "yearly", date(9998, 1, 1))
    _recurring(session, test_user, test_workspace, test_account,
               "Old", "10", "debit", "monthly", date(1, 1, 1), is_active=False)
    await session.commit()

    resp = await client.get(
        "/api/recurring-transactions/summary", params={"month": month},
        headers=auth_headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_summary_route_reads_the_last_supported_month(client, auth_headers):
    resp = await client.get(
        "/api/recurring-transactions/summary", params={"month": "9989-12-01"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
