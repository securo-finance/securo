"""PR-5: accounts.exclude_from_reports — rollup totals honor the flag.

Named cases T-1…T-15 and T-18 from the design packet §7 (T-16 lives in
test_accounts_api_coverage.py, T-17 in test_agents_mcp_tools.py — the
packet's "unless noted" files). Each case pins a surface of the 28-surface
map (§2.3): an excluded account leaves every rollup total while staying
visible in lists, ledgers, its own pages, card bills and exports (§4).
Fixtures follow test_report_service.py / test_dashboard_service.py.
"""

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.asset import Asset
from app.models.asset_value import AssetValue
from app.models.category import Category
from app.models.goal import Goal
from app.models.group import Group, GroupMember
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.models.transaction_split import TransactionSplit
from app.schemas.budget import BudgetCreate
from app.services import account_service, budget_service
from app.services._query_filters import (
    owner_split_offset_pnl,
    viewer_shared_pnl,
    viewer_shared_spending_by_category,
)
from app.services.dashboard_service import (
    _get_open_accounts,
    _month_range,
    get_balance_history,
    get_monthly_trend,
    get_spending_by_category,
    get_summary,
)
from app.services.goal_service import _resolve_current_amount
from app.services.report_service import (
    _get_baseline_projection,
    _net_worth_at,
    get_cash_flow_report,
    get_income_expenses_report,
    get_net_worth_report,
)
from app.services.transaction_service import get_transactions

from tests.conftest import engine as _test_engine


# ---------------------------------------------------------------------------
# Register a `to_char` UDF on the SQLite test engine: the income/expenses
# report and monthly trend bucket with Postgres `to_char`. Same pattern as
# test_report_service_coverage.py / test_dashboard_service_coverage.py.
# ---------------------------------------------------------------------------


def _py_to_char(value, fmt):
    """Minimal emulation of PostgreSQL to_char(date, fmt) for the formats
    the reports use: 'YYYY-MM-DD', 'YYYY-MM', 'YYYY'."""
    if value is None:
        return None
    s = str(value)[:10]  # 'YYYY-MM-DD'
    y, m, d = s.split("-")
    if fmt == "YYYY-MM-DD":
        return f"{y}-{m}-{d}"
    if fmt == "YYYY-MM":
        return f"{y}-{m}"
    if fmt == "YYYY":
        return y
    return s


@event.listens_for(_test_engine.sync_engine, "connect")
def _register_to_char(dbapi_connection, _connection_record):
    dbapi_connection.create_function("to_char", 2, _py_to_char)


@pytest.fixture(autouse=True)
async def _ensure_to_char(session: AsyncSession):
    """Make sure the UDF is registered on the live StaticPool connection."""
    raw = await session.connection()

    def _do(sync_conn):
        dbapi = sync_conn.connection.dbapi_connection
        dbapi.create_function("to_char", 2, _py_to_char)

    await raw.run_sync(_do)
    yield


# ---------------------------------------------------------------------------
# Helpers (conventions per test_dashboard_service.py / test_report_service.py)
# ---------------------------------------------------------------------------


async def _make_account(
    session: AsyncSession,
    user_id: uuid.UUID,
    name: str,
    *,
    acc_type: str = "checking",
    balance: str = "0",
    currency: str = "BRL",
    is_closed: bool = False,
    exclude: bool = False,
) -> Account:
    account = Account(
        id=uuid.uuid4(),
        user_id=user_id,
        name=name,
        type=acc_type,
        balance=Decimal(balance),
        currency=currency,
        is_closed=is_closed,
        exclude_from_reports=exclude,
    )
    session.add(account)
    await session.commit()
    await session.refresh(account)
    return account


async def _add_txn(
    session: AsyncSession,
    user_id: uuid.UUID,
    account_id: uuid.UUID,
    amount: float,
    txn_type: str,
    txn_date: date,
    *,
    category_id: uuid.UUID | None = None,
    source: str = "manual",
    status: str = "posted",
    currency: str = "BRL",
) -> Transaction:
    txn = Transaction(
        id=uuid.uuid4(),
        user_id=user_id,
        account_id=account_id,
        category_id=category_id,
        description=f"{txn_type} {amount}",
        amount=Decimal(str(amount)),
        date=txn_date,
        type=txn_type,
        source=source,
        currency=currency,
        status=status,
        created_at=datetime.now(timezone.utc),
    )
    session.add(txn)
    await session.commit()
    await session.refresh(txn)
    return txn


async def _seed_balance(
    session: AsyncSession, user_id: uuid.UUID, account: Account, amount: float,
    when: date | None = None,
) -> Transaction:
    return await _add_txn(
        session, user_id, account.id, amount, "credit",
        when or date.today(), source="opening_balance",
    )


async def _make_category(
    session: AsyncSession, user_id: uuid.UUID, name: str, *, treat_as_transfer: bool = False,
) -> Category:
    cat = Category(
        id=uuid.uuid4(), user_id=user_id, name=name, icon="tag", color="#000000",
        is_system=False, treat_as_transfer=treat_as_transfer,
    )
    session.add(cat)
    await session.commit()
    await session.refresh(cat)
    return cat


async def _set_flag(session: AsyncSession, account: Account, value: bool = True) -> None:
    account.exclude_from_reports = value
    await session.commit()


def _day_of_month(n: int) -> date:
    """The n-th day of the current month, clamped to today so rows stay past-dated."""
    today = date.today()
    return today.replace(day=min(n, today.day))


async def _make_recurring(
    session: AsyncSession, user_id: uuid.UUID, workspace_id: uuid.UUID,
    account: Category | Account, *, description: str, amount: str,
    category_id: uuid.UUID | None = None,
) -> RecurringTransaction:
    month_start, _ = _month_range(date.today())
    rec = RecurringTransaction(
        id=uuid.uuid4(),
        user_id=user_id,
        workspace_id=workspace_id,
        account_id=account.id,
        category_id=category_id,
        description=description,
        amount=Decimal(amount),
        type="debit",
        frequency="monthly",
        currency="BRL",
        start_date=month_start,
        next_occurrence=month_start,
    )
    session.add(rec)
    await session.commit()
    await session.refresh(rec)
    return rec


# ---------------------------------------------------------------------------
# T-1 — net worth report
# ---------------------------------------------------------------------------


async def test_net_worth_excludes_flagged_account(
    session: AsyncSession, test_user, test_workspace
):
    mirror = await _make_account(session, test_user.id, "Brokerage Mirror")
    await _seed_balance(session, test_user.id, mirror, 10000)
    checking = await _make_account(session, test_user.id, "Checking")
    await _seed_balance(session, test_user.id, checking, 1000)

    # Counterfactual: flag off, both accounts count (today's double count shape).
    before = await _net_worth_at(session, test_workspace.id, date.today(), "BRL")
    assert before.breakdowns["accounts"] == 11000.0

    await _set_flag(session, mirror)

    dp = await _net_worth_at(session, test_workspace.id, date.today(), "BRL")
    assert dp.breakdowns["accounts"] == 1000.0
    assert dp.value == 1000.0
    labels = [c.label for c in dp.composition]
    assert "Brokerage Mirror" not in labels
    assert "Checking" in labels

    report = await get_net_worth_report(
        session, test_workspace.id, test_user.id, months=1, interval="monthly"
    )
    assert report.summary.primary_value == 1000.0
    accounts_breakdown = next(b for b in report.summary.breakdowns if b.key == "accounts")
    assert accounts_breakdown.value == 1000.0
    for point in report.trend:
        assert "Brokerage Mirror" not in [c.label for c in point.composition]


# ---------------------------------------------------------------------------
# T-2 — #343 regression pin: portfolio + wallet mirror counts once
# ---------------------------------------------------------------------------


async def test_regression_343_portfolio_wallet_mirror_counts_once(
    session: AsyncSession, test_user, test_workspace
):
    mirror = await _make_account(session, test_user.id, "IRA Mirror")
    await _seed_balance(session, test_user.id, mirror, 10000)
    checking = await _make_account(session, test_user.id, "Everyday")
    await _seed_balance(session, test_user.id, checking, 1000)
    # The wallet side: the holdings the mirror account duplicates.
    asset = Asset(
        id=uuid.uuid4(), user_id=test_user.id, name="Wallet Holdings",
        type="investment", currency="BRL",
        purchase_price=Decimal("10000"), purchase_date=date.today(),
    )
    session.add(asset)
    await session.commit()

    # Counter-case in the same test: flag off reproduces today's double count.
    before = await get_net_worth_report(
        session, test_workspace.id, test_user.id, months=1, interval="monthly"
    )
    assert before.summary.primary_value == 21000.0

    await _set_flag(session, mirror)

    # Counted once — via the wallet, not twice.
    dp = await _net_worth_at(session, test_workspace.id, date.today(), "BRL")
    assert dp.breakdowns["accounts"] == 1000.0
    assert dp.breakdowns["assets"] == 10000.0
    assert dp.value == 11000.0

    report = await get_net_worth_report(
        session, test_workspace.id, test_user.id, months=1, interval="monthly"
    )
    assert report.summary.primary_value == 11000.0


# ---------------------------------------------------------------------------
# T-3 — excluded and closed are orthogonal (§4 interaction matrix)
# ---------------------------------------------------------------------------


async def test_excluded_and_closed_are_orthogonal(
    session: AsyncSession, test_user, test_workspace
):
    open_included = await _make_account(session, test_user.id, "Open Included")
    open_excluded = await _make_account(session, test_user.id, "Open Excluded", exclude=True)
    closed_included = await _make_account(session, test_user.id, "Closed Included")
    closed_excluded = await _make_account(session, test_user.id, "Closed Excluded", exclude=True)

    done = await account_service.close_account(
        session, closed_included.id, test_workspace.id
    )
    assert done is not None and done.is_closed
    done = await account_service.close_account(
        session, closed_excluded.id, test_workspace.id
    )
    assert done is not None and done.is_closed
    # Closing must not clear the reporting flag.
    assert done.exclude_from_reports is True

    ids = {a.id for a in await _get_open_accounts(session, test_workspace.id)}
    assert open_included.id in ids        # open, included → counted
    assert open_excluded.id not in ids    # open, excluded → rollups drop it
    assert closed_included.id not in ids  # closed, included → is_closed drops it
    assert closed_excluded.id not in ids  # closed, excluded → both signals

    # Reopen preserves the flag; the account stays out of rollups.
    reopened = await account_service.reopen_account(
        session, closed_excluded.id, test_workspace.id
    )
    assert reopened is not None
    assert reopened.is_closed is False
    assert reopened.exclude_from_reports is True
    ids = {a.id for a in await _get_open_accounts(session, test_workspace.id)}
    assert closed_excluded.id not in ids

    # Lists keep the row (S17) and carry the flag in the payload.
    rows = await account_service.get_accounts(
        session, test_workspace.id, include_closed=True
    )
    row = next(r for r in rows if str(r["id"]) == str(closed_excluded.id))
    assert row["exclude_from_reports"] is True
    assert row["is_closed"] is False


# ---------------------------------------------------------------------------
# T-4 — income vs expenses report
# ---------------------------------------------------------------------------


async def test_income_expenses_excludes_flagged_account(
    session: AsyncSession, test_user, test_workspace
):
    mirror = await _make_account(session, test_user.id, "Mirror Co")
    checking = await _make_account(session, test_user.id, "Paycheck")
    salary_m = await _make_category(session, test_user.id, "Mirror Salary")
    food_m = await _make_category(session, test_user.id, "Mirror Food")
    salary = await _make_category(session, test_user.id, "Salary")
    food = await _make_category(session, test_user.id, "Food")

    await _add_txn(session, test_user.id, mirror.id, 5000, "credit", _day_of_month(2), category_id=salary_m.id)
    await _add_txn(session, test_user.id, mirror.id, 300, "debit", _day_of_month(3), category_id=food_m.id)
    await _add_txn(session, test_user.id, checking.id, 800, "credit", _day_of_month(2), category_id=salary.id)
    await _add_txn(session, test_user.id, checking.id, 100, "debit", _day_of_month(4), category_id=food.id)

    # Counterfactual before the flag: everything counts.
    before = await get_income_expenses_report(
        session, test_workspace.id, test_user.id, months=12
    )
    assert next(b for b in before.summary.breakdowns if b.key == "income").value == 5800.0
    assert next(b for b in before.summary.breakdowns if b.key == "expenses").value == 400.0

    await _set_flag(session, mirror)

    rep = await get_income_expenses_report(
        session, test_workspace.id, test_user.id, months=12
    )
    # Main query: all sub-totals exclude the flagged account.
    assert next(b for b in rep.summary.breakdowns if b.key == "income").value == 800.0
    assert next(b for b in rep.summary.breakdowns if b.key == "expenses").value == 100.0

    # Breakdowns (composition) lose only the mirror's categories.
    income_comp = {c.label: c.value for c in rep.composition if c.group == "income"}
    expense_comp = {c.label: c.value for c in rep.composition if c.group == "expenses"}
    assert income_comp.get("Salary") == 800.0
    assert "Mirror Salary" not in income_comp
    assert expense_comp.get("Food") == 100.0
    assert "Mirror Food" not in expense_comp

    # Category trend inherits the same filter.
    assert "Mirror Food" not in [t.label for t in rep.category_trend]


# ---------------------------------------------------------------------------
# T-5 — split offsets respect the exclusion (both shared helpers, §14 gate 1)
# ---------------------------------------------------------------------------


async def test_split_offsets_respect_account_exclusion(
    session: AsyncSession, test_user, test_workspace
):
    month_start, month_end = _month_range(date.today())

    # --- Owner side: offsets for parents on an excluded account are dropped.
    excluded = await _make_account(session, test_user.id, "Split Mirror")
    included = await _make_account(session, test_user.id, "Split Real")

    group = Group(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        name="Trip", default_currency="BRL",
    )
    session.add(group)
    await session.flush()
    self_m = GroupMember(
        id=uuid.uuid4(), group_id=group.id, workspace_id=test_workspace.id,
        name="Me", linked_user_id=test_user.id, is_self=True,
    )
    other_m = GroupMember(
        id=uuid.uuid4(), group_id=group.id, workspace_id=test_workspace.id,
        name="Friend", linked_user_id=None, is_self=False,
    )
    session.add_all([self_m, other_m])
    await session.commit()

    for account in (excluded, included):
        txn = await _add_txn(session, test_user.id, account.id, 100, "debit", date.today())
        for member, amt in ((self_m, "60.00"), (other_m, "40.00")):
            session.add(TransactionSplit(
                id=uuid.uuid4(), transaction_id=txn.id,
                workspace_id=test_workspace.id, group_member_id=member.id,
                share_amount=Decimal(amt), share_type="exact",
            ))
    await session.commit()

    await _set_flag(session, excluded)
    income_off, expense_off = await owner_split_offset_pnl(
        session, test_user.id, month_start, month_end,
        workspace_id=test_workspace.id,
    )
    # Only the included parent's non-owner share may subtract (S15).
    assert income_off == 0.0
    assert expense_off == 40.0

    await _set_flag(session, excluded, False)
    _, expense_off = await owner_split_offset_pnl(
        session, test_user.id, month_start, month_end,
        workspace_id=test_workspace.id,
    )
    assert expense_off == 80.0

    # --- Viewer side: BOTH shared helpers must drop an excluded parent.
    # Another user owns the split; test_user is the linked invitee.
    import bcrypt as _bcrypt
    from app.models.user import User as UserModel
    from app.models.workspace import Workspace, WorkspaceMember

    owner = UserModel(
        id=uuid.uuid4(), email="split_owner@example.com",
        hashed_password=_bcrypt.hashpw(b"x", _bcrypt.gensalt()).decode(),
        is_active=True, is_verified=True, preferences={"currency_display": "BRL"},
    )
    session.add(owner)
    await session.flush()
    owner_ws = Workspace(
        id=uuid.uuid4(), name="OwnerWS", kind="personal",
        created_by_user_id=owner.id, default_currency="BRL", locale="en",
    )
    session.add(owner_ws)
    await session.flush()
    session.add(WorkspaceMember(
        id=uuid.uuid4(), workspace_id=owner_ws.id, user_id=owner.id, role="owner",
    ))
    await session.commit()

    owner_acc = await _make_account(session, owner.id, "Owner Acc")
    shared_group = Group(
        id=uuid.uuid4(), user_id=owner.id, workspace_id=owner_ws.id,
        name="Concert", default_currency="BRL",
    )
    session.add(shared_group)
    await session.flush()
    owner_self = GroupMember(
        id=uuid.uuid4(), group_id=shared_group.id, workspace_id=owner_ws.id,
        name="Owner", linked_user_id=owner.id, is_self=True,
    )
    viewer_m = GroupMember(
        id=uuid.uuid4(), group_id=shared_group.id, workspace_id=owner_ws.id,
        name="Viewer", linked_user_id=test_user.id, is_self=False,
    )
    session.add_all([owner_self, viewer_m])
    await session.commit()

    shared_cat = await _make_category(session, owner.id, "Shared")
    shared_txn = await _add_txn(
        session, owner.id, owner_acc.id, 100, "debit", date.today(),
        category_id=shared_cat.id,
    )
    for member, amt in ((owner_self, "60.00"), (viewer_m, "40.00")):
        session.add(TransactionSplit(
            id=uuid.uuid4(), transaction_id=shared_txn.id,
            workspace_id=owner_ws.id, group_member_id=member.id,
            share_amount=Decimal(amt), share_type="exact",
        ))
    await session.commit()

    await _set_flag(session, owner_acc)
    # Helper 1: viewer_shared_pnl must contribute nothing.
    income, expense = await viewer_shared_pnl(
        session, test_user.id, month_start, month_end
    )
    assert income == 0.0
    assert expense == 0.0
    # Helper 2: viewer_shared_spending_by_category must contribute nothing.
    by_cat = await viewer_shared_spending_by_category(
        session, test_user.id, month_start, month_end
    )
    assert by_cat == {}

    await _set_flag(session, owner_acc, False)
    income, expense = await viewer_shared_pnl(
        session, test_user.id, month_start, month_end
    )
    assert income == 0.0
    assert expense == 40.0
    by_cat = await viewer_shared_spending_by_category(
        session, test_user.id, month_start, month_end
    )
    assert float(by_cat.get(shared_cat.id, 0.0)) == pytest.approx(40.0)


# ---------------------------------------------------------------------------
# T-6 — Sankey composition + investments group
# ---------------------------------------------------------------------------


async def test_sankey_composition_and_investments_exclude_flagged_account(
    session: AsyncSession, test_user, test_workspace
):
    mirror = await _make_account(session, test_user.id, "Mirror Inv")
    checking = await _make_account(session, test_user.id, "Real Inv")
    inv_cat = await _make_category(
        session, test_user.id, "Investments In", treat_as_transfer=True
    )
    food = await _make_category(session, test_user.id, "Food")
    food_m = await _make_category(session, test_user.id, "Mirror Food")

    await _add_txn(session, test_user.id, mirror.id, 200, "debit", _day_of_month(3), category_id=inv_cat.id)
    await _add_txn(session, test_user.id, checking.id, 50, "debit", _day_of_month(3), category_id=inv_cat.id)
    await _add_txn(session, test_user.id, mirror.id, 30, "debit", _day_of_month(4), category_id=food_m.id)
    await _add_txn(session, test_user.id, checking.id, 20, "debit", _day_of_month(4), category_id=food.id)

    # Counterfactual: both sides feed the Sankey today.
    before = await get_income_expenses_report(
        session, test_workspace.id, test_user.id, months=12
    )
    investments = [c for c in before.composition if c.group == "investments"]
    assert [(c.label, c.value) for c in investments] == [("Investments In", 250.0)]

    await _set_flag(session, mirror)

    rep = await get_income_expenses_report(
        session, test_workspace.id, test_user.id, months=12
    )
    # The investments group keeps only the included account's contribution.
    investments = [c for c in rep.composition if c.group == "investments"]
    assert [(c.label, c.value) for c in investments] == [("Investments In", 50.0)]

    expense_comp = {c.label: c.value for c in rep.composition if c.group == "expenses"}
    assert expense_comp.get("Food") == 20.0
    assert "Mirror Food" not in expense_comp
    # P&L totals never saw the treat_as_transfer rows either way.
    assert next(b for b in rep.summary.breakdowns if b.key == "expenses").value == 20.0


@pytest.mark.parametrize("is_closed", [False, True], ids=["open", "closed"])
@pytest.mark.parametrize("exclude", [False, True], ids=["included", "excluded"])
async def test_historical_investments_respect_exclusion_not_closure(
    session: AsyncSession, test_user, test_workspace, is_closed, exclude
):
    account = await _make_account(
        session, test_user.id, "Historical Investments",
        is_closed=is_closed, exclude=exclude,
    )
    category = await _make_category(
        session, test_user.id, "Investment Contributions", treat_as_transfer=True
    )
    await _add_txn(
        session, test_user.id, account.id, 125, "debit",
        date.today() - timedelta(days=30), category_id=category.id,
    )

    report = await get_income_expenses_report(
        session, test_workspace.id, test_user.id, months=12
    )
    investments = [item for item in report.composition if item.group == "investments"]
    assert [(item.label, item.value) for item in investments] == (
        [] if exclude else [("Investment Contributions", 125.0)]
    )
    # Contributions are not spending, regardless of either account flag.
    assert next(b for b in report.summary.breakdowns if b.key == "expenses").value == 0.0


# ---------------------------------------------------------------------------
# T-7 — cash-flow anchor and flows
# ---------------------------------------------------------------------------


async def test_cash_flow_anchor_and_flows_exclude_flagged_account(
    session: AsyncSession, test_user, test_workspace
):
    mirror = await _make_account(session, test_user.id, "CF Mirror")
    await _seed_balance(session, test_user.id, mirror, 7000, when=date.today() - timedelta(days=40))
    checking = await _make_account(session, test_user.id, "CF Real")
    await _seed_balance(session, test_user.id, checking, 1000, when=date.today() - timedelta(days=40))
    # A past inflow inside the chart window: changes the walk when counted.
    await _add_txn(session, test_user.id, mirror.id, 500, "credit", date.today() - timedelta(days=5))

    before = await get_cash_flow_report(
        session, test_workspace.id, test_user.id, months=3, interval="daily"
    )
    starting_before = next(b for b in before.summary.breakdowns if b.key == "startingBalance")
    assert starting_before.value == 8500.0
    assert any(p.value != starting_before.value for p in before.trend)

    await _set_flag(session, mirror)

    after = await get_cash_flow_report(
        session, test_workspace.id, test_user.id, months=3, interval="daily"
    )
    # Anchor excludes the mirror…
    starting = next(b for b in after.summary.breakdowns if b.key == "startingBalance")
    assert starting.value == 1000.0
    # …and so do the flows: with no included-account movement the walk is flat.
    assert all(p.value == pytest.approx(1000.0) for p in after.trend)


# ---------------------------------------------------------------------------
# T-8 — baseline projection means
# ---------------------------------------------------------------------------


async def test_baseline_projection_excludes_flagged_account(
    session: AsyncSession, test_user, test_workspace
):
    mirror = await _make_account(session, test_user.id, "Baseline Mirror")
    checking = await _make_account(session, test_user.id, "Baseline Real")
    await _add_txn(session, test_user.id, mirror.id, 300, "debit", date.today() - timedelta(days=10))
    await _add_txn(session, test_user.id, checking.id, 100, "debit", date.today() - timedelta(days=5))

    async def to_primary(amount, ccy):
        return float(amount)

    today = date.today()
    end = today + timedelta(days=7)

    before, lookback = await _get_baseline_projection(
        session, test_workspace.id, today, end, "BRL", to_primary, None
    )
    assert lookback == 10
    assert sum(p["amount"] for p in before if p["type"] == "debit") == pytest.approx(280.0)

    await _set_flag(session, mirror)

    projections, lookback = await _get_baseline_projection(
        session, test_workspace.id, today, end, "BRL", to_primary, None
    )
    # Window anchors on the included account's history only.
    assert lookback == 5
    debits = [p for p in projections if p["type"] == "debit"]
    assert len(debits) == 7  # one per day, today+1 … end
    assert sum(p["amount"] for p in debits) == pytest.approx(140.0)
    assert not any(p["type"] == "credit" for p in projections)


# ---------------------------------------------------------------------------
# T-9 — budgets: actual + projected + prev-month
# ---------------------------------------------------------------------------


async def test_budget_actuals_and_projections_exclude_flagged_account(
    session: AsyncSession, test_user, test_workspace
):
    month_start, month_end = _month_range(date.today())
    prev_start = (month_start - timedelta(days=1)).replace(day=1)

    mirror = await _make_account(session, test_user.id, "Budget Mirror")
    checking = await _make_account(session, test_user.id, "Budget Real")
    food = await _make_category(session, test_user.id, "Food")

    await budget_service.create_budget(
        session, test_workspace.id, test_user.id,
        BudgetCreate(category_id=food.id, amount=Decimal("500.00"), month=month_start),
    )
    await _add_txn(session, test_user.id, mirror.id, 300, "debit", _day_of_month(3), category_id=food.id)
    await _add_txn(session, test_user.id, checking.id, 100, "debit", _day_of_month(4), category_id=food.id)
    await _add_txn(session, test_user.id, mirror.id, 400, "debit", prev_start, category_id=food.id)
    await _make_recurring(
        session, test_user.id, test_workspace.id, mirror,
        description="Mirror subscription", amount="10", category_id=food.id,
    )
    await _make_recurring(
        session, test_user.id, test_workspace.id, checking,
        description="Real subscription", amount="5", category_id=food.id,
    )

    before = await budget_service.get_budget_vs_actual(
        session, test_workspace.id, test_user.id, month=month_start
    )
    row = next(r for r in before if r.category_id == food.id)
    assert float(row.actual_amount) == pytest.approx(400.0)
    assert float(row.prev_month_amount) == pytest.approx(400.0)
    assert float(row.projected_amount) == pytest.approx(415.0)

    await _set_flag(session, mirror)

    after = await budget_service.get_budget_vs_actual(
        session, test_workspace.id, test_user.id, month=month_start
    )
    row = next(r for r in after if r.category_id == food.id)
    # Actuals: mirror's 300 gone.
    assert float(row.actual_amount) == pytest.approx(100.0)
    # Prev month: mirror's 400 gone.
    assert float(row.prev_month_amount) == pytest.approx(0.0)
    # Projected: actual + included recurring only (100 + 5).
    assert float(row.projected_amount) == pytest.approx(105.0)


# ---------------------------------------------------------------------------
# T-10 — dashboard summary + forecast layer (S1/S3/S4/S16)
# ---------------------------------------------------------------------------


async def test_dashboard_summary_and_forecast_exclude_flagged_account(
    session: AsyncSession, test_user, test_workspace
):
    month_start, _ = _month_range(date.today())
    old = date.today() - timedelta(days=70)

    mirror = await _make_account(session, test_user.id, "Dash Mirror")
    await _seed_balance(session, test_user.id, mirror, 500, when=old)
    checking = await _make_account(session, test_user.id, "Dash Real")
    await _seed_balance(session, test_user.id, checking, 1000, when=old)
    food = await _make_category(session, test_user.id, "Food")

    await _add_txn(session, test_user.id, mirror.id, 300, "debit", _day_of_month(3), category_id=food.id)
    await _add_txn(session, test_user.id, mirror.id, 200, "credit", _day_of_month(4))
    await _add_txn(session, test_user.id, checking.id, 50, "debit", _day_of_month(5), category_id=food.id)
    await _make_recurring(
        session, test_user.id, test_workspace.id, mirror, description="Mirror forecast", amount="10"
    )
    # Future-dated row: forecast, never current balance.
    await _add_txn(
        session, test_user.id, mirror.id, 7, "debit", date.today() + timedelta(days=1)
    )

    before = await get_summary(session, test_workspace.id, test_user.id, month=month_start)
    assert before.total_balance["BRL"] == pytest.approx(1350.0)
    assert before.monthly_expenses == pytest.approx(350.0)
    assert before.monthly_income == pytest.approx(200.0)

    await _set_flag(session, mirror)

    after = await get_summary(session, test_workspace.id, test_user.id, month=month_start)
    # S1: hero balance drops the mirror.
    assert after.total_balance["BRL"] == pytest.approx(950.0)
    # S4: monthly P&L drops the mirror's rows.
    assert after.monthly_expenses == pytest.approx(50.0)
    assert after.monthly_income == pytest.approx(0.0)
    # S3/S16: no excluded-account recurring rules or forecast rows — with
    # zero included-account forecast activity, projected == current exactly.
    assert after.projected_balance["BRL"] == pytest.approx(950.0)


# ---------------------------------------------------------------------------
# T-11 — balance history, monthly trend, spending by category
# ---------------------------------------------------------------------------


async def test_balance_history_and_trend_exclude_flagged_account(
    session: AsyncSession, test_user, test_workspace
):
    month_start, _ = _month_range(date.today())
    old = date.today() - timedelta(days=70)

    mirror = await _make_account(session, test_user.id, "Hist Mirror")
    await _seed_balance(session, test_user.id, mirror, 7000, when=old)
    checking = await _make_account(session, test_user.id, "Hist Real")
    await _seed_balance(session, test_user.id, checking, 1000, when=old)
    food = await _make_category(session, test_user.id, "Food")
    food_m = await _make_category(session, test_user.id, "Mirror Food")

    await _add_txn(session, test_user.id, mirror.id, 300, "debit", _day_of_month(2), category_id=food_m.id)
    await _add_txn(session, test_user.id, checking.id, 50, "debit", _day_of_month(3), category_id=food.id)

    # Trend counterfactual before the flag.
    cur_label = month_start.strftime("%Y-%m")
    before_trend = await get_monthly_trend(session, test_workspace.id, test_user.id, months=6)
    cur = next(t for t in before_trend if t.month == cur_label)
    assert cur.expenses == pytest.approx(350.0)

    await _set_flag(session, mirror)

    # Balance history: anchored AND delta'd on included accounts only. The
    # mirror's 7000 opening and 300 delta must both be absent.
    hist = await get_balance_history(
        session, test_workspace.id, test_user.id, month=month_start
    )
    assert hist.opening_balance == pytest.approx(1000.0)
    cur_balances = [d.balance for d in hist.current if d.balance is not None]
    assert cur_balances
    assert all(950.0 - 1e-6 <= b <= 1000.0 + 1e-6 for b in cur_balances)

    trend = await get_monthly_trend(session, test_workspace.id, test_user.id, months=6)
    cur = next(t for t in trend if t.month == cur_label)
    assert cur.expenses == pytest.approx(50.0)

    spending = await get_spending_by_category(
        session, test_workspace.id, test_user.id, month=month_start
    )
    names = {s.category_name for s in spending}
    assert "Mirror Food" not in names
    food_row = next(s for s in spending if s.category_name == "Food")
    assert food_row.total == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# T-12 — goals net-worth tracking
# ---------------------------------------------------------------------------


async def test_goal_net_worth_tracking_respects_exclusion(
    session: AsyncSession, test_user, test_workspace
):
    mirror = await _make_account(session, test_user.id, "Goal Mirror")
    await _seed_balance(session, test_user.id, mirror, 5000)
    checking = await _make_account(session, test_user.id, "Goal Real")
    await _seed_balance(session, test_user.id, checking, 1000)
    asset = Asset(
        id=uuid.uuid4(), user_id=test_user.id, name="Goal Wallet",
        type="investment", currency="BRL",
        purchase_price=Decimal("2000"), purchase_date=date.today(),
    )
    session.add(asset)
    await session.flush()
    session.add(AssetValue(
        id=uuid.uuid4(), asset_id=asset.id, workspace_id=test_workspace.id,
        amount=Decimal("2000"), date=date.today(),
    ))
    goal = Goal(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        name="Net worth", target_amount=Decimal("100000.00"),
        currency="BRL", tracking_type="net_worth",
    )
    session.add(goal)
    await session.commit()

    before = await _resolve_current_amount(session, goal, test_user.id)
    assert float(before) == pytest.approx(8000.0)

    await _set_flag(session, mirror)

    after = await _resolve_current_amount(session, goal, test_user.id)
    # Accounts drop the mirror; assets stay (S28 is a non-goal).
    assert float(after) == pytest.approx(3000.0)


# ---------------------------------------------------------------------------
# T-13 — the account's own pages keep their totals (S18)
# ---------------------------------------------------------------------------


async def test_account_own_pages_keep_totals_when_excluded(
    session: AsyncSession, test_user, test_workspace
):
    acct = await _make_account(session, test_user.id, "Own Pages")
    await _seed_balance(session, test_user.id, acct, 1000, when=date.today() - timedelta(days=70))
    food = await _make_category(session, test_user.id, "Food")
    await _add_txn(session, test_user.id, acct.id, 100, "debit", _day_of_month(3), category_id=food.id)
    await _add_txn(session, test_user.id, acct.id, 50, "credit", _day_of_month(4))

    summary_before = await account_service.get_account_summary(
        session, acct.id, test_workspace.id
    )
    history_before = await account_service.get_account_balance_history(
        session, acct.id, test_workspace.id
    )
    assert summary_before is not None
    assert history_before is not None

    await _set_flag(session, acct)

    summary_after = await account_service.get_account_summary(
        session, acct.id, test_workspace.id
    )
    history_after = await account_service.get_account_balance_history(
        session, acct.id, test_workspace.id
    )
    assert summary_after == summary_before
    assert history_after == history_before


# ---------------------------------------------------------------------------
# T-14 — CSV export keeps excluded accounts' rows (S26 pin)
# ---------------------------------------------------------------------------


async def test_export_rows_keep_excluded_account_transactions(
    client, auth_headers, session: AsyncSession, test_account, test_transactions
):
    # test_account is connection-backed: the PATCH also proves the flag
    # passes the connected-account edit gate (part of T-16's story).
    resp = await client.patch(
        f"/api/accounts/{test_account.id}", headers=auth_headers,
        json={"exclude_from_reports": True},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["exclude_from_reports"] is True

    out = await client.get("/api/transactions/export", headers=auth_headers)
    assert out.status_code == 200
    for txn in test_transactions:
        assert txn.description in out.text


# ---------------------------------------------------------------------------
# T-15 — transactions-list summary footer + excluded bucket
# ---------------------------------------------------------------------------


async def test_transaction_summary_footer_excludes_and_excluded_bucket_absorbs(
    session: AsyncSession, test_user, test_workspace
):
    mirror = await _make_account(session, test_user.id, "Footer Mirror")
    checking = await _make_account(session, test_user.id, "Footer Real")
    await _add_txn(session, test_user.id, mirror.id, 300, "debit", _day_of_month(3))
    await _add_txn(session, test_user.id, mirror.id, 200, "credit", _day_of_month(4))
    await _add_txn(session, test_user.id, checking.id, 100, "debit", _day_of_month(5))
    await _add_txn(session, test_user.id, checking.id, 50, "credit", _day_of_month(6))

    await _set_flag(session, mirror)

    rows, total, summary = await get_transactions(
        session, test_workspace.id, test_user.id, include_summary=True
    )
    assert summary is not None
    # Rows stay visible — the flag is a totals flag, not a hide flag.
    assert total == 4
    assert len(rows) == 4
    # Footer P&L excludes the mirror…
    assert float(summary["income"]) == pytest.approx(50.0)
    assert float(summary["expense"]) == pytest.approx(100.0)
    assert float(summary["net"]) == pytest.approx(-50.0)
    # …and its amounts land in the excluded bucket: nothing silently vanishes.
    assert float(summary["excluded"]) == pytest.approx(500.0)


# ---------------------------------------------------------------------------
# T-18 — credit-card bill totals unchanged (S19 pin)
# ---------------------------------------------------------------------------


async def test_card_bill_totals_unchanged_by_exclusion(
    session: AsyncSession, test_user, test_workspace
):
    card = await _make_account(session, test_user.id, "Excluded Visa", acc_type="credit_card")
    food = await _make_category(session, test_user.id, "Food")
    transfer_cat = await _make_category(
        session, test_user.id, "Card Payment", treat_as_transfer=True
    )
    # A transfer-tagged debit still lands on the bill (issue #647 semantics).
    await _add_txn(session, test_user.id, card.id, 100, "debit", _day_of_month(3), category_id=food.id)
    await _add_txn(session, test_user.id, card.id, 50, "debit", _day_of_month(4), category_id=transfer_cat.id)

    before = await account_service.get_account_summary(
        session, card.id, test_workspace.id
    )
    assert before is not None
    assert float(before["monthly_expenses"]) == pytest.approx(150.0)

    await _set_flag(session, card)

    after = await account_service.get_account_summary(
        session, card.id, test_workspace.id
    )
    assert after is not None
    # Bank-side authority: the bill answers to the bank, not to reporting flags.
    assert float(after["monthly_expenses"]) == pytest.approx(150.0)
    assert after["monthly_expenses"] == before["monthly_expenses"]
    assert after["monthly_income"] == before["monthly_income"]
