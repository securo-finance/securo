"""Coverage-focused tests for app.services.dashboard_service.

Targets the multi-currency conversion branches, recurring-projection
balance adjustments, group-split (owner offset + viewer shared) paths,
pending-shares-net aggregation, and the daily-deltas / balance-history
projection branches that the existing suite doesn't reach.
"""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from unittest.mock import AsyncMock, patch
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.category import Category
from app.models.fx_rate import FxRate
from app.models.group import Group, GroupMember
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.models.transaction_split import TransactionSplit
from app.services.dashboard_service import (
    _balance_at,
    _compute_pending_shares_net,
    _daily_deltas,
    get_balance_history,
    get_monthly_trend,
    get_projected_transactions,
    get_spending_by_category,
    get_summary,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_fx(session, today=None):
    """USD->BRL=5.0, USD->EUR=0.9, USD->USD=1.0."""
    today = today or date.today()
    for quote, rate in [("BRL", "5.0"), ("EUR", "0.9"), ("USD", "1.0")]:
        session.add(FxRate(base_currency="USD", quote_currency=quote, date=today,
                           rate=Decimal(rate), source="test"))
    await session.commit()


async def _make_account(session, user_id, workspace_id, *, currency="BRL",
                        balance="0.00", connection_id=None, acc_type="checking",
                        is_closed=False, name="Acc"):
    acc = Account(
        id=uuid.uuid4(), user_id=user_id, workspace_id=workspace_id, name=name,
        type=acc_type, balance=Decimal(balance), currency=currency,
        connection_id=connection_id, is_closed=is_closed,
    )
    session.add(acc)
    await session.commit()
    await session.refresh(acc)
    return acc


async def _add_txn(session, user_id, account_id, workspace_id, amount, typ, dt,
                   *, currency="BRL", source="manual", category_id=None,
                   amount_primary=None, transfer_pair_id=None, status="posted"):
    txn = Transaction(
        id=uuid.uuid4(), user_id=user_id, account_id=account_id,
        workspace_id=workspace_id, description="t", amount=Decimal(str(amount)),
        date=dt, type=typ, source=source, currency=currency,
        status=status,
        category_id=category_id,
        amount_primary=Decimal(str(amount_primary)) if amount_primary is not None else None,
        transfer_pair_id=transfer_pair_id,
        created_at=datetime.now(timezone.utc),
    )
    session.add(txn)
    await session.commit()
    await session.refresh(txn)
    return txn


async def _make_category(session, user_id, workspace_id, name):
    cat = Category(
        id=uuid.uuid4(), user_id=user_id, workspace_id=workspace_id,
        name=name, icon="tag", color="#000", is_system=False,
    )
    session.add(cat)
    await session.commit()
    await session.refresh(cat)
    return cat


# ---------------------------------------------------------------------------
# Multi-currency total balance + asset add (lines 224, 228-230) and primary
# income/expenses via amount_primary (257-258)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summary_multi_currency_and_primary_amounts(session: AsyncSession, test_user, test_workspace):
    today = date.today()
    await _seed_fx(session, today)
    brl = await _make_account(session, test_user.id, test_workspace.id, currency="BRL")
    usd = await _make_account(session, test_user.id, test_workspace.id, currency="USD")

    # Opening balances in two currencies -> multi-currency total -> convert loop
    await _add_txn(session, test_user.id, brl.id, test_workspace.id, 1000, "credit", today, source="opening_balance")
    await _add_txn(session, test_user.id, usd.id, test_workspace.id, 200, "credit", today, source="opening_balance", currency="USD")

    # Income/expense rows carrying amount_primary -> primary_row branch (257-258)
    await _add_txn(session, test_user.id, usd.id, test_workspace.id, 300, "credit", today,
                   currency="USD", amount_primary=1500)
    await _add_txn(session, test_user.id, usd.id, test_workspace.id, 100, "debit", today,
                   currency="USD", amount_primary=500)

    summary = await get_summary(session, test_workspace.id, test_user.id, month=today.replace(day=1))
    assert summary.total_balance.get("BRL") == pytest.approx(1000.0)
    assert summary.total_balance.get("USD") is not None
    assert summary.total_balance_primary > 0
    # amount_primary based primary income/expenses
    assert summary.monthly_income_primary == pytest.approx(1500.0, abs=1.0)
    assert summary.monthly_expenses_primary == pytest.approx(500.0, abs=1.0)


# ---------------------------------------------------------------------------
# Balance projection for current month with recurring projections (123-124)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summary_balance_projection_with_recurring(session, test_user, test_workspace):
    today = date.today()
    month_start = today.replace(day=1)
    acc = await _make_account(session, test_user.id, test_workspace.id, currency="BRL", balance="0")
    await _add_txn(session, test_user.id, acc.id, test_workspace.id, 1000, "credit", month_start, source="opening_balance")

    # Recurring that produces occurrences later this month (after today) so the
    # projection adjusts total_balance (lines 117-124). Use daily frequency.
    rec = RecurringTransaction(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        description="Daily", amount=Decimal("10"), type="debit",
        frequency="daily", currency="BRL",
        start_date=month_start, next_occurrence=month_start,
    )
    session.add(rec)
    await session.commit()

    # Use a balance_date in the past so month_end > cutoff -> projection path runs
    summary = await get_summary(
        session, test_workspace.id, test_user.id, month=month_start,
        balance_date=month_start,
    )
    assert summary is not None
    assert "BRL" in summary.total_balance


# ---------------------------------------------------------------------------
# Group splits: owner offset + viewer shared (summary 313-325, spending 496-535)
# ---------------------------------------------------------------------------


async def _make_group_with_members(session, owner_id, workspace_id, *, viewer_user_id=None):
    """Create a group owned by owner_id with a self member and one other.
    If viewer_user_id given, that other member is linked to viewer (invitee)."""
    group = Group(
        id=uuid.uuid4(), user_id=owner_id, workspace_id=workspace_id,
        name="Trip", default_currency="BRL",
    )
    session.add(group)
    await session.flush()
    self_m = GroupMember(
        id=uuid.uuid4(), group_id=group.id, workspace_id=workspace_id,
        name="Me", linked_user_id=owner_id, is_self=True,
    )
    other_m = GroupMember(
        id=uuid.uuid4(), group_id=group.id, workspace_id=workspace_id,
        name="Friend", linked_user_id=viewer_user_id, is_self=False,
    )
    session.add_all([self_m, other_m])
    await session.commit()
    await session.refresh(group)
    await session.refresh(self_m)
    await session.refresh(other_m)
    return group, self_m, other_m


@pytest.mark.asyncio
async def test_summary_owner_split_offset(session, test_user, test_workspace):
    today = date.today()
    month_start = today.replace(day=1)
    acc = await _make_account(session, test_user.id, test_workspace.id, currency="BRL")
    cat = await _make_category(session, test_user.id, test_workspace.id, "Dining")

    group, self_m, other_m = await _make_group_with_members(
        session, test_user.id, test_workspace.id
    )

    # Owner paid a 100 debit, split 50/50 with the friend (non-owner share = 50)
    txn = await _add_txn(session, test_user.id, acc.id, test_workspace.id, 100, "debit", today, category_id=cat.id)
    for member, amt in [(self_m, "50.00"), (other_m, "50.00")]:
        session.add(TransactionSplit(
            id=uuid.uuid4(), transaction_id=txn.id, workspace_id=test_workspace.id,
            group_member_id=member.id, share_amount=Decimal(amt), share_type="exact",
        ))
    await session.commit()

    summary = await get_summary(session, test_workspace.id, test_user.id, month=month_start)
    # Only the owner's 50 share should count as expense, not the full 100
    assert summary.monthly_expenses == pytest.approx(50.0, abs=0.01)

    spending = await get_spending_by_category(session, test_workspace.id, test_user.id, month=month_start)
    dining = next((s for s in spending if s.category_name == "Dining"), None)
    assert dining is not None
    assert dining.total == pytest.approx(50.0, abs=0.01)


@pytest.mark.asyncio
async def test_summary_viewer_shared_split(session, test_user, test_workspace, clean_db):
    """Viewer participates (linked, non-self) in another user's split tx."""
    today = date.today()
    month_start = today.replace(day=1)

    # Create the OWNER user (different from viewer test_user) and their workspace
    import bcrypt as _bcrypt
    from app.models.user import User as UserModel
    from app.models.workspace import Workspace, WorkspaceMember

    owner = UserModel(
        id=uuid.uuid4(), email="owner_dash@example.com",
        hashed_password=_bcrypt.hashpw(b"x", _bcrypt.gensalt()).decode(),
        is_active=True, is_verified=True,
        preferences={"currency_display": "BRL"},
    )
    session.add(owner)
    await session.flush()
    owner_ws = Workspace(
        id=uuid.uuid4(), name="OwnerWS", kind="personal",
        created_by_user_id=owner.id, default_currency="BRL", locale="en",
    )
    session.add(owner_ws)
    await session.flush()
    session.add(WorkspaceMember(id=uuid.uuid4(), workspace_id=owner_ws.id, user_id=owner.id, role="owner"))
    await session.commit()

    owner_acc = await _make_account(session, owner.id, owner_ws.id, currency="BRL")

    # Group owned by `owner`; viewer (test_user) is a linked, non-self member.
    group, self_m, viewer_m = await _make_group_with_members(
        session, owner.id, owner_ws.id, viewer_user_id=test_user.id
    )

    # Owner's debit tx split: viewer owes 40 of a 100 expense
    txn = await _add_txn(session, owner.id, owner_acc.id, owner_ws.id, 100, "debit", today)
    cat = await _make_category(session, owner.id, owner_ws.id, "Shared")
    txn.category_id = cat.id
    await session.commit()
    for member, amt in [(self_m, "60.00"), (viewer_m, "40.00")]:
        session.add(TransactionSplit(
            id=uuid.uuid4(), transaction_id=txn.id, workspace_id=owner_ws.id,
            group_member_id=member.id, share_amount=Decimal(amt), share_type="exact",
        ))
    await session.commit()

    # Viewer's summary should pick up their 40 shared expense (viewer_shared paths)
    summary = await get_summary(session, test_workspace.id, test_user.id, month=month_start)
    assert summary.monthly_expenses == pytest.approx(40.0, abs=0.01)

    spending = await get_spending_by_category(session, test_workspace.id, test_user.id, month=month_start)
    shared = next((s for s in spending if s.category_name == "Shared"), None)
    assert shared is not None
    assert shared.total == pytest.approx(40.0, abs=0.01)


# ---------------------------------------------------------------------------
# _compute_pending_shares_net (406-426)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pending_shares_net_owned_group(session, test_user, test_workspace):
    today = date.today()
    acc = await _make_account(session, test_user.id, test_workspace.id, currency="BRL")
    group, self_m, other_m = await _make_group_with_members(
        session, test_user.id, test_workspace.id
    )
    # Owner paid 100, friend owes 50 -> friend's line is +50 (asset to owner)
    txn = await _add_txn(session, test_user.id, acc.id, test_workspace.id, 100, "debit", today)
    for member, amt in [(self_m, "50.00"), (other_m, "50.00")]:
        session.add(TransactionSplit(
            id=uuid.uuid4(), transaction_id=txn.id, workspace_id=test_workspace.id,
            group_member_id=member.id, share_amount=Decimal(amt), share_type="exact",
        ))
    await session.commit()

    net = await _compute_pending_shares_net(session, test_workspace.id, test_user.id, "BRL")
    # Friend owes the owner -> positive net
    assert net == pytest.approx(50.0, abs=0.5)


@pytest.mark.asyncio
async def test_pending_shares_net_no_groups(session, test_user, test_workspace):
    net = await _compute_pending_shares_net(session, test_workspace.id, test_user.id, "BRL")
    assert net == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# get_projected_transactions currency conversion (718-721)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_projected_transactions_currency_conversion(session, test_user, test_workspace):
    today = date.today()
    month_start = today.replace(day=1)
    await _seed_fx(session, today)

    # Recurring in USD; user primary is BRL -> amount_primary conversion branch
    rec = RecurringTransaction(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        description="USD Sub", amount=Decimal("10"), type="debit",
        frequency="monthly", currency="USD",
        start_date=month_start, next_occurrence=month_start,
    )
    session.add(rec)
    await session.commit()

    projections = await get_projected_transactions(session, test_workspace.id, test_user.id, month=month_start)
    assert len(projections) >= 1
    assert projections[0].currency == "USD"
    assert projections[0].amount_primary is not None
    # USD 10 * 5.0 = BRL 50
    assert projections[0].amount_primary == pytest.approx(50.0, abs=1.0)


@pytest.mark.asyncio
async def test_projected_transactions_do_not_expose_fake_one_to_one(
    session, test_user, test_workspace
):
    today = date.today()
    month_start = today.replace(day=1)
    session.add(RecurringTransaction(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        description="USD without rate", amount=Decimal("10"), type="debit",
        frequency="monthly", currency="USD",
        start_date=month_start, next_occurrence=month_start,
    ))
    await session.commit()

    with patch(
        "app.services.dashboard_service._resolve_rate",
        new=AsyncMock(return_value=None),
    ):
        projections = await get_projected_transactions(
            session, test_workspace.id, test_user.id, month=month_start
        )

    assert projections[0].amount_primary is None


# ---------------------------------------------------------------------------
# _daily_deltas multi-currency (914-925) + balance_history projection (979-985)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_daily_deltas_multi_currency(session, test_user, test_workspace):
    today = date.today()
    month_start = today.replace(day=1)
    month_end = (month_start + timedelta(days=40)).replace(day=1)
    await _seed_fx(session, today)

    brl = await _make_account(session, test_user.id, test_workspace.id, currency="BRL")
    usd = await _make_account(session, test_user.id, test_workspace.id, currency="USD")
    await _add_txn(session, test_user.id, brl.id, test_workspace.id, 100, "credit", month_start)
    await _add_txn(session, test_user.id, usd.id, test_workspace.id, 20, "credit", month_start,
                   currency="USD", amount_primary=100)

    deltas = await _daily_deltas(
        session, test_workspace.id, month_start, month_end,
        primary_currency_hint="BRL",
    )
    # Day 1 should aggregate both currencies converted to BRL
    assert deltas.get(1, 0) > 100.0


@pytest.mark.asyncio
async def test_balance_history_with_future_projections(session, test_user, test_workspace):
    today = date.today()
    month_start = today.replace(day=1)
    acc = await _make_account(session, test_user.id, test_workspace.id, currency="BRL")
    await _add_txn(session, test_user.id, acc.id, test_workspace.id, 1000, "credit", month_start, source="opening_balance")

    # Daily recurring whose next occurrence is in the future -> future days
    # get projection deltas (lines 979-985). Skip on the last day of the month
    # when no future days remain.
    next_day = today + timedelta(days=1)
    if next_day.month != today.month:
        pytest.skip("no future days left in current month")
    rec = RecurringTransaction(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        description="Daily", amount=Decimal("5"), type="credit",
        frequency="daily", currency="BRL",
        start_date=month_start, next_occurrence=next_day,
    )
    session.add(rec)
    await session.commit()

    history = await get_balance_history(session, test_workspace.id, test_user.id, month=month_start)
    assert len(history.current) > 0
    assert len(history.previous) > 0


def test_signed_expr_helpers_smoke():
    """The signed-amount SQL expression helpers compile without a bound query.
    Covers the empty-currency branch of _signed_balance_expr and
    _signed_primary_expr (used elsewhere / kept for reuse)."""
    from app.services.dashboard_service import (
        _signed_balance_expr,
        _signed_primary_expr,
        _primary_amount_expr,
    )

    # Empty currency arg -> simple effective=amount branch
    expr_empty = _signed_balance_expr("")
    expr_cur = _signed_balance_expr("BRL")
    expr_primary = _signed_primary_expr()
    expr_amt = _primary_amount_expr()
    # They should be SQLAlchemy ColumnElements that render to SQL strings
    assert all(str(e) for e in (expr_empty, expr_cur, expr_primary, expr_amt))


async def _register_sqlite_to_char(session):
    """Register a SQLite UDF emulating Postgres to_char(date, 'YYYY-MM').

    get_monthly_trend uses func.to_char which only exists on Postgres in
    production. To exercise that code path under the SQLite test backend
    we install a matching scalar function on the underlying connection.
    """
    def _to_char(value, fmt):
        if value is None:
            return None
        # value comes through as an ISO date/datetime string
        s = str(value)
        return s[:7]  # 'YYYY-MM'

    raw = await session.connection()

    def _install(dbapi_conn):
        dbapi_conn.create_function("to_char", 2, _to_char)

    await raw.run_sync(lambda conn: _install(conn.connection.dbapi_connection))


@pytest.mark.asyncio
async def test_monthly_trend(session, test_user, test_workspace):
    await _register_sqlite_to_char(session)
    today = date.today()
    month_start = today.replace(day=1)
    acc = await _make_account(session, test_user.id, test_workspace.id, currency="BRL")
    await _add_txn(session, test_user.id, acc.id, test_workspace.id, 3000, "credit", month_start, amount_primary=3000)
    await _add_txn(session, test_user.id, acc.id, test_workspace.id, 500, "debit", month_start, amount_primary=500)

    trends = await get_monthly_trend(session, test_workspace.id, test_user.id, months=6)
    assert len(trends) >= 1
    current = next((t for t in trends if t.month == month_start.strftime("%Y-%m")), None)
    assert current is not None
    assert current.income >= 3000.0
    assert current.expenses >= 500.0


@pytest.mark.asyncio
async def test_summary_current_month_projection_only_adjusts_projected_balance(
    session, test_user, test_workspace
):
    """Future recurring rows affect projected, never current, balance."""
    today = date.today()
    month_start = today.replace(day=1)
    # Only run when there's at least one future day in the month.
    if (month_start + timedelta(days=40)).replace(day=1) - timedelta(days=1) <= today:
        pytest.skip("no future days left in current month")

    acc = await _make_account(session, test_user.id, test_workspace.id, currency="BRL")
    await _add_txn(session, test_user.id, acc.id, test_workspace.id, 1000, "credit", month_start, source="opening_balance")
    rec = RecurringTransaction(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        description="DailyProj", amount=Decimal("10"), type="credit",
        frequency="daily", currency="BRL",
        start_date=month_start, next_occurrence=today + timedelta(days=1),
    )
    session.add(rec)
    await session.commit()

    summary = await get_summary(session, test_workspace.id, test_user.id, month=month_start)
    assert summary.total_balance.get("BRL", 0) == 1000.0
    assert summary.projected_balance.get("BRL", 0) > 1000.0


@pytest.mark.asyncio
async def test_transfer_like_recurring_adjusts_balance_without_becoming_expense(
    session, test_user, test_workspace
):
    """An investment projection moves cash but remains outside P&L totals."""
    today = date.today()
    next_month = (
        date(today.year + 1, 1, 1)
        if today.month == 12
        else date(today.year, today.month + 1, 1)
    )
    account = await _make_account(
        session, test_user.id, test_workspace.id, currency="BRL"
    )
    await _add_txn(
        session,
        test_user.id,
        account.id,
        test_workspace.id,
        1000,
        "credit",
        today,
        source="opening_balance",
    )
    investments = Category(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Investments",
        treat_as_transfer=True,
    )
    session.add(investments)
    session.add(RecurringTransaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=account.id,
        description="Monthly investment",
        amount=Decimal("100"),
        type="debit",
        frequency="monthly",
        currency="BRL",
        start_date=next_month,
        next_occurrence=next_month,
        category_id=investments.id,
    ))
    await session.commit()

    summary = await get_summary(
        session, test_workspace.id, test_user.id, month=next_month
    )

    assert summary.total_balance.get("BRL", 0) == pytest.approx(1000.0)
    assert summary.projected_balance.get("BRL", 0) == pytest.approx(900.0)
    assert summary.projected_expenses == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_summary_connected_pending_split_by_who_reported_it(
    session, test_user, test_workspace, test_connection
):
    """On a connected account, only a provider-reported pending row is treated
    as already inside the provider's balance.

    Both rows sit in the forecast, but the synced one is assumed to be netted
    out of the 1000 the provider sent, while the recurring placeholder still
    has to come off the projected balance.
    """
    today = date.today()
    account = await _make_account(
        session, test_user.id, test_workspace.id, balance="1000.00",
        connection_id=test_connection.id,
    )
    await _add_txn(
        session, test_user.id, account.id, test_workspace.id, 40, "debit",
        today, source="sync", status="pending",
    )
    await _add_txn(
        session, test_user.id, account.id, test_workspace.id, 25, "debit",
        today, source="recurring", status="pending",
    )

    summary = await get_summary(session, test_workspace.id, test_user.id, month=today)

    assert summary.total_balance.get("BRL", 0) == pytest.approx(1000.0)
    # Only the placeholder moves the projection; the synced row is already in.
    assert summary.projected_balance.get("BRL", 0) == pytest.approx(975.0)
    # Neither has settled, so both stay out of the actual expenses.
    assert summary.monthly_expenses == pytest.approx(0.0)
    assert summary.projected_expenses == pytest.approx(65.0)


@pytest.mark.asyncio
async def test_balance_at_multi_currency_conversion(session, test_user, test_workspace):
    today = date.today()
    await _seed_fx(session, today)
    brl = await _make_account(session, test_user.id, test_workspace.id, currency="BRL")
    usd = await _make_account(session, test_user.id, test_workspace.id, currency="USD")
    await _add_txn(session, test_user.id, brl.id, test_workspace.id, 500, "credit", today)
    await _add_txn(session, test_user.id, usd.id, test_workspace.id, 100, "credit", today, currency="USD")

    total = await _balance_at(session, test_workspace.id, today, primary_currency_hint="BRL")
    # 500 BRL + (100 USD * 5.0 = 500 BRL) = 1000
    assert total == pytest.approx(1000.0, abs=5.0)
