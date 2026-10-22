"""Tests for recurring-bill ↔ transaction matching (issue #116)."""
import uuid
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.transaction import Transaction
from app.models.workspace import Workspace, WorkspaceMember
from app.schemas.recurring_transaction import RecurringTransactionCreate
from app.services import (
    reconciliation_policy,
    reconciliation_rule_service,
    recurring_match_service as rms,
)
from app.services.text_similarity import token_overlap
from app.services.recurring_transaction_service import (
    create_recurring_transaction,
    generate_pending,
)


@pytest_asyncio.fixture
async def account(session: AsyncSession, test_user, test_workspace) -> Account:
    acc = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="MatchAcc",
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
        description=overrides.pop("description", "Netflix Subscription"),
        amount=overrides.pop("amount", Decimal("39.90")),
        currency=overrides.pop("currency", "BRL"),
        type=overrides.pop("type", "debit"),
        frequency=overrides.pop("frequency", "monthly"),
        start_date=overrides.pop("start_date", date(2025, 1, 10)),
        account_id=account.id,
        **overrides,
    )
    return await create_recurring_transaction(session, test_workspace.id, test_user.id, data)


async def _add_tx(session, test_user, test_workspace, account, **kw):
    tx = Transaction(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=account.id,
        description=kw.get("description", "NETFLIX SUBSCRIPTION"),
        amount=kw.get("amount", Decimal("39.90")),
        currency="BRL",
        date=kw["date"],
        type=kw.get("type", "debit"),
        source=kw.get("source", "sync"),
        status=kw.get("status", "posted"),
        external_id=kw.get("external_id", "ext-1"),
        recurring_transaction_id=kw.get("recurring_transaction_id"),
    )
    session.add(tx)
    await session.commit()
    await session.refresh(tx)
    return tx


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_description_similarity():
    """One implementation now, shared by the matching engine and the
    bank-sync fuzzy merge. The bar it sets did not move."""
    assert token_overlap("Netflix Sub", "netflix sub") == 1.0
    assert token_overlap("Netflix", "Spotify") == 0.0
    assert token_overlap(None, "x") == 0.0


def test_match_window():
    """The windows are policy now rather than a function, and they are the
    same windows.

    Only weekly narrows, and the reason is arithmetic rather than taste: a
    weekly bill sits seven days from its neighbours, and the shipped
    window spans eight (three before, five after), so a charge could match
    the wrong occurrence. Every other frequency is far enough apart that
    the shipped window cannot reach the next one, which is why they all
    read the same and why a new frequency needs no entry here unless its
    occurrences fall closer together than eight days.
    """
    from app.services import reconciliation_policy

    def window(frequency: str) -> tuple[int, int]:
        rule = reconciliation_policy.for_recurring(frequency)["strategies"][0]["when"]
        return rule["date"]["before_days"], rule["date"]["after_days"]

    assert window("weekly") == (2, 2)
    assert window("monthly") == (3, 5)
    assert window("yearly") == (3, 5)
    # Fourteen and one hundred and eighty days apart: nothing to narrow.
    assert window("biweekly") == (3, 5)
    assert window("semiannual") == (3, 5)


# ---------------------------------------------------------------------------
# find_bill_for_incoming (sync-first path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_bill_matches_within_window(session, test_user, test_workspace, account):
    bill = await _make_bill(session, test_workspace, test_user, account)
    # next_occurrence = 2025-01-10; charge posts 2 days late.
    match = await rms.find_bill_for_incoming(
        session, test_user.id, account.id, Decimal("39.90"), "BRL", "debit",
        date(2025, 1, 12), "NETFLIX SUBSCRIPTION",
    )
    assert match is not None
    assert match.id == bill.id


@pytest.mark.asyncio
async def test_find_bill_rejects_outside_window(session, test_user, test_workspace, account):
    await _make_bill(session, test_workspace, test_user, account)
    # 10 days after the due date is well outside the monthly window.
    match = await rms.find_bill_for_incoming(
        session, test_user.id, account.id, Decimal("39.90"), "BRL", "debit",
        date(2025, 1, 20), "NETFLIX SUBSCRIPTION",
    )
    assert match is None


@pytest.mark.asyncio
async def test_find_bill_rejects_amount_and_type_mismatch(session, test_user, test_workspace, account):
    await _make_bill(session, test_workspace, test_user, account)
    assert await rms.find_bill_for_incoming(
        session, test_user.id, account.id, Decimal("41.00"), "BRL", "debit",
        date(2025, 1, 10), "NETFLIX SUBSCRIPTION",
    ) is None
    assert await rms.find_bill_for_incoming(
        session, test_user.id, account.id, Decimal("39.90"), "BRL", "credit",
        date(2025, 1, 10), "NETFLIX SUBSCRIPTION",
    ) is None


@pytest.mark.asyncio
async def test_find_bill_rejects_currency_mismatch(session, test_user, test_workspace, account):
    # Bill is in BRL; a same-numeric charge in another currency must not match.
    await _make_bill(session, test_workspace, test_user, account, currency="BRL")
    assert await rms.find_bill_for_incoming(
        session, test_user.id, account.id, Decimal("39.90"), "USD", "debit",
        date(2025, 1, 10), "NETFLIX SUBSCRIPTION",
    ) is None


@pytest.mark.asyncio
async def test_find_bill_rejects_low_similarity(session, test_user, test_workspace, account):
    await _make_bill(session, test_workspace, test_user, account)
    match = await rms.find_bill_for_incoming(
        session, test_user.id, account.id, Decimal("39.90"), "BRL", "debit",
        date(2025, 1, 10), "Amazon Prime",
    )
    assert match is None


@pytest.mark.asyncio
async def test_find_bill_centers_existing_tolerance_on_effective_date(
    session, test_user, test_workspace, account
):
    bill = await _make_bill(
        session,
        test_workspace,
        test_user,
        account,
        start_date=date(2026, 8, 2),
        weekend_adjustment="next_monday",
    )

    match = await rms.find_bill_for_incoming(
        session,
        test_user.id,
        account.id,
        Decimal("39.90"),
        "BRL",
        "debit",
        date(2026, 8, 8),
        "NETFLIX SUBSCRIPTION",
    )
    outside = await rms.find_bill_for_incoming(
        session,
        test_user.id,
        account.id,
        Decimal("39.90"),
        "BRL",
        "debit",
        date(2026, 8, 9),
        "NETFLIX SUBSCRIPTION",
    )

    assert match is not None and match.id == bill.id
    assert outside is None


# ---------------------------------------------------------------------------
# find_placeholder_for_incoming (placeholder-first path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_placeholder_matches_generated_row(session, test_user, test_workspace, account):
    bill = await _make_bill(session, test_workspace, test_user, account)
    placeholder = await _add_tx(
        session, test_user, test_workspace, account,
        date=date(2025, 1, 10), source="recurring", external_id=None,
        recurring_transaction_id=bill.id, description="Netflix Subscription",
    )
    found = await rms.find_placeholder_for_incoming(
        session, account.id, Decimal("39.90"), "BRL", "debit",
        date(2025, 1, 11), "NETFLIX SUBSCRIPTION",
    )
    assert found is not None and found.id == placeholder.id


@pytest.mark.asyncio
async def test_find_placeholder_ignores_already_linked_synced(session, test_user, test_workspace, account):
    bill = await _make_bill(session, test_workspace, test_user, account)
    # A real synced row (has external_id) is not a placeholder to merge into.
    await _add_tx(
        session, test_user, test_workspace, account,
        date=date(2025, 1, 10), source="sync", external_id="ext-9",
        recurring_transaction_id=bill.id,
    )
    found = await rms.find_placeholder_for_incoming(
        session, account.id, Decimal("39.90"), "BRL", "debit",
        date(2025, 1, 10), "NETFLIX SUBSCRIPTION",
    )
    assert found is None


# ---------------------------------------------------------------------------
# advance_past
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_advance_past_moves_pointer(session, test_user, test_workspace, account):
    bill = await _make_bill(session, test_workspace, test_user, account)
    assert bill.next_occurrence == date(2025, 1, 10)
    rms.advance_past(bill, date(2025, 1, 12))
    assert bill.next_occurrence == date(2025, 2, 10)


@pytest.mark.asyncio
async def test_advance_past_moves_quarterly_pointer(session, test_user, test_workspace, account):
    bill = await _make_bill(
        session,
        test_workspace,
        test_user,
        account,
        frequency="quarterly",
        day_of_month=31,
        start_date=date(2025, 1, 31),
    )
    rms.advance_past(bill, date(2025, 1, 31))
    assert bill.next_occurrence == date(2025, 4, 30)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("frequency", "start_date", "day_of_month", "expected_next"),
    [
        ("biweekly", date(2026, 1, 5), None, date(2026, 1, 19)),
        ("semiannual", date(2026, 1, 31), 31, date(2026, 7, 31)),
    ],
    ids=["biweekly", "semiannual"],
)
async def test_advance_past_new_frequencies(
    session,
    test_user,
    test_workspace,
    account,
    frequency,
    start_date,
    day_of_month,
    expected_next,
):
    bill = await _make_bill(
        session,
        test_workspace,
        test_user,
        account,
        frequency=frequency,
        day_of_month=day_of_month,
        start_date=start_date,
    )
    rms.advance_past(bill, start_date)
    assert bill.next_occurrence == expected_next


@pytest.mark.asyncio
async def test_advance_past_deactivates_past_end_date(session, test_user, test_workspace, account):
    bill = await _make_bill(
        session, test_workspace, test_user, account, end_date=date(2025, 1, 31),
    )
    rms.advance_past(bill, date(2025, 1, 10))
    assert bill.next_occurrence == date(2025, 2, 10)
    assert bill.is_active is False


@pytest.mark.asyncio
async def test_advance_past_early_charge_still_advances(session, test_user, test_workspace, account):
    """An early-posted charge (inside the before-window, before next_occurrence)
    must still advance the pointer, or generate_pending would duplicate it."""
    bill = await _make_bill(session, test_workspace, test_user, account)  # next_occ 2025-01-10
    rms.advance_past(bill, date(2025, 1, 8))  # 2 days early
    assert bill.next_occurrence == date(2025, 2, 10)


# ---------------------------------------------------------------------------
# generate_pending integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_pending_links_existing_real_tx(session, test_user, test_workspace, account):
    """A synced charge that already covers the occurrence is linked, not duplicated."""
    bill = await _make_bill(session, test_workspace, test_user, account)
    real = await _add_tx(
        session, test_user, test_workspace, account,
        date=date(2025, 1, 11), source="sync", external_id="bank-1",
    )

    count = await generate_pending(session, test_user.id, up_to=date(2025, 1, 20))

    # No placeholder was written for January; the real charge got linked.
    assert count == 0
    await session.refresh(real)
    assert real.recurring_transaction_id == bill.id

    result = await session.execute(
        select(Transaction).where(Transaction.account_id == account.id)
    )
    assert len(result.scalars().all()) == 1  # only the synced row, no duplicate


@pytest.mark.asyncio
async def test_generate_pending_matches_real_transaction_on_effective_date(
    session, test_user, test_workspace, account
):
    bill = await _make_bill(
        session,
        test_workspace,
        test_user,
        account,
        start_date=date(2026, 8, 1),
        weekend_adjustment="previous_friday",
    )
    real = await _add_tx(
        session,
        test_user,
        test_workspace,
        account,
        date=date(2026, 7, 31),
        source="sync",
        external_id="effective-date",
    )

    count = await generate_pending(session, test_user.id, up_to=date(2026, 7, 31))

    assert count == 0
    await session.refresh(real)
    assert real.recurring_transaction_id == bill.id
    await session.refresh(bill)
    assert bill.next_occurrence == date(2026, 9, 1)


@pytest.mark.asyncio
async def test_generate_pending_stamps_placeholder_link(session, test_user, test_workspace, account):
    bill = await _make_bill(session, test_workspace, test_user, account)
    count = await generate_pending(session, test_user.id, up_to=date(2025, 1, 20))
    assert count == 1
    result = await session.execute(
        select(Transaction).where(Transaction.source == "recurring")
    )
    tx = result.scalar_one()
    assert tx.recurring_transaction_id == bill.id
    # Due occurrences are actuals; forecast lives in the projection layer.
    assert tx.status == "posted"


@pytest.mark.asyncio
async def test_generate_pending_skips_auto_generate_off(session, test_user, test_workspace, account):
    await _make_bill(session, test_workspace, test_user, account, auto_generate=False)
    count = await generate_pending(session, test_user.id, up_to=date(2025, 6, 1))
    assert count == 0
    result = await session.execute(
        select(Transaction).where(Transaction.account_id == account.id)
    )
    assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_generate_pending_include_manual_covers_auto_generate_off(
    session, test_user, test_workspace, account
):
    """The Generate Pending button asks for schedules the hourly task skips."""
    bill = await _make_bill(session, test_workspace, test_user, account, auto_generate=False)
    count = await generate_pending(
        session, test_user.id, up_to=date(2025, 1, 20), include_manual=True
    )
    assert count == 1
    result = await session.execute(
        select(Transaction).where(Transaction.account_id == account.id)
    )
    txs = result.scalars().all()
    assert len(txs) == 1
    assert txs[0].recurring_transaction_id == bill.id
    assert txs[0].date == date(2025, 1, 10)


@pytest.mark.asyncio
async def test_generate_pending_include_manual_links_real_tx_instead_of_duplicating(
    session, test_user, test_workspace, account
):
    bill = await _make_bill(session, test_workspace, test_user, account, auto_generate=False)
    real = await _add_tx(session, test_user, test_workspace, account, date=date(2025, 1, 10))
    count = await generate_pending(
        session, test_user.id, up_to=date(2025, 1, 20), include_manual=True
    )
    assert count == 0
    await session.refresh(real)
    assert real.recurring_transaction_id == bill.id


@pytest.mark.asyncio
async def test_generate_pending_workspace_scope_leaves_other_workspaces_alone(
    session, test_user, test_workspace, account
):
    """Generate Pending is authorized for one workspace, so it must not
    materialize or advance a schedule that lives in another."""
    other_ws = Workspace(
        id=uuid.uuid4(), name="Other", kind="personal",
        created_by_user_id=test_user.id, default_currency="BRL",
    )
    session.add(other_ws)
    await session.flush()
    session.add(
        WorkspaceMember(
            id=uuid.uuid4(), workspace_id=other_ws.id, user_id=test_user.id, role="owner",
        )
    )
    other_account = Account(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=other_ws.id,
        name="OtherAcc", type="checking", balance=Decimal("0"), currency="BRL",
    )
    session.add(other_account)
    await session.commit()

    await _make_bill(session, test_workspace, test_user, account, auto_generate=False)
    other_bill = await _make_bill(
        session, other_ws, test_user, other_account,
        description="Gym Membership", auto_generate=False,
    )

    count = await generate_pending(
        session,
        test_user.id,
        up_to=date(2025, 1, 20),
        include_manual=True,
        workspace_id=test_workspace.id,
    )

    assert count == 1
    result = await session.execute(
        select(Transaction).where(Transaction.account_id == other_account.id)
    )
    assert result.scalars().all() == []
    await session.refresh(other_bill)
    assert other_bill.next_occurrence == date(2025, 1, 10)


@pytest.mark.asyncio
async def test_generate_pending_description_mismatch_creates_placeholder(
    session, test_user, test_workspace, account
):
    """A real charge with the same amount/type/date but an unrelated description
    must NOT be swallowed as the bill's occurrence; a placeholder is written and
    the real charge is left untouched."""
    bill = await _make_bill(session, test_workspace, test_user, account)
    unrelated = await _add_tx(
        session, test_user, test_workspace, account,
        date=date(2025, 1, 10), source="sync", external_id="bank-x",
        description="Amazon Marketplace",
    )
    count = await generate_pending(session, test_user.id, up_to=date(2025, 1, 20))
    assert count == 1  # placeholder created, real charge not reused
    await session.refresh(unrelated)
    assert unrelated.recurring_transaction_id is None
    result = await session.execute(
        select(Transaction).where(Transaction.source == "recurring")
    )
    ph = result.scalar_one()
    assert ph.recurring_transaction_id == bill.id


@pytest.mark.asyncio
async def test_find_bill_weekly_window_boundary(session, test_user, test_workspace, account):
    await _make_bill(
        session, test_workspace, test_user, account,
        description="Weekly Gym", frequency="weekly", start_date=date(2025, 1, 6),
    )
    # weekly window is (2, 2): 2 days late matches, 3 days late does not.
    assert await rms.find_bill_for_incoming(
        session, test_user.id, account.id, Decimal("39.90"), "BRL", "debit",
        date(2025, 1, 8), "Weekly Gym",
    ) is not None
    assert await rms.find_bill_for_incoming(
        session, test_user.id, account.id, Decimal("39.90"), "BRL", "debit",
        date(2025, 1, 9), "Weekly Gym",
    ) is None


@pytest.mark.asyncio
async def test_find_real_tx_ignores_already_linked(session, test_user, test_workspace, account):
    bill = await _make_bill(session, test_workspace, test_user, account)
    await _add_tx(
        session, test_user, test_workspace, account,
        date=date(2025, 1, 10), source="sync", external_id="bank-1",
        recurring_transaction_id=bill.id,  # already linked
    )
    found = await rms.find_real_tx_for_occurrence(session, bill, date(2025, 1, 10))
    assert found is None


# ---------------------------------------------------------------------------
# Unlink escape hatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unlink_recurring_clears_link(session, test_user, test_workspace, account):
    from app.services import transaction_service

    bill = await _make_bill(session, test_workspace, test_user, account)
    tx = await _add_tx(
        session, test_user, test_workspace, account,
        date=date(2025, 1, 10), source="sync", external_id="bank-1",
        recurring_transaction_id=bill.id,
    )
    result = await transaction_service.unlink_recurring_transaction(
        session, tx.id, test_workspace.id
    )
    assert result is not None
    assert result.recurring_transaction_id is None
    await session.refresh(tx)
    assert tx.recurring_transaction_id is None


@pytest.mark.asyncio
async def test_unlink_recurring_noop_when_not_linked(session, test_user, test_workspace, account):
    from app.services import transaction_service

    tx = await _add_tx(
        session, test_user, test_workspace, account,
        date=date(2025, 1, 10), source="sync", external_id="bank-2",
    )
    result = await transaction_service.unlink_recurring_transaction(
        session, tx.id, test_workspace.id
    )
    assert result is None  # nothing to unlink → 404 at the API layer


# ---------------------------------------------------------------------------
# A workspace's amount rule (issue #1073)
# ---------------------------------------------------------------------------


async def _allow_tolerance(session, test_workspace, test_user, **config):
    """A workspace that lets a bill match a charge within 5%, and link on it."""
    await reconciliation_rule_service.upsert_override(
        session,
        test_workspace.id,
        test_user.id,
        reconciliation_policy.MATCH_RECURRING["node"],
        "same_account_exact",
        {
            "when": {
                "amount": {"match": "tolerance", "percent": "5"},
                "unique_candidate": True,
            },
            **config,
        },
    )


@pytest.mark.asyncio
async def test_default_rule_still_needs_the_exact_amount(
    session, test_user, test_workspace, account
):
    """A charge a few cents off the bill is still a different amount when the
    workspace has not said otherwise: nothing links."""
    bill = await _make_bill(
        session, test_workspace, test_user, account, amount=Decimal("18.43")
    )
    await _add_tx(
        session, test_user, test_workspace, account,
        date=date(2025, 1, 10), source="recurring", external_id=None,
        recurring_transaction_id=bill.id, description="Netflix Subscription",
        amount=Decimal("18.43"),
    )

    assert await rms.find_bill_for_incoming(
        session, test_user.id, account.id, Decimal("18.71"), "BRL", "debit",
        date(2025, 1, 11), "NETFLIX SUBSCRIPTION",
    ) is None
    assert await rms.find_placeholder_for_incoming(
        session, account.id, Decimal("18.71"), "BRL", "debit",
        date(2025, 1, 11), "NETFLIX SUBSCRIPTION",
    ) is None


@pytest.mark.asyncio
async def test_find_bill_honours_the_workspace_tolerance(
    session, test_user, test_workspace, account
):
    bill = await _make_bill(
        session, test_workspace, test_user, account, amount=Decimal("18.43")
    )
    await _allow_tolerance(session, test_workspace, test_user)

    match = await rms.find_bill_for_incoming(
        session, test_user.id, account.id, Decimal("18.71"), "BRL", "debit",
        date(2025, 1, 11), "NETFLIX SUBSCRIPTION",
    )
    assert match is not None and match.id == bill.id

    # Past the tolerance it is a different charge again.
    assert await rms.find_bill_for_incoming(
        session, test_user.id, account.id, Decimal("20.00"), "BRL", "debit",
        date(2025, 1, 11), "NETFLIX SUBSCRIPTION",
    ) is None


@pytest.mark.asyncio
async def test_find_bill_refuses_to_guess_between_bills_within_tolerance(
    session, test_user, test_workspace, account
):
    """The rule asks for a single candidate, and two bills fit."""
    await _make_bill(session, test_workspace, test_user, account, amount=Decimal("18.43"))
    await _make_bill(
        session, test_workspace, test_user, account,
        amount=Decimal("18.60"), description="Netflix Subscription Family",
    )
    await _allow_tolerance(session, test_workspace, test_user)

    assert await rms.find_bill_for_incoming(
        session, test_user.id, account.id, Decimal("18.71"), "BRL", "debit",
        date(2025, 1, 11), "NETFLIX SUBSCRIPTION",
    ) is None


@pytest.mark.asyncio
async def test_find_bill_does_not_link_under_a_suggest_outcome(
    session, test_user, test_workspace, account
):
    await _make_bill(session, test_workspace, test_user, account, amount=Decimal("18.43"))
    await _allow_tolerance(session, test_workspace, test_user, outcome="suggest")

    assert await rms.find_bill_for_incoming(
        session, test_user.id, account.id, Decimal("18.71"), "BRL", "debit",
        date(2025, 1, 11), "NETFLIX SUBSCRIPTION",
    ) is None


@pytest.mark.asyncio
async def test_find_placeholder_honours_the_workspace_tolerance(
    session, test_user, test_workspace, account
):
    bill = await _make_bill(
        session, test_workspace, test_user, account, amount=Decimal("18.43")
    )
    placeholder = await _add_tx(
        session, test_user, test_workspace, account,
        date=date(2025, 1, 10), source="recurring", external_id=None,
        recurring_transaction_id=bill.id, description="Netflix Subscription",
        amount=Decimal("18.43"),
    )
    await _allow_tolerance(session, test_workspace, test_user)

    found = await rms.find_placeholder_for_incoming(
        session, account.id, Decimal("18.71"), "BRL", "debit",
        date(2025, 1, 11), "NETFLIX SUBSCRIPTION",
    )
    assert found is not None and found.id == placeholder.id


@pytest.mark.asyncio
async def test_find_placeholder_reads_the_recurring_window_from_the_charge(
    session, test_user, test_workspace, account
):
    """The recurring rule allows a charge to post late (five days) more than
    early (three), measured from the occurrence: the placeholder."""
    bill = await _make_bill(
        session, test_workspace, test_user, account, amount=Decimal("18.43")
    )
    await _add_tx(
        session, test_user, test_workspace, account,
        date=date(2025, 1, 10), source="recurring", external_id=None,
        recurring_transaction_id=bill.id, description="Netflix Subscription",
        amount=Decimal("18.43"),
    )
    await _allow_tolerance(session, test_workspace, test_user)

    late = await rms.find_placeholder_for_incoming(
        session, account.id, Decimal("18.71"), "BRL", "debit",
        date(2025, 1, 14), "NETFLIX SUBSCRIPTION",
    )
    early = await rms.find_placeholder_for_incoming(
        session, account.id, Decimal("18.71"), "BRL", "debit",
        date(2025, 1, 6), "NETFLIX SUBSCRIPTION",
    )
    assert late is not None
    assert early is None


@pytest.mark.asyncio
async def test_find_bill_takes_the_first_rule_that_matches_over_a_better_score(
    session, test_user, test_workspace, account
):
    """Rules are ordered and the first to match wins. The exact bill matches
    under the shipped rule; the other only under the workspace's own looser
    rule, with a better description, and must not overtake it."""
    exact = await _make_bill(
        session, test_workspace, test_user, account,
        amount=Decimal("18.71"), description="Netflix Subscription Family",
    )
    await _make_bill(
        session, test_workspace, test_user, account, amount=Decimal("18.43")
    )
    await reconciliation_rule_service.create_custom(
        session, test_workspace.id, test_user.id,
        reconciliation_policy.MATCH_RECURRING["node"], "Within five percent",
        {
            "outcome": "link",
            "trigger": "money_arrives",
            "when": {
                "counterparty": "any",
                "same_account": True,
                "amount": {"match": "tolerance", "percent": "5"},
                "date": {"before_days": 3, "after_days": 5},
                "currency": {"conversion": "reject"},
                "description_similarity": {"min": "0.6"},
                "unique_candidate": True,
            },
        },
    )

    match = await rms.find_bill_for_incoming(
        session, test_user.id, account.id, Decimal("18.71"), "BRL", "debit",
        date(2025, 1, 11), "NETFLIX SUBSCRIPTION",
    )
    assert match is not None and match.id == exact.id


@pytest.mark.asyncio
async def test_generate_pending_links_a_charge_within_tolerance(
    session, test_user, test_workspace, account
):
    bill = await _make_bill(
        session, test_workspace, test_user, account, amount=Decimal("18.43")
    )
    await _allow_tolerance(session, test_workspace, test_user)
    real = await _add_tx(
        session, test_user, test_workspace, account,
        date=date(2025, 1, 11), source="sync", external_id="bank-1",
        amount=Decimal("18.71"),
    )

    count = await generate_pending(session, test_user.id, up_to=date(2025, 1, 20))

    assert count == 0
    await session.refresh(real)
    assert real.recurring_transaction_id == bill.id
    result = await session.execute(
        select(Transaction).where(Transaction.account_id == account.id)
    )
    assert len(result.scalars().all()) == 1


# ---------------------------------------------------------------------------
# A workspace's date window
# ---------------------------------------------------------------------------


async def _widen_window(
    session, test_workspace, test_user, *, before_days=3, after_days=10
):
    """A workspace whose bank is slow: a charge may post up to `after_days` late."""
    await reconciliation_rule_service.upsert_override(
        session,
        test_workspace.id,
        test_user.id,
        reconciliation_policy.MATCH_RECURRING["node"],
        "same_account_exact",
        {"when": {"date": {"before_days": before_days, "after_days": after_days}}},
    )


async def _late_placeholder(session, test_user, test_workspace, account):
    bill = await _make_bill(
        session, test_workspace, test_user, account, amount=Decimal("18.43")
    )
    return await _add_tx(
        session, test_user, test_workspace, account,
        date=date(2025, 1, 10), source="recurring", external_id=None,
        recurring_transaction_id=bill.id, description="Netflix Subscription",
        amount=Decimal("18.43"),
    )


@pytest.mark.asyncio
async def test_find_placeholder_reaches_as_far_as_the_workspace_window(
    session, test_user, test_workspace, account
):
    placeholder = await _late_placeholder(session, test_user, test_workspace, account)
    await _widen_window(session, test_workspace, test_user)

    found = await rms.find_placeholder_for_incoming(
        session, account.id, Decimal("18.43"), "BRL", "debit",
        date(2025, 1, 18), "NETFLIX SUBSCRIPTION",
    )
    assert found is not None and found.id == placeholder.id


@pytest.mark.asyncio
async def test_find_placeholder_default_window_still_stops_at_five_days(
    session, test_user, test_workspace, account
):
    await _late_placeholder(session, test_user, test_workspace, account)

    found = await rms.find_placeholder_for_incoming(
        session, account.id, Decimal("18.43"), "BRL", "debit",
        date(2025, 1, 18), "NETFLIX SUBSCRIPTION",
    )
    assert found is None


@pytest.mark.asyncio
async def test_generate_pending_links_a_charge_inside_a_widened_window(
    session, test_user, test_workspace, account
):
    bill = await _make_bill(
        session, test_workspace, test_user, account, amount=Decimal("18.43")
    )
    await _widen_window(session, test_workspace, test_user)
    real = await _add_tx(
        session, test_user, test_workspace, account,
        date=date(2025, 1, 18), source="sync", external_id="bank-1",
        amount=Decimal("18.43"),
    )

    count = await generate_pending(session, test_user.id, up_to=date(2025, 1, 20))

    assert count == 0
    await session.refresh(real)
    assert real.recurring_transaction_id == bill.id
    result = await session.execute(
        select(Transaction).where(Transaction.account_id == account.id)
    )
    assert len(result.scalars().all()) == 1


@pytest.mark.asyncio
async def test_generate_pending_default_window_does_not_link_a_late_charge(
    session, test_user, test_workspace, account
):
    await _make_bill(
        session, test_workspace, test_user, account, amount=Decimal("18.43")
    )
    real = await _add_tx(
        session, test_user, test_workspace, account,
        date=date(2025, 1, 18), source="sync", external_id="bank-1",
        amount=Decimal("18.43"),
    )

    count = await generate_pending(session, test_user.id, up_to=date(2025, 1, 20))

    assert count == 1
    await session.refresh(real)
    assert real.recurring_transaction_id is None


@pytest.mark.asyncio
async def test_find_placeholder_prefers_the_nearest_of_two_tied_placeholders(
    session, test_user, test_workspace, account
):
    bill = await _make_bill(
        session, test_workspace, test_user, account, amount=Decimal("18.43")
    )
    await _widen_window(session, test_workspace, test_user, after_days=40)
    # The farther one goes in first, so insertion order would pick it.
    far = await _add_tx(
        session, test_user, test_workspace, account,
        date=date(2025, 1, 10), source="recurring", external_id=None,
        recurring_transaction_id=bill.id, description="Netflix Subscription",
        amount=Decimal("18.43"),
    )
    near = await _add_tx(
        session, test_user, test_workspace, account,
        date=date(2025, 2, 10), source="recurring", external_id=None,
        recurring_transaction_id=bill.id, description="Netflix Subscription",
        amount=Decimal("18.43"),
    )

    found = await rms.find_placeholder_for_incoming(
        session, account.id, Decimal("18.43"), "BRL", "debit",
        date(2025, 2, 18), "NETFLIX SUBSCRIPTION",
    )
    assert found is not None and found.id == near.id
    assert found.id != far.id


@pytest.mark.asyncio
async def test_find_real_tx_prefers_the_nearest_of_two_tied_charges(
    session, test_user, test_workspace, account
):
    bill = await _make_bill(
        session, test_workspace, test_user, account, amount=Decimal("18.43")
    )
    await _widen_window(session, test_workspace, test_user, after_days=40)
    # The farther one goes in first, so insertion order would pick it.
    far = await _add_tx(
        session, test_user, test_workspace, account,
        date=date(2025, 3, 12), source="sync", external_id="bank-far",
        amount=Decimal("18.43"),
    )
    near = await _add_tx(
        session, test_user, test_workspace, account,
        date=date(2025, 2, 11), source="sync", external_id="bank-near",
        amount=Decimal("18.43"),
    )

    found = await rms.find_real_tx_for_occurrence(session, bill, date(2025, 2, 10))
    assert found is not None and found.id == near.id
    assert found.id != far.id


@pytest.mark.asyncio
async def test_find_real_tx_breaks_an_equal_distance_tie_towards_the_earlier(
    session, test_user, test_workspace, account
):
    bill = await _make_bill(
        session, test_workspace, test_user, account, amount=Decimal("18.43")
    )
    # The later one goes in first, so insertion order would pick it.
    later = await _add_tx(
        session, test_user, test_workspace, account,
        date=date(2025, 2, 13), source="sync", external_id="bank-later",
        amount=Decimal("18.43"),
    )
    earlier = await _add_tx(
        session, test_user, test_workspace, account,
        date=date(2025, 2, 7), source="sync", external_id="bank-earlier",
        amount=Decimal("18.43"),
    )

    found = await rms.find_real_tx_for_occurrence(session, bill, date(2025, 2, 10))
    assert found is not None and found.id == earlier.id
    assert found.id != later.id


@pytest.mark.asyncio
async def test_widened_window_weekly_clamp_still_wins(
    session, test_user, test_workspace, account
):
    """A guard: the weekly clamp already held before the window followed the policy."""
    bill = await _make_bill(
        session, test_workspace, test_user, account,
        amount=Decimal("18.43"), frequency="weekly",
    )
    await _widen_window(session, test_workspace, test_user, before_days=10, after_days=10)
    await _add_tx(
        session, test_user, test_workspace, account,
        date=date(2025, 1, 14), source="sync", external_id="bank-1",
        amount=Decimal("18.43"),
    )

    assert await rms.find_real_tx_for_occurrence(session, bill, date(2025, 1, 10)) is None


def test_date_reach_reads_only_the_enabled_rules_windows():
    policy = {
        "strategies": [
            {"enabled": True, "when": {"date": {"before_days": 3, "after_days": 5}}},
            {"enabled": False, "when": {"date": {"before_days": 30, "after_days": 40}}},
            {"enabled": True, "when": {}},
        ]
    }

    assert rms._date_reach(policy) == (3, 5)
    assert rms._date_reach({"strategies": []}) == (0, 0)
