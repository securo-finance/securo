"""How reimbursement allocations net user-level P&L (#1126, ADR 0031).

The parts returned by `reimbursement_offset_parts` come off the full-row
totals: positive takes off its direction, negative adds to it.
"""

import uuid
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

import pytest

from app.core.app_clock import app_today
from app.models.category import Category
from app.models.transaction import Transaction
from app.models.transaction_category_line import TransactionCategoryLine
from app.schemas.reimbursement import ReimbursementAllocationInput
from app.services import reimbursement_service
from app.services.reimbursement_netting import (
    reimbursement_offset_parts,
    reimbursement_share_fractions,
)

TODAY = app_today()
MONTH = TODAY.replace(day=1)
PREV = (MONTH - timedelta(days=1)).replace(day=1)


@pytest.fixture
def category(session, test_user, test_workspace):
    async def make(name="Food", **flags) -> Category:
        cat = Category(
            user_id=test_user.id, workspace_id=test_workspace.id,
            name=name, icon="x", color="#000000", **flags,
        )
        session.add(cat)
        await session.flush()
        return cat

    return make


@pytest.fixture
def make_tx(session, test_user, test_account, test_workspace):
    async def make(amount, type_="debit", **extra) -> Transaction:
        tx = Transaction(
            id=uuid.uuid4(),
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            account_id=test_account.id,
            description="Row",
            amount=Decimal(str(amount)),
            currency=extra.pop("currency", test_account.currency),
            date=extra.pop("date", MONTH),
            type=type_,
            source="manual",
            **extra,
        )
        session.add(tx)
        await session.flush()
        return tx

    return make


async def _link(session, anchor, *pairs):
    await reimbursement_service.replace_allocations(
        session,
        anchor,
        [
            ReimbursementAllocationInput(counterpart_id=tx.id, amount=Decimal(str(amount)))
            for tx, amount in pairs
        ],
    )


async def _parts(session, test_user, start=MONTH, end=None, **kwargs):
    parts = await reimbursement_offset_parts(
        session, test_user.id, start, end or TODAY, end_inclusive=True, **kwargs
    )
    totals: dict = defaultdict(Decimal)
    for part in parts:
        totals[(part.category_id, part.type)] += part.amount
    return {key: value.quantize(Decimal("0.01")) for key, value in totals.items() if value}


@pytest.mark.asyncio
async def test_a_partly_paid_back_dinner_counts_only_the_users_share(
    session, test_user, make_tx, category
):
    food, transfers_in = await category("Food"), await category("Paybacks")
    dinner = await make_tx(100, category_id=food.id)
    alice = await make_tx(33, "credit", category_id=transfers_in.id)
    bob = await make_tx(33, "credit", category_id=transfers_in.id)
    await _link(session, dinner, (alice, 33), (bob, 33))

    assert await _parts(session, test_user) == {
        (food.id, "debit"): Decimal("66.00"),
        (transfers_in.id, "credit"): Decimal("66.00"),
    }


@pytest.mark.asyncio
async def test_an_income_anchor_nets_the_expenses_paid_out_of_it(
    session, test_user, make_tx, category
):
    pool, dining = await category("Pool"), await category("Dining")
    collected = await make_tx(200, "credit", category_id=pool.id)
    spends = [await make_tx(50, category_id=dining.id) for _ in range(3)]
    await _link(session, collected, *[(tx, 50) for tx in spends])

    assert await _parts(session, test_user) == {
        (pool.id, "credit"): Decimal("150.00"),
        (dining.id, "debit"): Decimal("150.00"),
    }


@pytest.mark.asyncio
async def test_paid_back_more_than_in_full_counts_the_excess_the_other_way(
    session, test_user, make_tx, category
):
    gifts = await category("Gifts")
    gift = await make_tx(100, category_id=gifts.id)
    payback = await make_tx(150, "credit")
    await _link(session, gift, (payback, 150))

    # The gift's 100 of spending goes, and 50 of income appears in Gifts.
    assert await _parts(session, test_user) == {
        (gifts.id, "debit"): Decimal("100.00"),
        (gifts.id, "credit"): Decimal("-50.00"),
        (None, "credit"): Decimal("150.00"),
    }


@pytest.mark.asyncio
async def test_each_side_lands_in_its_own_period(session, test_user, make_tx, category):
    food = await category("Food")
    dinner = await make_tx(100, category_id=food.id, date=PREV)
    payback = await make_tx(60, "credit", date=MONTH)
    await _link(session, dinner, (payback, 60))

    assert await _parts(session, test_user, start=PREV, end=MONTH - timedelta(days=1)) == {
        (food.id, "debit"): Decimal("60.00"),
    }
    assert await _parts(session, test_user) == {(None, "credit"): Decimal("60.00")}


@pytest.mark.asyncio
async def test_a_counterpart_split_across_two_anchors(session, test_user, make_tx, category):
    food, drinks = await category("Food"), await category("Drinks")
    first = await make_tx(30, category_id=food.id)
    second = await make_tx(40, category_id=drinks.id)
    payback = await make_tx(60, "credit")
    await _link(session, first, (payback, 30))
    await _link(session, second, (payback, 25))

    assert await _parts(session, test_user) == {
        (food.id, "debit"): Decimal("30.00"),
        (drinks.id, "debit"): Decimal("25.00"),
        (None, "credit"): Decimal("55.00"),
    }


@pytest.mark.asyncio
async def test_an_anchor_with_category_lines_nets_them_in_proportion(
    session, test_user, test_workspace, make_tx, category
):
    food, home = await category("Food"), await category("Home")
    receipt = await make_tx(100, has_category_lines=True)
    for position, (cat, amount) in enumerate(((food, 75), (home, 25))):
        session.add(
            TransactionCategoryLine(
                workspace_id=test_workspace.id, transaction_id=receipt.id,
                category_id=cat.id, amount=Decimal(amount), position=position,
            )
        )
    await session.flush()
    await session.refresh(receipt, ["category_lines"])
    refund = await make_tx(40, "credit")
    await _link(session, receipt, (refund, 40))

    assert await _parts(session, test_user) == {
        (food.id, "debit"): Decimal("30.00"),
        (home.id, "debit"): Decimal("10.00"),
        (None, "credit"): Decimal("40.00"),
    }


@pytest.mark.asyncio
async def test_converts_at_the_counterparts_stored_rate(session, test_user, make_tx, category):
    food = await category("Food")
    dinner = await make_tx(100, currency="EUR", amount_primary=Decimal("1100"), category_id=food.id)
    payback = await make_tx(550, "credit", currency="SEK", amount_primary=Decimal("550"))
    await _link(session, dinner, (payback, 275))

    assert await _parts(session, test_user, primary_currency="SEK") == {
        (food.id, "debit"): Decimal("275.00"),
        (None, "credit"): Decimal("275.00"),
    }


@pytest.mark.asyncio
async def test_a_counterpart_without_any_rate_nets_nothing(
    session, test_user, make_tx, category, monkeypatch
):
    from app.services import fx_rate_service

    async def no_rate(*args, **kwargs):
        return None

    monkeypatch.setattr(fx_rate_service, "_resolve_rate", no_rate)
    food = await category("Food")
    dinner = await make_tx(100, category_id=food.id, amount_primary=Decimal("100"))
    payback = await make_tx(550, "credit", currency="SEK")
    await _link(session, dinner, (payback, 550))

    assert await _parts(session, test_user, primary_currency="BRL") == {}


@pytest.mark.asyncio
async def test_a_counterpart_a_sync_shrank_is_scaled_down(session, test_user, make_tx, category):
    food = await category("Food")
    first = await make_tx(100, category_id=food.id)
    second = await make_tx(100, category_id=food.id)
    payback = await make_tx(60, "credit")
    await _link(session, first, (payback, 30))
    await _link(session, second, (payback, 30))
    payback.amount = Decimal("30")
    await session.flush()

    assert await _parts(session, test_user) == {
        (food.id, "debit"): Decimal("30.00"),
        (None, "credit"): Decimal("30.00"),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"is_ignored": True},
        {"exclude_from_pnl": True},
        {"transfer_pair_id": uuid.uuid4()},
    ],
)
async def test_an_allocation_counts_only_while_both_rows_do(
    session, test_user, make_tx, category, change
):
    food = await category("Food")
    for side in ("anchor", "counterpart"):
        dinner = await make_tx(100, category_id=food.id)
        payback = await make_tx(60, "credit")
        await _link(session, dinner, (payback, 60))
        target = dinner if side == "anchor" else payback
        for field, value in change.items():
            setattr(target, field, value)
        await session.flush()

        assert await _parts(session, test_user) == {}, side
        for field in change:
            setattr(target, field, None if field == "transfer_pair_id" else False)
        await session.flush()
        await _link(session, dinner)


@pytest.mark.asyncio
async def test_an_allocation_stops_counting_when_a_row_gets_an_ignored_category(
    session, test_user, make_tx, category
):
    food, ignored = await category("Food"), await category("Ignored", is_ignored=True)
    dinner = await make_tx(100, category_id=food.id)
    payback = await make_tx(60, "credit")
    await _link(session, dinner, (payback, 60))
    payback.category_id = ignored.id
    await session.flush()

    assert await _parts(session, test_user) == {}


@pytest.mark.asyncio
async def test_pending_and_future_sides_are_left_to_the_forecast(
    session, test_user, make_tx, category
):
    food = await category("Food")
    dinner = await make_tx(100, category_id=food.id)
    pending = await make_tx(30, "credit", status="pending")
    future = await make_tx(20, "credit", date=TODAY + timedelta(days=3))
    await _link(session, dinner, (pending, 30), (future, 20))

    # The posted anchor nets both; the counterparts wait for the forecast.
    assert await _parts(session, test_user) == {(food.id, "debit"): Decimal("50.00")}
    assert await reimbursement_share_fractions(session, [pending, future]) == {
        pending.id: Decimal("0"),
        future.id: Decimal("0"),
    }


@pytest.mark.asyncio
async def test_forecast_fractions_for_an_anchor(session, make_tx, category):
    food = await category("Food")
    dinner = await make_tx(100, category_id=food.id, status="pending")
    partial = await make_tx(25, "credit")
    await _link(session, dinner, (partial, 25))
    gift = await make_tx(100, category_id=food.id, status="pending")
    await _link(session, gift, (await make_tx(150, "credit"), 150))

    assert await reimbursement_share_fractions(session, [dinner, gift]) == {
        dinner.id: Decimal("0.75"),
        gift.id: Decimal("-0.5"),
    }


@pytest.mark.asyncio
async def test_parts_stay_in_their_workspace(session, test_user, make_tx, category):
    from app.services.workspace_service import create_workspace

    other = await create_workspace(session, name="Other", creator=test_user)
    food = await category("Food")
    dinner = await make_tx(100, category_id=food.id)
    await _link(session, dinner, (await make_tx(60, "credit"), 60))

    assert await _parts(session, test_user, workspace_id=other.id) == {}


@pytest.mark.asyncio
async def test_a_partly_ignored_row_gives_up_only_its_counted_share(
    session, test_user, test_workspace, make_tx, category
):
    # A 100 payback with 50 in a category that is ignored since: only 50 of it
    # counts as income, so only 50 can come off, on both sides.
    food, shop, hidden = await category("Food"), await category("Shop"), await category("Hidden")
    dinner = await make_tx(100, category_id=food.id)
    payback = await make_tx(100, "credit", has_category_lines=True)
    for position, (cat, amount) in enumerate(((shop, 50), (hidden, 50))):
        session.add(TransactionCategoryLine(
            workspace_id=test_workspace.id, transaction_id=payback.id,
            category_id=cat.id, amount=Decimal(amount), position=position,
        ))
    await session.flush()
    await session.refresh(payback, ["category_lines"])
    await _link(session, dinner, (payback, 100))
    hidden.is_ignored = True
    await session.flush()

    assert await _parts(session, test_user) == {
        (shop.id, "credit"): Decimal("50.00"),
        (food.id, "debit"): Decimal("50.00"),
    }


@pytest.mark.asyncio
async def test_a_pending_anchor_keeps_the_fx_residual_the_posted_path_keeps(session, make_tx):
    # The same currency on both rows, booked at different primary values: the
    # posted path leaves 10 of the 120 expense, so the P&L forecast must too.
    expense = await make_tx(100, status="pending", amount_primary=Decimal("120"))
    payback = await make_tx(100, "credit", status="pending", amount_primary=Decimal("110"))
    await _link(session, expense, (payback, 100))

    in_primary = await reimbursement_share_fractions(session, [expense, payback], in_primary=True)
    native = await reimbursement_share_fractions(session, [expense, payback])

    assert in_primary[expense.id] == pytest.approx(Decimal("10") / Decimal("120"))
    assert in_primary[payback.id] == Decimal("0")
    # The balance curve works in the account's currency and keeps the native share.
    assert native[expense.id] == Decimal("0")
