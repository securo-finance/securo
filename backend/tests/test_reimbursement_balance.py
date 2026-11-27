"""Netting on the dashboard's balance curve (#1126, ADR 0033).

The curve is a running real balance; "netted" counts a row linked by a
reimbursement only for the share that is the user's own, which moves money
between days but never changes what a fully settled month ends on.
"""

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.app_clock import app_today
from app.models.transaction import Transaction
from app.schemas.reimbursement import ReimbursementAllocationInput
from app.services import dashboard_service, reimbursement_service

TODAY = app_today()
THIS_MONTH = TODAY.replace(day=1)
LAST_MONTH = (THIS_MONTH - timedelta(days=1)).replace(day=1)
TWO_MONTHS_AGO = (LAST_MONTH - timedelta(days=1)).replace(day=1)


@pytest.fixture
async def manual_account(session, test_account):
    # A connected account reports the provider's balance; a manual one is the
    # sum of its rows, which is what the curve walks.
    test_account.connection_id = None
    await session.commit()
    return test_account


@pytest.fixture
def make_tx(session, test_user, manual_account, test_workspace):
    async def make(amount, type_, on: date, **extra) -> Transaction:
        tx = Transaction(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
            account_id=manual_account.id, description="Row", amount=Decimal(str(amount)),
            currency=manual_account.currency, date=on, type=type_, source="manual", **extra,
        )
        session.add(tx)
        await session.flush()
        return tx

    return make


async def _link(session, anchor, *pairs):
    await reimbursement_service.replace_allocations(
        session, anchor,
        [ReimbursementAllocationInput(counterpart_id=tx.id, amount=Decimal(str(a))) for tx, a in pairs],
    )
    await session.commit()


async def _curve(session, test_user, test_workspace, month, **kwargs):
    history = await dashboard_service.get_balance_history(
        session, test_workspace.id, test_user.id, month, comparison_months=[month - timedelta(days=40)], **kwargs
    )
    return history, {point.day: point.balance for point in history.current if point.balance is not None}


@pytest.mark.asyncio
async def test_a_paid_back_dinner_dips_once_by_the_users_share(session, make_tx, test_user, test_workspace):
    dinner = await make_tx(400, "debit", LAST_MONTH.replace(day=3))
    alice = await make_tx(100, "credit", LAST_MONTH.replace(day=4))
    bob = await make_tx(100, "credit", LAST_MONTH.replace(day=6))
    await _link(session, dinner, (alice, 100), (bob, 100))

    real_history, real = await _curve(session, test_user, test_workspace, LAST_MONTH, netting=False)
    net_history, net = await _curve(session, test_user, test_workspace, LAST_MONTH)

    assert (real[2], real[3], real[4], real[5], real[6]) == (0, -400, -300, -300, -200)
    assert (net[2], net[3], net[4], net[5], net[6]) == (0, -200, -200, -200, -200)
    assert real[max(real)] == net[max(net)] == -200
    assert (real_history.netted, net_history.netted) == (False, True)


@pytest.mark.asyncio
async def test_netting_off_is_the_real_balance_and_on_is_the_default(
    session, make_tx, test_user, test_workspace
):
    dinner = await make_tx(400, "debit", LAST_MONTH.replace(day=3))
    await _link(session, dinner, (await make_tx(100, "credit", LAST_MONTH.replace(day=4)), 100))

    _, off = await _curve(session, test_user, test_workspace, LAST_MONTH, netting=False)
    _, default = await _curve(session, test_user, test_workspace, LAST_MONTH)

    assert off[3] == -400
    assert default[3] == -300


@pytest.mark.asyncio
async def test_a_payback_in_a_later_month_does_not_bump_the_next_months_curve(
    session, make_tx, test_user, test_workspace
):
    dinner = await make_tx(400, "debit", TWO_MONTHS_AGO.replace(day=28))
    payback = await make_tx(300, "credit", LAST_MONTH.replace(day=3))
    await _link(session, dinner, (payback, 300))

    real_history, real = await _curve(session, test_user, test_workspace, LAST_MONTH, netting=False)
    net_history, net = await _curve(session, test_user, test_workspace, LAST_MONTH)

    # The month starts where the netted dinner left the balance: the 300 that
    # is coming is already counted, and arriving later changes nothing.
    assert real_history.opening_balance == -400
    assert net_history.opening_balance == -100
    assert real[2] == -400 and real[3] == -100
    assert net[2] == net[3] == -100


@pytest.mark.asyncio
async def test_paid_back_more_than_in_full_shows_the_excess_as_a_gain(
    session, make_tx, test_user, test_workspace
):
    gift = await make_tx(100, "debit", LAST_MONTH.replace(day=3))
    await _link(session, gift, (await make_tx(150, "credit", LAST_MONTH.replace(day=5)), 150))

    _, real = await _curve(session, test_user, test_workspace, LAST_MONTH, netting=False)
    _, net = await _curve(session, test_user, test_workspace, LAST_MONTH)

    assert (real[3], real[5]) == (-100, 50)
    assert (net[3], net[5]) == (50, 50)


@pytest.mark.asyncio
async def test_a_chart_scoped_to_some_accounts_stays_the_real_balance(
    session, make_tx, manual_account, test_user, test_workspace
):
    dinner = await make_tx(400, "debit", LAST_MONTH.replace(day=3))
    await _link(session, dinner, (await make_tx(100, "credit", LAST_MONTH.replace(day=4)), 100))

    history, curve = await _curve(
        session, test_user, test_workspace, LAST_MONTH, account_ids=[manual_account.id]
    )

    assert history.netted is False
    assert curve[3] == -400 and curve[4] == -300


@pytest.mark.asyncio
async def test_a_link_whose_side_is_ignored_counts_whole(session, make_tx, test_user, test_workspace):
    dinner = await make_tx(400, "debit", LAST_MONTH.replace(day=3))
    payback = await make_tx(100, "credit", LAST_MONTH.replace(day=4))
    await _link(session, dinner, (payback, 100))
    payback.exclude_from_pnl = True
    await session.commit()

    _, net = await _curve(session, test_user, test_workspace, LAST_MONTH)

    assert (net[3], net[4]) == (-400, -300)


@pytest.mark.asyncio
async def test_a_pending_payback_nets_the_next_months_projected_opening(
    session, make_tx, test_user, test_workspace
):
    next_month = (THIS_MONTH + timedelta(days=32)).replace(day=1)
    dinner = await make_tx(400, "debit", THIS_MONTH)
    payback = await make_tx(100, "credit", TODAY, status="pending")
    await _link(session, dinner, (payback, 100))

    real_history, _ = await _curve(session, test_user, test_workspace, next_month, netting=False)
    net_history, _ = await _curve(session, test_user, test_workspace, next_month)

    # Same money either way once the payback lands; netting must not count the
    # pending 100 a second time on top of the dinner's smaller share.
    assert real_history.opening_balance == net_history.opening_balance == -300


@pytest.mark.asyncio
async def test_a_pending_payback_the_provider_snapshot_holds_is_not_netted_twice(
    session, test_user, test_workspace, test_account
):
    # A connected account: its balance is the provider's, which already holds
    # the pending payback. The next month's opening must come out the same
    # netted or not, since the link only moves where money is counted.
    next_month = (THIS_MONTH + timedelta(days=32)).replace(day=1)
    for amount, type_, on, extra in (
        (400, "debit", THIS_MONTH, {}),
        (100, "credit", TODAY, {"status": "pending"}),
    ):
        session.add(Transaction(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
            account_id=test_account.id, description="Row", amount=Decimal(amount),
            currency=test_account.currency, date=on, type=type_, source="manual", **extra,
        ))
    await session.flush()
    rows = (await session.execute(
        select(Transaction).where(Transaction.workspace_id == test_workspace.id).order_by(Transaction.amount.desc())
    )).scalars().all()
    await _link(session, rows[0], (rows[1], 100))

    real_history, _ = await _curve(session, test_user, test_workspace, next_month, netting=False)
    net_history, _ = await _curve(session, test_user, test_workspace, next_month)

    assert net_history.opening_balance == real_history.opening_balance


@pytest.mark.asyncio
async def test_a_linked_pair_in_a_foreign_currency_keeps_its_fx_residual(
    session, manual_account, test_user, test_workspace
):
    # USD 100 spent and USD 100 paid back on a BRL account, at different rates:
    # the bank took R$600 and returned R$500. The real balance ends at -100,
    # and netting only moves money between days, so the netted curve must too.
    rows = {}
    for key, type_, day, in_account in (("expense", "debit", 3, "600"), ("payback", "credit", 5, "500")):
        rows[key] = Transaction(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
            account_id=manual_account.id, description=key, amount=Decimal("100"), currency="USD",
            amount_account=Decimal(in_account), date=LAST_MONTH.replace(day=day), type=type_,
            source="manual",
        )
        session.add(rows[key])
    await session.flush()
    await _link(session, rows["expense"], (rows["payback"], 100))

    _, real = await _curve(session, test_user, test_workspace, LAST_MONTH, netting=False)
    _, net = await _curve(session, test_user, test_workspace, LAST_MONTH)

    assert (real[3], real[5]) == (-600, -100)
    # The residual shows up where the money went: R$600 out, R$500 back.
    assert (net[3], net[5]) == (-100, -100)
    assert real[max(real)] == net[max(net)]


@pytest.mark.asyncio
async def test_a_link_to_a_closed_account_is_not_netted_on_one_side_only(
    session, make_tx, test_user, test_workspace, manual_account
):
    from app.models.account import Account

    # The payback sits in a closed account, which the curve leaves out. Netting
    # the expense alone would add its share to the curve and never take the
    # payback off, ending the month above the real balance.
    closed = Account(
        user_id=test_user.id, workspace_id=test_workspace.id, name="Old", type="checking",
        balance=0, currency=manual_account.currency, is_closed=True,
    )
    session.add(closed)
    await session.flush()
    dinner = await make_tx(400, "debit", LAST_MONTH.replace(day=3))
    payback = Transaction(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id, account_id=closed.id,
        description="Payback", amount=Decimal("100"), currency=closed.currency,
        date=LAST_MONTH.replace(day=4), type="credit", source="manual",
    )
    session.add(payback)
    await session.flush()
    await _link(session, dinner, (payback, 100))

    _, real = await _curve(session, test_user, test_workspace, LAST_MONTH, netting=False)
    _, net = await _curve(session, test_user, test_workspace, LAST_MONTH)

    assert net == real


@pytest.mark.asyncio
async def test_a_linked_card_purchase_in_accrual_mode_nets_on_the_bill_date(
    session, make_tx, test_user, test_workspace
):
    from app.models.account import Account
    from app.services import admin_service

    # In accrual mode the curve moves a card purchase to its bill date, so the
    # linked part must leave the curve there too, not on the purchase date.
    await admin_service.set_app_setting(session, "credit_card_accounting_mode", "accrual")
    card = Account(
        user_id=test_user.id, workspace_id=test_workspace.id, name="Card", type="credit_card",
        balance=0, currency="BRL", statement_close_day=31, payment_due_day=15,
    )
    session.add(card)
    await session.flush()
    purchase = Transaction(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id, account_id=card.id,
        description="Dinner", amount=Decimal("400"), currency=card.currency,
        date=LAST_MONTH.replace(day=3), effective_date=LAST_MONTH.replace(day=15),
        type="debit", source="manual",
    )
    session.add(purchase)
    await session.flush()
    payback = await make_tx(100, "credit", LAST_MONTH.replace(day=20))
    await _link(session, purchase, (payback, 100))

    _, real = await _curve(session, test_user, test_workspace, LAST_MONTH, netting=False)
    _, net = await _curve(session, test_user, test_workspace, LAST_MONTH)

    assert (real[3], real[15], real[20]) == (0, -400, -300)
    assert {day: net[day] - real[day] for day in real} == {
        day: 100 if 15 <= day < 20 else 0 for day in real
    }
