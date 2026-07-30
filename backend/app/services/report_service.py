import calendar
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import String, select, desc, func, case
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.account import Account
from app.models.asset import Asset
from app.models.asset_value import AssetValue
from app.models.transaction import Transaction
from app.models.category import Category
from app.models.user import User
from app.services._query_filters import (
    counts_as_pnl,
    counts_as_user_pnl,
    owner_split_offset_by_category,
    reporting_date_col,
)
from app.services.admin_service import get_credit_card_accounting_mode
from app.services.account_service import get_account_name
from app.services.fx_rate_service import convert
from app.schemas.report import (
    CategoryTrendItem,
    ReportBreakdown,
    ReportCompositionItem,
    ReportDataPoint,
    ReportMeta,
    ReportResponse,
    ReportSummary,
)
from app.services.dashboard_service import _get_open_accounts, _account_balance_at

CATEGORY_TREND_TOP_N = 11

_ACCOUNT_TYPE_COLORS: dict[str, str] = {
    "checking": "#6366F1",
    "savings": "#3B82F6",
    "credit_card": "#F43F5E",
    "investment": "#8B5CF6",
    "wallet": "#F59E0B",
}
_ASSET_TYPE_COLORS: dict[str, str] = {
    "real_estate": "#0EA5E9",
    "vehicle": "#14B8A6",
    "valuable": "#F59E0B",
    "investment": "#8B5CF6",
    "other": "#6B7280",
}


def _report_start_date(
    today: date, months: int, period: str | None = None, days: int | None = None
) -> date:
    """Resolve historical report start date.

    `days` requests an exact rolling window ending today (inclusive), instead of
    the month-aligned window the `months` ranges use.
    """
    if days:
        return today - timedelta(days=days - 1)

    if period == "ytd":
        return date(today.year, 1, 1)

    start = date(today.year, today.month, 1) - timedelta(days=months * 30)
    return start.replace(day=1)


async def _asset_value_at(
    session: AsyncSession, workspace_id: uuid.UUID, cutoff: date,
    primary_currency: str = "USD",
    group_ids: Optional[list[uuid.UUID]] = None,
) -> float:
    """Sum of all active asset values at a given date, converted to primary currency."""
    asset_stmt = select(Asset).where(
        Asset.workspace_id == workspace_id,
        Asset.is_archived == False,
        Asset.sell_date.is_(None),
    )
    if group_ids is not None:
        asset_stmt = asset_stmt.where(Asset.group_id.in_(group_ids))
    asset_result = await session.execute(asset_stmt)
    total = 0.0
    for asset in asset_result.scalars().all():
        val_result = await session.execute(
            select(AssetValue.amount)
            .where(AssetValue.asset_id == asset.id, AssetValue.date <= cutoff)
            .order_by(desc(AssetValue.date), desc(AssetValue.id))
            .limit(1)
        )
        val = val_result.scalar_one_or_none()
        if val is not None:
            amount = float(val)
        elif asset.purchase_price is not None and (
            asset.purchase_date is None or asset.purchase_date <= cutoff
        ):
            amount = float(asset.purchase_price)
        else:
            amount = 0.0
        if amount > 0:
            converted, _ = await convert(
                session, Decimal(str(amount)), asset.currency, primary_currency, cutoff
            )
            total += float(converted)
    return total


async def _net_worth_at(
    session: AsyncSession, workspace_id: uuid.UUID, cutoff: date,
    primary_currency: str = "USD",
    account_ids: Optional[list[uuid.UUID]] = None,
    asset_group_ids: Optional[list[uuid.UUID]] = None,
) -> ReportDataPoint:
    """Compute a single net worth snapshot at a given date, converted to primary currency.

    Under a Collection filter (``account_ids`` set), only those accounts are
    summed and only assets in the collection's wallets (``asset_group_ids``)
    are included."""
    accounts = await _get_open_accounts(session, workspace_id, account_ids)

    accounts_total = 0.0
    liabilities_total = 0.0
    composition: list[ReportCompositionItem] = []

    for account in accounts:
        bal = await _account_balance_at(session, account, cutoff)
        converted, _ = await convert(
            session, Decimal(str(abs(bal))), account.currency, primary_currency, cutoff
        )
        converted_val = float(converted)
        if account.type == "credit_card" or bal < 0:
            liabilities_total += converted_val
            if converted_val > 0:
                composition.append(ReportCompositionItem(
                    key=str(account.id),
                    label=get_account_name(account),
                    value=round(converted_val, 2),
                    color=_ACCOUNT_TYPE_COLORS.get(account.type, "#6B7280"),
                    group="liabilities",
                ))
        else:
            accounts_total += converted_val
            if converted_val > 0:
                composition.append(ReportCompositionItem(
                    key=str(account.id),
                    label=get_account_name(account),
                    value=round(converted_val, 2),
                    color=_ACCOUNT_TYPE_COLORS.get(account.type, "#6B7280"),
                    group="accounts",
                ))

    # Per-asset composition at the cutoff date
    filtered = account_ids is not None
    asset_stmt = select(Asset).where(
        Asset.workspace_id == workspace_id,
        Asset.is_archived == False,
        Asset.sell_date.is_(None),
    )
    if filtered:
        asset_stmt = asset_stmt.where(Asset.group_id.in_(asset_group_ids or []))
    asset_result = await session.execute(asset_stmt)
    assets_total = 0.0
    for asset in asset_result.scalars().all():
        val_result = await session.execute(
            select(AssetValue.amount)
            .where(AssetValue.asset_id == asset.id, AssetValue.date <= cutoff)
            .order_by(desc(AssetValue.date), desc(AssetValue.id))
            .limit(1)
        )
        val = val_result.scalar_one_or_none()
        if val is not None:
            amount = float(val)
        elif asset.purchase_price is not None and (
            asset.purchase_date is None or asset.purchase_date <= cutoff
        ):
            amount = float(asset.purchase_price)
        else:
            amount = 0.0
        if amount > 0:
            converted, _ = await convert(
                session, Decimal(str(amount)), asset.currency, primary_currency, cutoff
            )
            converted_val = round(float(converted), 2)
            assets_total += converted_val
            composition.append(ReportCompositionItem(
                key=str(asset.id),
                label=asset.name,
                value=converted_val,
                color=_ASSET_TYPE_COLORS.get(asset.type, "#6B7280"),
                group="assets",
            ))

    net_worth = accounts_total + assets_total - liabilities_total

    return ReportDataPoint(
        date=cutoff.isoformat(),
        value=round(net_worth, 2),
        breakdowns={
            "accounts": round(accounts_total, 2),
            "assets": round(assets_total, 2),
            "liabilities": round(liabilities_total, 2),
        },
        composition=composition,
    )


def _format_date_label(d: date, interval: str) -> str:
    """Format a date point based on interval granularity."""
    if interval == "daily":
        return d.isoformat()
    elif interval == "weekly":
        iso_year, iso_week, _ = d.isocalendar()
        return f"{iso_year}-W{iso_week:02d}"
    elif interval == "monthly":
        return f"{d.year}-{d.month:02d}"
    elif interval == "yearly":
        return str(d.year)
    return d.isoformat()


def _date_points(
    start: date, end: date, interval: str
) -> list[date]:
    """Generate date points between start and end for the given interval."""
    points: list[date] = []
    current = start

    if interval == "daily":
        while current <= end:
            points.append(current)
            current += timedelta(days=1)
    elif interval == "weekly":
        while current <= end:
            points.append(current)
            current += timedelta(weeks=1)
    elif interval == "monthly":
        # One snapshot per month: last day of the month, capped at end
        current = date(start.year, start.month, 1)
        while current <= end:
            last_day = calendar.monthrange(current.year, current.month)[1]
            points.append(min(date(current.year, current.month, last_day), end))
            if current.month == 12:
                current = date(current.year + 1, 1, 1)
            else:
                current = date(current.year, current.month + 1, 1)
    elif interval == "yearly":
        while current <= end:
            points.append(current)
            current = date(current.year + 1, current.month, current.day)
    else:
        # Default to monthly
        return _date_points(start, end, "monthly")

    # Ensure the last point uses `end` so the final snapshot reflects today's data.
    # If the last generated point is in the same period as `end`, replace it;
    # otherwise append `end` as a new point.
    if points and points[-1] < end:
        if _format_date_label(points[-1], interval) == _format_date_label(end, interval):
            points[-1] = end  # same label, use today's cutoff
        else:
            points.append(end)

    return points


async def get_net_worth_report(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    months: int = 12,
    interval: str = "monthly",
    currency: str = "USD",
    account_ids: Optional[list[uuid.UUID]] = None,
    asset_group_ids: Optional[list[uuid.UUID]] = None,
    period: str | None = None,
) -> ReportResponse:
    """Build a full ReportResponse for net worth over time."""
    # A wallet-only collection (wallets, no accounts) still filters.
    if asset_group_ids is not None and account_ids is None:
        account_ids = []
    today = date.today()
    start = _report_start_date(today, months, period)

    # Get user's primary currency
    user = await session.get(User, user_id)
    primary_currency = user.primary_currency if user else get_settings().default_currency

    points = _date_points(start, today, interval)

    # Compute snapshot at each date point
    trend: list[ReportDataPoint] = []
    for point in points:
        dp = await _net_worth_at(session, workspace_id, point, primary_currency, account_ids, asset_group_ids)
        dp.date = _format_date_label(point, interval)
        dp.change = round(dp.value - trend[-1].value, 2) if trend else None
        trend.append(dp)

    # Current snapshot (last point) for summary; baseline at period start for delta
    current = trend[-1] if trend else ReportDataPoint(
        date="", value=0, breakdowns={"accounts": 0, "assets": 0, "liabilities": 0}
    )
    baseline = await _net_worth_at(session, workspace_id, start, primary_currency, account_ids, asset_group_ids)
    previous = baseline if trend else current

    change_amount = current.value - previous.value
    change_percent = (
        (change_amount / abs(previous.value) * 100)
        if previous.value != 0
        else None
    )

    summary = ReportSummary(
        primary_value=current.value,
        change_amount=round(change_amount, 2),
        change_percent=round(change_percent, 2) if change_percent is not None else None,
        breakdowns=[
            ReportBreakdown(
                key="accounts",
                label="Accounts",
                value=current.breakdowns.get("accounts", 0),
                color="#6366F1",
            ),
            ReportBreakdown(
                key="assets",
                label="Assets",
                value=current.breakdowns.get("assets", 0),
                color="#F59E0B",
            ),
            ReportBreakdown(
                key="liabilities",
                label="Liabilities",
                value=current.breakdowns.get("liabilities", 0),
                color="#F43F5E",
            ),
        ],
    )

    meta = ReportMeta(
        type="net_worth",
        series_keys=["accounts", "assets", "liabilities"],
        currency=primary_currency,
        interval=interval,
    )

    # The last trend point is always today — reuse its per-item composition for
    # the response-level `composition` field (current snapshot for the donut).
    composition = trend[-1].composition if trend else []

    return ReportResponse(summary=summary, trend=trend, meta=meta, composition=composition)


def _interval_label_expr(interval: str, date_col=None):
    """SQL expression that groups transaction dates into interval buckets."""
    col = date_col if date_col is not None else Transaction.date
    if interval == "daily":
        return func.to_char(col, 'YYYY-MM-DD')
    elif interval == "weekly":
        return func.concat(
            func.extract('isoyear', col).cast(String),
            '-W',
            func.lpad(func.extract('week', col).cast(String), 2, '0'),
        )
    elif interval == "yearly":
        return func.to_char(col, 'YYYY')
    else:  # monthly (default)
        return func.to_char(col, 'YYYY-MM')


async def get_income_expenses_report(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    months: int = 12,
    interval: str = "monthly",
    currency: str = "USD",
    account_ids: Optional[list[uuid.UUID]] = None,
    period: str | None = None,
    days: int | None = None,
) -> ReportResponse:
    """Build a ReportResponse for income vs expenses over time."""
    filtered = account_ids is not None
    acct_filter = [Transaction.account_id.in_(account_ids)] if filtered else []
    today = date.today()
    start = _report_start_date(today, months, period, days)

    # Get user's primary currency + global reporting mode
    user = await session.get(User, user_id)
    primary_currency = user.primary_currency if user else get_settings().default_currency
    accounting_mode = await get_credit_card_accounting_mode(session)
    report_date = reporting_date_col(accounting_mode)

    label_expr = _interval_label_expr(interval, report_date).label('period')

    # Use amount_primary when available, fall back to amount
    amount_expr = func.coalesce(Transaction.amount_primary, Transaction.amount)

    result = await session.execute(
        select(
            label_expr,
            func.sum(case((Transaction.type == "credit", amount_expr), else_=0)),
            func.sum(case((Transaction.type == "debit", amount_expr), else_=0)),
        )
        .join(Account, Transaction.account_id == Account.id)
        .where(
            Transaction.workspace_id == workspace_id,
            Account.is_closed == False,
            report_date >= start,
            report_date <= today,
            Transaction.source != "opening_balance",
            counts_as_user_pnl(),
            *acct_filter,
        )
        .group_by(label_expr)
        .order_by(label_expr)
    )

    # Build data map from query results
    data_map: dict[str, tuple[float, float]] = {}
    for row in result.all():
        income = float(row[1] or 0)
        expenses = abs(float(row[2] or 0))
        data_map[row[0]] = (income, expenses)

    # Subtract non-owner shares of the user's own splits per period — the
    # user shouldn't be charged for the parts they're owed back.
    from sqlalchemy import or_ as _or_, and_ as _and_
    from app.models.group import Group as _Group_, GroupMember as _GroupMember_
    from app.models.transaction_split import TransactionSplit as _TS_
    from app.services.fx_rate_service import convert as fx_convert

    own_member_ids_sq = (
        select(_GroupMember_.id)
        .outerjoin(_Group_, _Group_.id == _GroupMember_.group_id)
        .where(
            _or_(
                _GroupMember_.linked_user_id == user_id,
                _and_(_GroupMember_.is_self == True, _Group_.user_id == user_id),
            )
        )
    )
    owner_offset_result = await session.execute(
        select(
            label_expr,
            Transaction.currency,
            func.sum(
                case(
                    (Transaction.type == "credit", _TS_.share_amount),
                    else_=0,
                )
            ),
            func.sum(
                case(
                    (Transaction.type == "debit", _TS_.share_amount),
                    else_=0,
                )
            ),
        )
        .select_from(_TS_)
        .join(Transaction, _TS_.transaction_id == Transaction.id)
        .where(
            Transaction.user_id == user_id,
            Transaction.workspace_id == workspace_id,
            _TS_.group_member_id.notin_(own_member_ids_sq),
            report_date >= start,
            report_date <= today,
            Transaction.source != "opening_balance",
            counts_as_user_pnl(),
        )
        .group_by(label_expr, Transaction.currency)
    )
    for row in (owner_offset_result.all() if not filtered else []):
        period, currency, raw_credit, raw_debit = row
        sub_inc = 0.0
        sub_exp = 0.0
        if raw_credit:
            inc_pri, _ = await fx_convert(
                session, Decimal(str(raw_credit)), currency, primary_currency
            )
            sub_inc = float(inc_pri)
        if raw_debit:
            exp_pri, _ = await fx_convert(
                session, Decimal(str(raw_debit)), currency, primary_currency
            )
            sub_exp = abs(float(exp_pri))
        existing_income, existing_expenses = data_map.get(period, (0.0, 0.0))
        data_map[period] = (
            max(0.0, existing_income - sub_inc),
            max(0.0, existing_expenses - sub_exp),
        )

    # Layer in the viewer's share from group splits — concert tickets
    # paid by a friend show up as the viewer's expense in their P/L
    # picture, just like in /transactions and the dashboard. Group by
    # currency so we can FX-convert each bucket to primary correctly.
    from app.models.group import GroupMember
    from app.models.transaction_split import TransactionSplit

    # Exclude is_self memberships — the owner's own self-member must
    # not surface their in-Pessoal transactions in Trabalho's report.
    viewer_member_ids = select(GroupMember.id).where(
        GroupMember.linked_user_id == user_id,
        GroupMember.is_self.is_(False),
    )
    shared_result = await session.execute(
        select(
            label_expr,
            Transaction.currency,
            func.sum(
                case(
                    (Transaction.type == "credit", TransactionSplit.share_amount),
                    else_=0,
                )
            ),
            func.sum(
                case(
                    (Transaction.type == "debit", TransactionSplit.share_amount),
                    else_=0,
                )
            ),
        )
        .select_from(TransactionSplit)
        .join(Transaction, TransactionSplit.transaction_id == Transaction.id)
        .where(
            TransactionSplit.group_member_id.in_(viewer_member_ids),
            Transaction.user_id != user_id,
            Transaction.workspace_id != workspace_id,
            report_date >= start,
            report_date <= today,
            Transaction.source != "opening_balance",
            counts_as_user_pnl(),
        )
        .group_by(label_expr, Transaction.currency)
    )
    from app.services.fx_rate_service import convert as fx_convert
    for row in (shared_result.all() if not filtered else []):
        period, currency, raw_credit, raw_debit = row
        share_income_pri = 0.0
        share_expenses_pri = 0.0
        if raw_credit:
            inc_pri, _ = await fx_convert(
                session, Decimal(str(raw_credit)), currency, primary_currency
            )
            share_income_pri = float(inc_pri)
        if raw_debit:
            exp_pri, _ = await fx_convert(
                session, Decimal(str(raw_debit)), currency, primary_currency
            )
            share_expenses_pri = abs(float(exp_pri))
        existing_income, existing_expenses = data_map.get(period, (0.0, 0.0))
        data_map[period] = (
            existing_income + share_income_pri,
            existing_expenses + share_expenses_pri,
        )

    # Add recurring projections for each month in the range (consistent with dashboard)
    from app.services.dashboard_service import _month_range, _get_recurring_projections

    cursor = start
    while cursor <= today:
        m_start, m_end = _month_range(cursor)
        # A day-window range can start mid-month: only project occurrences that
        # fall inside the requested window.
        projections = await _get_recurring_projections(
            session, workspace_id, max(m_start, start), m_end, account_ids
        )
        for proj in projections:
            # Convert to primary currency
            converted, _ = await fx_convert(
                session, Decimal(str(proj["amount"])), proj["currency"], primary_currency,
            )
            proj_amount = float(converted)
            label = _format_date_label(cursor, interval)
            existing_income, existing_expenses = data_map.get(label, (0.0, 0.0))
            if proj["type"] == "credit":
                data_map[label] = (existing_income + proj_amount, existing_expenses)
            else:
                data_map[label] = (existing_income, existing_expenses + proj_amount)
        # Advance to next month
        if cursor.month == 12:
            cursor = date(cursor.year + 1, 1, 1)
        else:
            cursor = date(cursor.year, cursor.month + 1, 1)

    # Generate all expected date points and map to results
    points = _date_points(start, today, interval)
    trend: list[ReportDataPoint] = []
    total_income = 0.0
    total_expenses = 0.0

    for point in points:
        label = _format_date_label(point, interval)
        income, expenses = data_map.get(label, (0.0, 0.0))
        net = round(income - expenses, 2)
        total_income += income
        total_expenses += expenses
        trend.append(ReportDataPoint(
            date=label,
            value=net,
            breakdowns={
                "income": round(income, 2),
                "expenses": round(expenses, 2),
            },
        ))

    total_net = round(total_income - total_expenses, 2)

    # Compare last point vs first point net income
    current_net = trend[-1].value if trend else 0.0
    previous_net = trend[0].value if len(trend) > 1 else 0.0
    change_amount = current_net - previous_net
    change_percent = (
        (change_amount / abs(previous_net) * 100)
        if previous_net != 0
        else None
    )

    summary = ReportSummary(
        primary_value=total_net,
        change_amount=round(change_amount, 2),
        change_percent=round(change_percent, 2) if change_percent is not None else None,
        breakdowns=[
            ReportBreakdown(
                key="income",
                label="Income",
                value=round(total_income, 2),
                color="#10B981",
            ),
            ReportBreakdown(
                key="expenses",
                label="Expenses",
                value=round(total_expenses, 2),
                color="#F43F5E",
            ),
            ReportBreakdown(
                key="netIncome",
                label="Net Income",
                value=total_net,
                color="#6366F1",
            ),
        ],
    )

    meta = ReportMeta(
        type="income_expenses",
        series_keys=["income", "expenses"],
        currency=primary_currency,
        interval=interval,
    )

    # Build per-category composition for the full date range
    cat_result = await session.execute(
        select(
            Category.id,
            Category.name,
            Category.color,
            Transaction.type,
            func.sum(amount_expr),
        )
        .select_from(Transaction)
        .join(Account, Transaction.account_id == Account.id)
        .outerjoin(Category, Transaction.category_id == Category.id)
        .where(
            Transaction.workspace_id == workspace_id,
            Account.is_closed == False,
            report_date >= start,
            report_date <= today,
            Transaction.source != "opening_balance",
            counts_as_user_pnl(),
            *acct_filter,
        )
        .group_by(Category.id, Category.name, Category.color, Transaction.type)
    )

    # Collect composition into a mutable map so projections can be added
    # Key: (cat_key, group) -> {label, color, value}
    comp_map: dict[tuple[str, str], dict] = {}
    for row in cat_result.all():
        cat_id, cat_name, cat_color, txn_type, total_amount = row
        amount = abs(float(total_amount or 0))
        if amount <= 0:
            continue
        cat_key = str(cat_id) if cat_id else "uncategorized"
        group = "income" if txn_type == "credit" else "expenses"
        comp_map[(cat_key, group)] = {
            "label": cat_name if cat_name else "Uncategorized",
            "color": cat_color if cat_color else "#6B7280",
            "value": amount,
        }

    # Subtract non-owner shares of own splits from composition (debit only —
    # owner_split_offset_by_category is debit-only). Keeps the report's
    # composition consistent with summary totals under share-only model.
    full_range_offset = {} if filtered else await owner_split_offset_by_category(
        session, user_id, start, today + timedelta(days=1),
        use_effective_date=accounting_mode == "accrual",
        primary_currency=primary_currency,
    )
    for cat_uuid, offset_total in full_range_offset.items():
        cat_key = str(cat_uuid) if cat_uuid else "uncategorized"
        comp_key = (cat_key, "expenses")
        if comp_key in comp_map:
            comp_map[comp_key]["value"] -= offset_total
            if comp_map[comp_key]["value"] <= 0:
                comp_map.pop(comp_key)

    # Investment-style outflows: transactions in `treat_as_transfer` categories
    # are excluded from P&L by counts_as_user_pnl (an investment application's
    # counterpart is an Asset/Holding, not spending). But for the cashflow Sankey
    # we surface them as their own "investments" group — money set aside, distinct
    # from spending and from leftover surplus (mirrors how Sure shows an
    # "Investment Contributions" node). Paired account transfers stay excluded via
    # the transfer_pair_id filter, so only one-sided movements (contributions)
    # appear here.
    inv_result = await session.execute(
        select(
            Category.id,
            Category.name,
            Category.color,
            func.sum(amount_expr),
        )
        .select_from(Transaction)
        .join(Account, Transaction.account_id == Account.id)
        .join(Category, Transaction.category_id == Category.id)
        .where(
            Transaction.workspace_id == workspace_id,
            Account.is_closed == False,
            report_date >= start,
            report_date <= today,
            Transaction.source != "opening_balance",
            Transaction.type == "debit",
            Transaction.transfer_pair_id.is_(None),
            Transaction.is_ignored.is_(False),
            Category.treat_as_transfer.is_(True),
            Category.is_ignored.is_(False),
            *acct_filter,
        )
        .group_by(Category.id, Category.name, Category.color)
    )
    for cat_id, cat_name, cat_color, total_amount in inv_result.all():
        amount = abs(float(total_amount or 0))
        if amount <= 0:
            continue
        comp_map[(str(cat_id), "investments")] = {
            "label": cat_name or "Investments",
            "color": cat_color or "#0EA5E9",
            "value": amount,
        }

    # Build per-category trend (sparklines) for the full date range
    cat_trend_result = await session.execute(
        select(
            label_expr,
            Category.id,
            Category.name,
            Category.color,
            Transaction.type,
            func.sum(amount_expr),
        )
        .select_from(Transaction)
        .join(Account, Transaction.account_id == Account.id)
        .outerjoin(Category, Transaction.category_id == Category.id)
        .where(
            Transaction.workspace_id == workspace_id,
            Account.is_closed == False,
            report_date >= start,
            report_date <= today,
            Transaction.source != "opening_balance",
            counts_as_user_pnl(),
            *acct_filter,
        )
        .group_by(label_expr, Category.id, Category.name, Category.color, Transaction.type)
    )

    # Collect into dict[(cat_key, group)] -> {label, color, total, periods}
    cat_trend_map: dict[tuple[str, str], dict] = {}
    for row in cat_trend_result.all():
        period_label, cat_id, cat_name, cat_color, txn_type, total_amount = row
        amount = abs(float(total_amount or 0))
        if amount <= 0:
            continue
        cat_key = str(cat_id) if cat_id else "uncategorized"
        group = "income" if txn_type == "credit" else "expenses"
        map_key = (cat_key, group)
        if map_key not in cat_trend_map:
            cat_trend_map[map_key] = {
                "label": cat_name if cat_name else "Uncategorized",
                "color": cat_color if cat_color else "#6B7280",
                "total": 0.0,
                "periods": {},
            }
        cat_trend_map[map_key]["total"] += amount
        cat_trend_map[map_key]["periods"][period_label] = (
            cat_trend_map[map_key]["periods"].get(period_label, 0.0) + amount
        )

    # Subtract non-owner shares of own splits per (period, category) — keeps
    # the per-category trend consistent with the share-only summary.
    cat_offset_result = await session.execute(
        select(
            label_expr,
            Transaction.category_id,
            Transaction.currency,
            func.sum(_TS_.share_amount),
        )
        .select_from(_TS_)
        .join(Transaction, _TS_.transaction_id == Transaction.id)
        .where(
            Transaction.user_id == user_id,
            Transaction.workspace_id == workspace_id,
            Transaction.type == "debit",
            _TS_.group_member_id.notin_(own_member_ids_sq),
            report_date >= start,
            report_date <= today,
            Transaction.source != "opening_balance",
            counts_as_user_pnl(),
        )
        .group_by(label_expr, Transaction.category_id, Transaction.currency)
    )
    for period_label, cat_id, currency, raw_total in (cat_offset_result.all() if not filtered else []):
        if not raw_total:
            continue
        cat_key = str(cat_id) if cat_id else "uncategorized"
        offset_pri, _ = await fx_convert(
            session, Decimal(str(raw_total)), currency, primary_currency
        )
        offset = float(offset_pri)
        map_key = (cat_key, "expenses")
        if map_key not in cat_trend_map:
            continue
        cat_trend_map[map_key]["total"] = max(
            0.0, cat_trend_map[map_key]["total"] - offset
        )
        cur_period = cat_trend_map[map_key]["periods"].get(period_label, 0.0)
        cat_trend_map[map_key]["periods"][period_label] = max(0.0, cur_period - offset)
    # Drop categories that fully zeroed out
    for key in list(cat_trend_map.keys()):
        if cat_trend_map[key]["total"] <= 0:
            cat_trend_map.pop(key)

    # Add recurring projections to composition and category trend
    cat_cache: dict[str, dict] = {}
    cursor2 = start
    while cursor2 <= today:
        m_start, m_end = _month_range(cursor2)
        projections = await _get_recurring_projections(session, workspace_id, m_start, m_end, account_ids)
        period_label = _format_date_label(cursor2, interval)
        for proj in projections:
            cat_id_str = str(proj["category_id"]) if proj["category_id"] else "uncategorized"
            group = "income" if proj["type"] == "credit" else "expenses"

            # Fetch category info if needed
            if cat_id_str != "uncategorized" and cat_id_str not in cat_cache:
                cat_row = await session.execute(
                    select(Category.name, Category.color)
                    .where(Category.id == proj["category_id"])
                )
                row = cat_row.one_or_none()
                cat_cache[cat_id_str] = {
                    "label": row[0] if row else "Uncategorized",
                    "color": row[1] if row else "#6B7280",
                }

            info = cat_cache.get(cat_id_str, {"label": "Uncategorized", "color": "#6B7280"})

            # Convert projection amount to primary currency
            converted, _ = await fx_convert(
                session, Decimal(str(proj["amount"])), proj["currency"], primary_currency,
            )
            proj_amount = float(converted)

            # Add to composition map
            comp_key = (cat_id_str, group)
            if comp_key in comp_map:
                comp_map[comp_key]["value"] += proj_amount
            else:
                comp_map[comp_key] = {
                    "label": info["label"],
                    "color": info["color"],
                    "value": proj_amount,
                }

            # Add to category trend map
            if comp_key not in cat_trend_map:
                cat_trend_map[comp_key] = {
                    "label": info["label"],
                    "color": info["color"],
                    "total": 0.0,
                    "periods": {},
                }
            cat_trend_map[comp_key]["total"] += proj_amount
            cat_trend_map[comp_key]["periods"][period_label] = (
                cat_trend_map[comp_key]["periods"].get(period_label, 0.0) + proj_amount
            )

        if cursor2.month == 12:
            cursor2 = date(cursor2.year + 1, 1, 1)
        else:
            cursor2 = date(cursor2.year, cursor2.month + 1, 1)

    # Build final composition list from map
    composition: list[ReportCompositionItem] = []
    for (cat_key, group), info in comp_map.items():
        if info["value"] <= 0:
            continue
        composition.append(ReportCompositionItem(
            key=cat_key,
            label=info["label"],
            value=round(info["value"], 2),
            color=info["color"],
            group=group,
        ))

    # Build period labels from the same points used by the trend
    period_labels = [_format_date_label(p, interval) for p in points]

    # Top 6 + Other per group
    category_trend: list[CategoryTrendItem] = []
    for group in ("expenses", "income"):
        group_items = [
            (k, v) for (k, g), v in cat_trend_map.items() if g == group
        ]
        group_items.sort(key=lambda x: x[1]["total"], reverse=True)
        top = group_items[:CATEGORY_TREND_TOP_N]
        rest = group_items[CATEGORY_TREND_TOP_N:]

        for cat_key, info in top:
            series = [
                ReportDataPoint(
                    date=pl,
                    value=round(info["periods"].get(pl, 0.0), 2),
                    breakdowns={},
                )
                for pl in period_labels
            ]
            category_trend.append(CategoryTrendItem(
                key=cat_key,
                label=info["label"],
                color=info["color"],
                total=round(info["total"], 2),
                group=group,
                series=series,
            ))

        if rest:
            other_total = sum(v["total"] for _, v in rest)
            other_periods: dict[str, float] = {}
            for _, v in rest:
                for pl, amt in v["periods"].items():
                    other_periods[pl] = other_periods.get(pl, 0.0) + amt
            series = [
                ReportDataPoint(
                    date=pl,
                    value=round(other_periods.get(pl, 0.0), 2),
                    breakdowns={},
                )
                for pl in period_labels
            ]
            category_trend.append(CategoryTrendItem(
                key="other",
                label="Other",
                color="#6B7280",
                total=round(other_total, 2),
                group=group,
                series=series,
            ))

    # Populate each trend point's composition from the per-period category data,
    # so the frontend can show a per-period donut when a bar is clicked.
    for dp in trend:
        for (cat_key, group), info in cat_trend_map.items():
            v = info["periods"].get(dp.date, 0.0)
            if v > 0:
                dp.composition.append(ReportCompositionItem(
                    key=cat_key,
                    label=info["label"],
                    value=round(v, 2),
                    color=info["color"],
                    group=group,
                ))

    return ReportResponse(
        summary=summary, trend=trend, meta=meta,
        composition=composition, category_trend=category_trend,
    )


def _add_months(d: date, months: int) -> date:
    """Advance a date by N months, clamping the day to the target month's length."""
    new_month = d.month + months
    new_year = d.year + (new_month - 1) // 12
    new_month = ((new_month - 1) % 12) + 1
    last_day = calendar.monthrange(new_year, new_month)[1]
    new_day = min(d.day, last_day)
    return date(new_year, new_month, new_day)


_BASELINE_MAX_LOOKBACK_MONTHS = 12
_PAST_HISTORY_MONTHS = 1


async def _get_baseline_projection(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    today: date,
    end: date,
    primary_currency: str,
    to_primary,
    account_ids: Optional[list[uuid.UUID]] = None,
) -> tuple[list[dict], int]:
    """Estimate future flows by averaging the user's recent transaction history.

    Replaces deterministic recurring projections when baseline mode is on:
    sums all non-ignored, P&L-counting transactions inside the look-back
    window, splits into mean daily inflow and outflow, then emits one
    synthetic flow per day from today+1 to end.

    The look-back window is **adaptive**: capped at
    ``_BASELINE_MAX_LOOKBACK_MONTHS`` (12) but shrunk to the user's earliest
    qualifying transaction date when they have less history. This means a
    user with 4 months of activity gets a 4-month average; a user with
    3 years gets a 12-month average. More history → more stable estimate;
    less history → still works, just noisier.

    Symmetric for income and expense — fixes the "no recurring salary set
    up, chart looks catastrophic" case as well as the "no recurring expenses
    set up, chart looks rosy" case.

    Returns ``(projections, lookback_days)`` so the caller can surface the
    actual window used in the response (zero when there's no history).
    """
    acct_filter = [Transaction.account_id.in_(account_ids)] if account_ids is not None else []
    cap_start = _add_months(today, -_BASELINE_MAX_LOOKBACK_MONTHS)
    earliest_result = await session.execute(
        select(func.min(Transaction.date))
        .join(Account, Transaction.account_id == Account.id)
        .where(
            Transaction.workspace_id == workspace_id,
            Account.is_closed == False,
            Transaction.date <= today,
            Transaction.source != "opening_balance",
            counts_as_pnl(),
            *acct_filter,
        )
    )
    earliest_date = earliest_result.scalar_one_or_none()
    if earliest_date is None:
        return [], 0
    window_start = max(earliest_date, cap_start)

    rows = await session.execute(
        select(
            Transaction.type,
            Transaction.amount,
            Transaction.amount_primary,
            Transaction.currency,
        )
        .join(Account, Transaction.account_id == Account.id)
        .where(
            Transaction.workspace_id == workspace_id,
            Account.is_closed == False,
            Transaction.date >= window_start,
            Transaction.date <= today,
            Transaction.source != "opening_balance",
            counts_as_pnl(),
            *acct_filter,
        )
    )
    total_inflow_primary = 0.0
    total_outflow_primary = 0.0
    for tx_type, amt, amt_primary, ccy in rows.all():
        if amt_primary is not None:
            amount = float(amt_primary)
        else:
            amount = await to_primary(Decimal(str(amt or 0)), ccy)
        if amount == 0:
            continue
        if tx_type == "credit":
            total_inflow_primary += abs(amount)
        else:
            total_outflow_primary += abs(amount)

    lookback_days = max((today - window_start).days, 1)
    daily_inflow = total_inflow_primary / lookback_days
    daily_outflow = total_outflow_primary / lookback_days

    projections: list[dict] = []
    if daily_inflow == 0 and daily_outflow == 0:
        return projections, lookback_days

    cursor = today + timedelta(days=1)
    while cursor <= end:
        if daily_inflow > 0:
            projections.append({
                "date": cursor,
                "amount": daily_inflow,
                "currency": primary_currency,
                "type": "credit",
                "category_id": None,
            })
        if daily_outflow > 0:
            projections.append({
                "date": cursor,
                "amount": daily_outflow,
                "currency": primary_currency,
                "type": "debit",
                "category_id": None,
            })
        cursor = cursor + timedelta(days=1)
    return projections, lookback_days


async def get_cash_flow_report(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    months: int = 6,
    interval: str = "daily",
    currency: str = "USD",
    baseline: bool = False,
    account_ids: Optional[list[uuid.UUID]] = None,
) -> ReportResponse:
    """Cash flow chart with a short past window plus a forward projection.

    Past window (``_PAST_HISTORY_MONTHS`` months back to today) shows actual
    booked transactions so the user has a visible "this is real" anchor next
    to the forecast. Forward window walks from today to ``today + months``
    applying either deterministic recurring projections (default) or a
    historical-mean baseline (when ``baseline=True``) — see
    ``_get_baseline_projection`` for the latter.

    Respects the global ``credit_card_accounting_mode`` setting:
      - **cash**: flows queried by ``Transaction.date``.
      - **accrual**: flows queried by ``Transaction.effective_date`` so CC
        purchases show up as cash leaving on their bill due date. The
        balance at past-history start is also adjusted to add back any
        pending CC purchases whose effective_date is in the future window,
        avoiding double-counting against ``_balance_at``.
    """
    from app.services.dashboard_service import _balance_at, _get_recurring_projections
    from app.services.fx_rate_service import get_rate

    acct_filter = [Transaction.account_id.in_(account_ids)] if account_ids is not None else []
    today = date.today()
    end = _add_months(today, months)
    chart_start = _add_months(today, -_PAST_HISTORY_MONTHS)

    user = await session.get(User, user_id)
    primary_currency = user.primary_currency if user else get_settings().default_currency

    accounting_mode = await get_credit_card_accounting_mode(session)
    accrual = accounting_mode == "accrual"

    # "Saldo Atual" shown in the hero card. The walk is anchored at this
    # value (not at balance-at-chart_start) so opening-balance transactions
    # inside the past-history window can't introduce drift.
    current_balance = await _balance_at(
        session, workspace_id, today, primary_currency_hint=primary_currency,
        account_ids=account_ids,
    )

    rate_cache: dict[str, Decimal] = {primary_currency: Decimal("1")}

    async def _to_primary(amount: Decimal, ccy: str) -> float:
        if ccy == primary_currency:
            return float(amount)
        rate = rate_cache.get(ccy)
        if rate is None:
            rate = await get_rate(session, ccy, primary_currency)
            rate_cache[ccy] = rate
        return float((amount * rate).quantize(Decimal("0.01")))

    flows: dict[date, dict[str, float]] = {}

    def _add_flow(d: date, amount: float, is_credit: bool) -> None:
        bucket = flows.setdefault(d, {"inflow": 0.0, "outflow": 0.0})
        if is_credit:
            bucket["inflow"] += amount
        else:
            bucket["outflow"] += amount

    flow_date_col = Transaction.effective_date if accrual else Transaction.date

    # 1a. Past actual transactions (chart_start, today]. Gives the chart a
    #     "real" section before the forecast so the today-marker has meaning.
    past_result = await session.execute(
        select(
            flow_date_col,
            Transaction.type,
            Transaction.amount,
            Transaction.amount_primary,
            Transaction.currency,
        )
        .join(Account, Transaction.account_id == Account.id)
        .where(
            Transaction.workspace_id == workspace_id,
            Account.is_closed == False,
            flow_date_col > chart_start,
            flow_date_col <= today,
            Transaction.source != "opening_balance",
            counts_as_pnl(),
            *acct_filter,
        )
    )
    for flow_date, tx_type, amt, amt_primary, ccy in past_result.all():
        if amt_primary is not None:
            amount_primary = float(amt_primary)
        else:
            amount_primary = await _to_primary(Decimal(str(amt or 0)), ccy)
        if amount_primary == 0:
            continue
        _add_flow(flow_date, abs(amount_primary), tx_type == "credit")

    # 1b. Future booked transactions whose cash impact is past today.
    booked_result = await session.execute(
        select(
            flow_date_col,
            Transaction.type,
            Transaction.amount,
            Transaction.amount_primary,
            Transaction.currency,
        )
        .join(Account, Transaction.account_id == Account.id)
        .where(
            Transaction.workspace_id == workspace_id,
            Account.is_closed == False,
            flow_date_col > today,
            flow_date_col <= end,
            Transaction.source != "opening_balance",
            counts_as_pnl(),
            *acct_filter,
        )
    )
    for row in booked_result.all():
        flow_date, tx_type, amt, amt_primary, ccy = row
        if amt_primary is not None:
            amount_primary = float(amt_primary)
        else:
            amount_primary = await _to_primary(Decimal(str(amt or 0)), ccy)
        if amount_primary == 0:
            continue
        _add_flow(flow_date, abs(amount_primary), tx_type == "credit")

    # 2. Accrual mode: pending CC purchases (purchase date <= today, due
    #    date in the forward window) already reduced today's balance via the
    #    CC liability. We re-project them as outflows on their effective_date,
    #    so add them back to both balance snapshots to avoid double-counting.
    if accrual:
        pending_cc = await session.execute(
            select(
                Transaction.type,
                Transaction.amount,
                Transaction.amount_primary,
                Transaction.currency,
            )
            .join(Account, Transaction.account_id == Account.id)
            .where(
                Transaction.workspace_id == workspace_id,
                Account.is_closed == False,
                Account.type == "credit_card",
                Transaction.date <= today,
                Transaction.effective_date > today,
                Transaction.effective_date <= end,
                Transaction.source != "opening_balance",
                counts_as_pnl(),
                *acct_filter,
            )
        )
        for tx_type, amt, amt_primary, ccy in pending_cc.all():
            if amt_primary is not None:
                amount_primary = float(amt_primary)
            else:
                amount_primary = await _to_primary(Decimal(str(amt or 0)), ccy)
            if amount_primary == 0:
                continue
            if tx_type == "debit":
                current_balance += abs(amount_primary)
            else:
                current_balance -= abs(amount_primary)

    # 3. Forward projection. Default: deterministic recurring rules.
    #    Baseline mode: replace them with a historical-mean estimate so the
    #    chart reflects the user's actual recent lifestyle, not just what
    #    they've explicitly marked as recurring.
    cat_totals: dict[tuple[str, str], dict] = {}
    cat_cache: dict[str, dict] = {}
    baseline_lookback_days = 0

    if baseline:
        projections, baseline_lookback_days = await _get_baseline_projection(
            session, workspace_id, today, end, primary_currency, _to_primary, account_ids,
        )
    else:
        projections = await _get_recurring_projections(
            session, workspace_id, today + timedelta(days=1), end + timedelta(days=1),
            account_ids,
        )

    for proj in projections:
        d = proj["date"]
        if d <= today or d > end:
            continue
        amount_primary = await _to_primary(
            Decimal(str(proj["amount"])), proj["currency"]
        )
        if amount_primary == 0:
            continue
        _add_flow(d, amount_primary, proj["type"] == "credit")

        cat_id = proj["category_id"]
        if cat_id:
            cat_id_str = str(cat_id)
        else:
            cat_id_str = "baseline" if baseline else "uncategorized"
        group = "income" if proj["type"] == "credit" else "expenses"
        if cat_id and cat_id_str not in cat_cache:
            cat_row = await session.execute(
                select(Category.name, Category.color).where(Category.id == cat_id)
            )
            row = cat_row.one_or_none()
            cat_cache[cat_id_str] = {
                "label": row[0] if row else "Uncategorized",
                "color": row[1] if row else "#6B7280",
            }
        elif cat_id_str == "baseline" and cat_id_str not in cat_cache:
            # Frontend translates via reports.baseline; label is the fallback.
            cat_cache[cat_id_str] = {"label": "Baseline", "color": "#94A3B8"}
        info = cat_cache.get(cat_id_str, {"label": "Uncategorized", "color": "#6B7280"})
        key = (cat_id_str, group)
        if key not in cat_totals:
            cat_totals[key] = {"label": info["label"], "color": info["color"], "value": 0.0}
        cat_totals[key]["value"] += amount_primary

    # 4. Walk day-by-day. The walk is anchored at today's authoritative
    #    balance (``current_balance`` from ``_balance_at``) rather than at
    #    ``chart_starting_balance``: opening-balance transactions are excluded
    #    from the past-actuals query, so a forward walk seeded from
    #    ``chart_starting_balance`` would drift if any opening balance falls
    #    inside the past-history window. Anchoring at today eliminates that
    #    class of bug and keeps the past trend visually correct.
    daily_balance: dict[date, float] = {today: current_balance}
    daily_inflow: dict[date, float] = {}
    daily_outflow: dict[date, float] = {}

    # Today's own inflow/outflow bucket (for tooltip), but the balance at
    # end-of-today is already current_balance regardless of today's flows
    # (they're folded into the authoritative number from _balance_at).
    today_bucket = flows.get(today, {"inflow": 0.0, "outflow": 0.0})
    daily_inflow[today] = today_bucket["inflow"]
    daily_outflow[today] = today_bucket["outflow"]

    # Backward walk: balance(d-1) = balance(d) - net_flow(d).
    running = current_balance
    cursor_d = today
    while cursor_d > chart_start:
        bucket = flows.get(cursor_d, {"inflow": 0.0, "outflow": 0.0})
        running -= bucket["inflow"] - bucket["outflow"]
        cursor_d = cursor_d - timedelta(days=1)
        daily_balance[cursor_d] = running
        prev_bucket = flows.get(cursor_d, {"inflow": 0.0, "outflow": 0.0})
        daily_inflow[cursor_d] = prev_bucket["inflow"]
        daily_outflow[cursor_d] = prev_bucket["outflow"]

    # Forward walk: balance(d+1) = balance(d) + net_flow(d+1).
    running = current_balance
    cursor_d = today
    while cursor_d < end:
        cursor_d = cursor_d + timedelta(days=1)
        bucket = flows.get(cursor_d, {"inflow": 0.0, "outflow": 0.0})
        running += bucket["inflow"] - bucket["outflow"]
        daily_balance[cursor_d] = running
        daily_inflow[cursor_d] = bucket["inflow"]
        daily_outflow[cursor_d] = bucket["outflow"]

    # 5. Aggregate to interval.
    points = _date_points(chart_start, end, interval)
    trend: list[ReportDataPoint] = []

    if interval == "daily":
        for p in points:
            bal = daily_balance.get(p, running)
            inf = daily_inflow.get(p, 0.0)
            outf = daily_outflow.get(p, 0.0)
            trend.append(ReportDataPoint(
                date=_format_date_label(p, "daily"),
                value=round(bal, 2),
                breakdowns={"inflow": round(inf, 2), "outflow": round(outf, 2)},
            ))
    else:
        groups: dict[str, dict] = {}
        cur = chart_start
        while cur <= end:
            label = _format_date_label(cur, interval)
            g = groups.setdefault(
                label,
                {"balance": daily_balance.get(cur, running), "last_d": cur,
                 "inflow": 0.0, "outflow": 0.0},
            )
            if cur >= g["last_d"]:
                g["balance"] = daily_balance.get(cur, running)
                g["last_d"] = cur
            g["inflow"] += daily_inflow.get(cur, 0.0)
            g["outflow"] += daily_outflow.get(cur, 0.0)
            cur = cur + timedelta(days=1)

        seen: set[str] = set()
        for p in points:
            label = _format_date_label(p, interval)
            if label in seen:
                continue
            seen.add(label)
            g = groups.get(label)
            if g is None:
                continue
            trend.append(ReportDataPoint(
                date=label,
                value=round(g["balance"], 2),
                breakdowns={
                    "inflow": round(g["inflow"], 2),
                    "outflow": round(g["outflow"], 2),
                },
            ))

    # 6. Summary. "Projected income/expenses" sums only the forward portion,
    #    not the past actuals that the chart now also includes.
    ending_balance = trend[-1].value if trend else round(current_balance, 2)
    forward_inflow = 0.0
    forward_outflow = 0.0
    for d, bucket in flows.items():
        if today < d <= end:
            forward_inflow += bucket["inflow"]
            forward_outflow += bucket["outflow"]
    total_inflow = round(forward_inflow, 2)
    total_outflow = round(forward_outflow, 2)
    change_amount = round(ending_balance - current_balance, 2)
    change_percent = (
        (change_amount / abs(current_balance) * 100) if current_balance != 0 else None
    )

    summary = ReportSummary(
        primary_value=ending_balance,
        change_amount=change_amount,
        change_percent=round(change_percent, 2) if change_percent is not None else None,
        breakdowns=[
            ReportBreakdown(
                key="startingBalance", label="Starting Balance",
                value=round(current_balance, 2), color="#6366F1",
            ),
            ReportBreakdown(
                key="projectedIncome", label="Projected Income",
                value=total_inflow, color="#10B981",
            ),
            ReportBreakdown(
                key="projectedExpenses", label="Projected Expenses",
                value=total_outflow, color="#F43F5E",
            ),
            ReportBreakdown(
                key="endingBalance", label="Ending Balance",
                value=ending_balance, color="#8B5CF6",
            ),
        ],
    )

    meta = ReportMeta(
        type="cash_flow",
        series_keys=["balance"],
        currency=primary_currency,
        interval=interval,
        forecast_start_date=_format_date_label(today, interval),
        baseline_active=baseline,
        baseline_lookback_days=baseline_lookback_days if baseline else None,
    )

    composition: list[ReportCompositionItem] = []
    for (cat_key, group), info in cat_totals.items():
        if info["value"] <= 0:
            continue
        composition.append(ReportCompositionItem(
            key=cat_key,
            label=info["label"],
            value=round(info["value"], 2),
            color=info["color"],
            group=group,
        ))

    return ReportResponse(
        summary=summary, trend=trend, meta=meta,
        composition=composition, category_trend=[],
    )
