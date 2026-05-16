import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import select, func, case, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.account import Account
from app.models.bank_connection import BankConnection
from app.models.transaction import Transaction
from app.models.category import Category
from app.models.recurring_transaction import RecurringTransaction
from app.schemas.dashboard import DashboardSummary, SpendingByCategory, MonthlyTrend, ProjectedTransaction, DailyBalance, BalanceHistory
from app.services._query_filters import (
    counts_as_user_pnl,
    owner_split_offset_by_category,
    owner_split_offset_pnl,
    viewer_shared_pnl,
    viewer_shared_spending_by_category,
)
from app.services.admin_service import get_credit_card_accounting_mode
from app.services.recurring_transaction_service import get_occurrences_in_range
from app.services.asset_service import get_asset_values_at
from app.services.fx_rate_service import convert
from app.models.user import User


def _month_range(month: date) -> tuple[date, date]:
    """Return (month_start, month_end) for a given date."""
    month_start = month.replace(day=1)
    if month.month == 12:
        month_end = month.replace(year=month.year + 1, month=1, day=1)
    else:
        month_end = month.replace(month=month.month + 1, day=1)
    return month_start, month_end


async def _get_recurring_projections(
    session: AsyncSession, user_id: uuid.UUID, month_start: date, month_end: date
) -> list[dict]:
    """Compute virtual recurring transaction projections for a month.
    Pure read — no DB writes. Returns list of dicts with category_id, amount, type, currency."""
    result = await session.execute(
        select(RecurringTransaction)
        .where(
            RecurringTransaction.user_id == user_id,
            RecurringTransaction.is_active == True,
            RecurringTransaction.start_date < month_end,
        )
    )
    recurring_list = list(result.scalars().all())

    projections = []
    for rec in recurring_list:
        # Compute occurrences starting from next_occurrence (skips already-created transactions)
        occurrences = get_occurrences_in_range(
            start=rec.next_occurrence,
            frequency=rec.frequency,
            end_date=rec.end_date,
            range_start=month_start,
            range_end=month_end,
            intended_day=rec.day_of_month or rec.start_date.day,
        )
        for occ_date in occurrences:
            projections.append({
                "category_id": rec.category_id,
                "amount": float(rec.amount),
                "type": rec.type,
                "currency": rec.currency,
                "date": occ_date,
            })
    return projections


async def get_summary(
    session: AsyncSession, user_id: uuid.UUID, month: Optional[date] = None,
    balance_date: Optional[date] = None,
) -> DashboardSummary:
    if not month:
        month = date.today().replace(day=1)

    month_start, month_end = _month_range(month)
    today = date.today()

    # Reporting mode is a global app setting (admin-controlled). When "accrual",
    # aggregation queries bucket credit card transactions by the bill's due
    # date (effective_date) instead of the purchase date — gives a true
    # cash-flow view.
    user = await session.get(User, user_id)
    accounting_mode = await get_credit_card_accounting_mode(session)
    report_date = (
        Transaction.effective_date if accounting_mode == "accrual" else Transaction.date
    )

    # Compute the effective cutoff date for balance calculation
    if balance_date:
        cutoff = balance_date
    elif month_end <= today:
        # Past month: last day of that month
        cutoff = month_end - timedelta(days=1)
    else:
        # Current or future month: today
        cutoff = today

    total_balance = await _total_balance_by_currency(session, user_id, cutoff)

    # For current/future months, project the total balance by adding recurring
    # projections from cutoff+1 through month_end.
    if month_end > cutoff:
        projection_start = cutoff + timedelta(days=1)
        balance_projections = await _get_recurring_projections(
            session, user_id, projection_start, month_end
        )
        for proj in balance_projections:
            signed = proj["amount"] if proj["type"] == "credit" else -proj["amount"]
            total_balance[proj["currency"]] = total_balance.get(proj["currency"], 0.0) + signed

    # Monthly income and expenses — exclude opening_balance so initial deposits
    # don't inflate the month's income figure. counts_as_user_pnl() skips
    # paired transfers, transfer-like categories AND settlement movements
    # (whose offset is already in the owner's share, see share-only model).
    monthly_result = await session.execute(
        select(
            func.sum(case((Transaction.type == "credit", Transaction.amount), else_=0)),
            func.sum(case((Transaction.type == "debit", Transaction.amount), else_=0)),
        )
        .join(Account, Transaction.account_id == Account.id)
        .where(
            Transaction.user_id == user_id,
            Account.is_closed == False,
            report_date >= month_start,
            report_date < month_end,
            Transaction.source != "opening_balance",
            counts_as_user_pnl(),
        )
    )
    monthly_row = monthly_result.one()
    monthly_income = float(monthly_row[0] or 0)
    monthly_expenses = float(monthly_row[1] or 0)

    # Subtract non-owner shares of the user's own split txs — they paid
    # for the others, so those amounts aren't their actual cost.
    own_offset_inc, own_offset_exp = await owner_split_offset_pnl(
        session, user_id, month_start, month_end, use_effective_date=False
    )
    monthly_income -= own_offset_inc
    monthly_expenses -= own_offset_exp

    # Add the viewer's share from group splits where they're a linked
    # member but not the owner. Their concert ticket paid by a friend
    # is a real expense in their P/L picture.
    shared_income, shared_expenses = await viewer_shared_pnl(
        session, user_id, month_start, month_end, use_effective_date=False
    )
    monthly_income += shared_income
    monthly_expenses += shared_expenses

    # Save real-only totals before adding projections (used for primary init)
    real_monthly_income = monthly_income
    real_monthly_expenses = monthly_expenses

    # Add virtual recurring projections
    projections = await _get_recurring_projections(session, user_id, month_start, month_end)
    for proj in projections:
        if proj["type"] == "credit":
            monthly_income += proj["amount"]
        else:
            monthly_expenses += proj["amount"]

    # Account count — all accounts belonging to the user (manual + bank-connected)
    accounts_count = await session.scalar(
        select(func.count())
        .select_from(Account)
        .where(Account.user_id == user_id)
    ) or 0

    # Pending categorization — exclude opening_balance and transfer pairs
    pending_cat_filters = [
        Transaction.user_id == user_id,
        Transaction.category_id.is_(None),
        Transaction.source != "opening_balance",
        # Settlement-sourced rows are auto-generated movements (paying
        # back / receiving back a group debt). They aren't expenses or
        # income that need a category, so exclude them.
        Transaction.source != "settlement",
        Transaction.transfer_pair_id.is_(None),
    ]
    pending_categorization = await session.scalar(
        select(func.count())
        .select_from(Transaction)
        .where(*pending_cat_filters)
    ) or 0

    pending_categorization_amount = abs(float(await session.scalar(
        select(func.coalesce(func.sum(func.abs(Transaction.amount)), 0))
        .select_from(Transaction)
        .where(*pending_cat_filters)
    ) or 0))

    # Get user's primary currency (user already loaded above for reporting mode)
    primary_currency = user.primary_currency if user else get_settings().default_currency

    # Asset values — use cutoff so past months show historical values
    assets_value, assets_value_primary = await get_asset_values_at(
        session, user_id, as_of_date=cutoff, primary_currency=primary_currency
    )

    # Add asset values to total balance
    for currency, amount in assets_value.items():
        total_balance[currency] = total_balance.get(currency, 0.0) + amount

    # Convert totals to primary currency
    total_balance_primary = 0.0
    for currency, amount in total_balance.items():
        converted, _ = await convert(session, Decimal(str(amount)), currency, primary_currency, cutoff)
        total_balance_primary += float(converted)

    # Convert income/expenses to primary currency using amount_primary when available
    # Use real-only totals (without projections) to avoid double-counting;
    # projections are added separately below via convert().
    monthly_income_primary = real_monthly_income
    monthly_expenses_primary = abs(real_monthly_expenses)

    # Use amount_primary sums for more accurate multi-currency income/expenses
    primary_result = await session.execute(
        select(
            func.sum(case((Transaction.type == "credit", Transaction.amount_primary), else_=0)),
            func.sum(case((Transaction.type == "debit", Transaction.amount_primary), else_=0)),
        )
        .join(Account, Transaction.account_id == Account.id)
        .where(
            Transaction.user_id == user_id,
            Account.is_closed == False,
            report_date >= month_start,
            report_date < month_end,
            Transaction.source != "opening_balance",
            counts_as_user_pnl(),
            Transaction.amount_primary.isnot(None),
        )
    )
    primary_row = primary_result.one()
    if primary_row[0] is not None or primary_row[1] is not None:
        monthly_income_primary = float(primary_row[0] or 0)
        monthly_expenses_primary = abs(float(primary_row[1] or 0))

    # Apply share-only offset in primary currency (FX-converted).
    own_offset_inc_pri, own_offset_exp_pri = await owner_split_offset_pnl(
        session,
        user_id,
        month_start,
        month_end,
        use_effective_date=False,
        primary_currency=primary_currency,
    )
    monthly_income_primary -= own_offset_inc_pri
    monthly_expenses_primary -= own_offset_exp_pri

    # Add the viewer's shared shares to primary totals too. The shares
    # are stored in the parent transaction's currency, so we convert
    # each currency bucket separately rather than re-using shared_income
    # / shared_expenses (which were summed without conversion).
    from app.models.group import GroupMember
    from app.models.transaction_split import TransactionSplit

    viewer_member_ids = select(GroupMember.id).where(
        GroupMember.linked_user_id == user_id
    )
    shared_currency_rows = await session.execute(
        select(
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
            Transaction.source != "opening_balance",
            report_date >= month_start,
            report_date < month_end,
            counts_as_user_pnl(),
        )
        .group_by(Transaction.currency)
    )
    for row in shared_currency_rows.all():
        cur = row[0]
        in_credit = float(row[1] or 0)
        in_debit = float(row[2] or 0)
        if in_credit:
            credit_pri, _ = await convert(
                session, Decimal(str(in_credit)), cur, primary_currency
            )
            monthly_income_primary += float(credit_pri)
        if in_debit:
            debit_pri, _ = await convert(
                session, Decimal(str(in_debit)), cur, primary_currency
            )
            monthly_expenses_primary += abs(float(debit_pri))

    # Add recurring projections to primary totals (convert each)
    for proj in projections:
        proj_converted, _ = await convert(
            session, Decimal(str(proj["amount"])),
            proj["currency"], primary_currency,
        )
        if proj["type"] == "credit":
            monthly_income_primary += float(proj_converted)
        else:
            monthly_expenses_primary += float(proj_converted)

    # Aggregate the user's net pending balance across all groups they
    # participate in. We reuse the group balance computation so partial
    # settlements are already netted out.
    pending_shares_net = await _compute_pending_shares_net(
        session, user_id, primary_currency
    )

    return DashboardSummary(
        total_balance=total_balance,
        total_balance_primary=round(total_balance_primary, 2),
        balance_date=cutoff.isoformat(),
        monthly_income=monthly_income,
        monthly_expenses=abs(monthly_expenses),
        monthly_income_primary=round(monthly_income_primary, 2),
        monthly_expenses_primary=round(monthly_expenses_primary, 2),
        accounts_count=accounts_count,
        pending_categorization=pending_categorization,
        pending_categorization_amount=pending_categorization_amount,
        assets_value=assets_value,
        assets_value_primary=round(assets_value_primary, 2),
        primary_currency=primary_currency,
        pending_shares_net=round(pending_shares_net, 2),
    )


async def _compute_pending_shares_net(
    session: AsyncSession, user_id: uuid.UUID, primary_currency: str
) -> float:
    """Sum, in primary currency, the user's net position across every
    group they belong to.

    For each group:
      - Owner: sum of (positive lines) - sum of (abs negative lines).
        Positive net = others owe them; negative = they owe.
      - Linked member: their own line. Positive line = they owe the
        owner; we flip the sign so the dashboard shows it as negative
        (net liability).
    """
    from app.models.group import Group, GroupMember
    from app.services.balance_service import compute_balances

    # All groups the user can see (owned + linked-as-member).
    owned_q = await session.execute(
        select(Group.id).where(Group.user_id == user_id)
    )
    owned_ids = {row[0] for row in owned_q.all()}
    linked_q = await session.execute(
        select(GroupMember.group_id, GroupMember.id).where(
            GroupMember.linked_user_id == user_id
        )
    )
    linked_rows = list(linked_q.all())
    linked_ids = {row.group_id for row in linked_rows}
    member_id_for_group = {row.group_id: row.id for row in linked_rows}

    total_primary = 0.0
    for gid in owned_ids | linked_ids:
        balances = await compute_balances(session, gid, user_id)
        if not balances:
            continue
        for line in balances["lines"]:
            line_amount = float(line["amount"])
            currency = line["currency"]
            if gid in owned_ids:
                # Positive = member owes the owner (an asset).
                # Negative = owner owes the member (a liability).
                signed = line_amount
            else:
                # Linked-member view: only the line about *us* matters
                # to our personal net. Flip the sign — `compute_balances`
                # frames it from the owner's perspective.
                if line["member_id"] != member_id_for_group[gid]:
                    continue
                signed = -line_amount
            converted, _ = await convert(
                session, Decimal(str(signed)), currency, primary_currency
            )
            total_primary += float(converted)
    return total_primary


async def get_spending_by_category(
    session: AsyncSession, user_id: uuid.UUID, month: Optional[date] = None
) -> list[SpendingByCategory]:
    if not month:
        month = date.today().replace(day=1)

    month_start, month_end = _month_range(month)

    user = await session.get(User, user_id)
    accounting_mode = await get_credit_card_accounting_mode(session)
    primary_currency = user.primary_currency if user else get_settings().default_currency
    report_date = (
        Transaction.effective_date if accounting_mode == "accrual" else Transaction.date
    )

    # Real transactions grouped by category (exclude transfer-like movements
    # and closed accounts). Use amount_primary for multi-currency support.
    result = await session.execute(
        select(
            Category.id,
            Category.name,
            Category.icon,
            Category.color,
            func.sum(_primary_amount_expr()),
        )
        .select_from(Transaction)
        .join(Account, Transaction.account_id == Account.id)
        .outerjoin(Category, Transaction.category_id == Category.id)
        .where(
            Transaction.user_id == user_id,
            Account.is_closed == False,
            Transaction.type == "debit",
            report_date >= month_start,
            report_date < month_end,
            counts_as_user_pnl(),
        )
        .group_by(Category.id, Category.name, Category.icon, Category.color)
        .order_by(func.sum(_primary_amount_expr()).desc())
    )

    # Build a dict of category_id -> {name, icon, color, total}
    spending_map: dict[str | None, dict] = {}
    for row in result.all():
        cat_id = str(row[0]) if row[0] else None
        spending_map[cat_id] = {
            "name": row[1] or "Sem categoria",
            "icon": row[2] or "circle-help",
            "color": row[3] or "#6B7280",
            "total": abs(float(row[4] or 0)),
        }

    # Subtract non-owner shares per category — owner-side splits should
    # contribute only the owner's share, not the full amount.
    owner_offset = await owner_split_offset_by_category(
        session,
        user_id,
        month_start,
        month_end,
        use_effective_date=accounting_mode == "accrual",
        primary_currency=primary_currency,
    )
    for cat_uuid, offset_total in owner_offset.items():
        cat_id = str(cat_uuid) if cat_uuid else None
        if cat_id in spending_map:
            spending_map[cat_id]["total"] -= offset_total
            if spending_map[cat_id]["total"] <= 0:
                spending_map.pop(cat_id)

    # Add shared shares — the viewer's portion of group-split debits
    # they participate in but don't own. The category comes from the
    # parent transaction.
    shared_by_cat = await viewer_shared_spending_by_category(
        session, user_id, month_start, month_end,
        use_effective_date=accounting_mode == "accrual",
        primary_currency=primary_currency,
    )
    if shared_by_cat:
        cat_meta_cache: dict[str, dict] = {}
        for cat_uuid, share_total in shared_by_cat.items():
            cat_id = str(cat_uuid) if cat_uuid else None
            if cat_id and cat_id not in cat_meta_cache and cat_id not in spending_map:
                meta_row = (
                    await session.execute(
                        select(Category.name, Category.icon, Category.color).where(
                            Category.id == cat_uuid
                        )
                    )
                ).one_or_none()
                if meta_row:
                    cat_meta_cache[cat_id] = {
                        "name": meta_row[0],
                        "icon": meta_row[1],
                        "color": meta_row[2],
                    }
            if cat_id in spending_map:
                spending_map[cat_id]["total"] += share_total
            else:
                meta = cat_meta_cache.get(
                    cat_id,
                    {"name": "Sem categoria", "icon": "circle-help", "color": "#6B7280"},
                )
                spending_map[cat_id] = {
                    "name": meta["name"],
                    "icon": meta["icon"],
                    "color": meta["color"],
                    "total": share_total,
                }

    # Add virtual recurring projections (debit only), converted to primary currency
    projections = await _get_recurring_projections(session, user_id, month_start, month_end)
    # We need category info for recurring projections — fetch categories
    cat_cache: dict[str, dict] = {}
    for proj in projections:
        if proj["type"] != "debit":
            continue
        cat_id = str(proj["category_id"]) if proj["category_id"] else None
        if cat_id and cat_id not in cat_cache:
            # Fetch category info
            cat_result = await session.execute(
                select(Category.name, Category.icon, Category.color)
                .where(Category.id == proj["category_id"])
            )
            cat_row = cat_result.one_or_none()
            if cat_row:
                cat_cache[cat_id] = {"name": cat_row[0], "icon": cat_row[1], "color": cat_row[2]}
            else:
                cat_cache[cat_id] = {"name": "Sem categoria", "icon": "circle-help", "color": "#6B7280"}

        # Convert projection amount to primary currency
        proj_amount, _ = await convert(
            session, Decimal(str(proj["amount"])), proj["currency"], primary_currency,
        )
        proj_amount_float = float(proj_amount)

        if cat_id in spending_map:
            spending_map[cat_id]["total"] += proj_amount_float
        else:
            info = cat_cache.get(cat_id, {"name": "Sem categoria", "icon": "circle-help", "color": "#6B7280"})
            spending_map[cat_id] = {
                "name": info["name"],
                "icon": info["icon"],
                "color": info["color"],
                "total": proj_amount_float,
            }

    # Convert to list and compute percentages
    grand_total = sum(entry["total"] for entry in spending_map.values())
    spending = []
    for cat_id, entry in sorted(spending_map.items(), key=lambda x: x[1]["total"], reverse=True):
        spending.append(SpendingByCategory(
            category_id=cat_id,
            category_name=entry["name"],
            category_icon=entry["icon"],
            category_color=entry["color"],
            total=entry["total"],
            percentage=(entry["total"] / grand_total * 100) if grand_total else 0,
        ))

    return spending


async def get_monthly_trend(
    session: AsyncSession, user_id: uuid.UUID, months: int = 6
) -> list[MonthlyTrend]:
    accounting_mode = await get_credit_card_accounting_mode(session)
    report_date = (
        Transaction.effective_date if accounting_mode == "accrual" else Transaction.date
    )
    month_label = func.to_char(report_date, 'YYYY-MM').label('month')
    primary_amt = _primary_amount_expr()
    result = await session.execute(
        select(
            month_label,
            func.sum(case((Transaction.type == "credit", primary_amt), else_=0)),
            func.sum(case((Transaction.type == "debit", primary_amt), else_=0)),
        )
        .join(Account, Transaction.account_id == Account.id)
        .where(
            Transaction.user_id == user_id,
            Account.is_closed == False,
            Transaction.source != "opening_balance",
            counts_as_user_pnl(),
        )
        .group_by(month_label)
        .order_by(month_label.desc())
        .limit(months)
    )

    user = await session.get(User, user_id)
    primary_currency = user.primary_currency if user else get_settings().default_currency

    trends_raw: list[tuple[str, float, float]] = []
    for row in result.all():
        trends_raw.append((row[0], float(row[1] or 0), abs(float(row[2] or 0))))

    # Subtract owner non-owner-share offsets per month, and add the
    # viewer's shares of others' splits.
    adjusted: list[MonthlyTrend] = []
    for month_str, income, expenses in trends_raw:
        year, mnum = month_str.split("-")
        m_start = date(int(year), int(mnum), 1)
        m_end = (
            date(int(year), int(mnum) + 1, 1)
            if int(mnum) < 12
            else date(int(year) + 1, 1, 1)
        )
        own_inc, own_exp = await owner_split_offset_pnl(
            session, user_id, m_start, m_end,
            use_effective_date=accounting_mode == "accrual",
            primary_currency=primary_currency,
        )
        shared_inc, shared_exp = await viewer_shared_pnl(
            session, user_id, m_start, m_end,
            use_effective_date=accounting_mode == "accrual",
            primary_currency=primary_currency,
        )
        adjusted.append(
            MonthlyTrend(
                month=month_str,
                income=max(0.0, income - own_inc + shared_inc),
                expenses=max(0.0, expenses - own_exp + shared_exp),
            )
        )

    return list(reversed(adjusted))


async def get_projected_transactions(
    session: AsyncSession, user_id: uuid.UUID, month: Optional[date] = None
) -> list[ProjectedTransaction]:
    """Return virtual recurring transaction projections for a month,
    enriched with description and category info for display."""
    if not month:
        month = date.today().replace(day=1)

    month_start, month_end = _month_range(month)

    # Get user's primary currency for live conversion
    user = await session.get(User, user_id)
    primary_currency = user.primary_currency if user else get_settings().default_currency

    result = await session.execute(
        select(RecurringTransaction)
        .where(
            RecurringTransaction.user_id == user_id,
            RecurringTransaction.is_active == True,
            RecurringTransaction.start_date < month_end,
        )
    )
    recurring_list = list(result.scalars().all())

    # Pre-fetch categories for all recurring templates that have one
    cat_ids = {r.category_id for r in recurring_list if r.category_id}
    cat_map: dict[uuid.UUID, tuple[str, str, str]] = {}
    if cat_ids:
        cat_result = await session.execute(
            select(Category.id, Category.name, Category.icon, Category.color)
            .where(Category.id.in_(cat_ids))
        )
        for row in cat_result.all():
            cat_map[row[0]] = (row[1], row[2], row[3])

    projections: list[ProjectedTransaction] = []
    for rec in recurring_list:
        occurrences = get_occurrences_in_range(
            start=rec.next_occurrence,
            frequency=rec.frequency,
            end_date=rec.end_date,
            range_start=month_start,
            range_end=month_end,
            intended_day=rec.day_of_month or rec.start_date.day,
        )
        cat_name, cat_icon, cat_color = cat_map.get(rec.category_id, (None, None, None)) if rec.category_id else (None, None, None)

        # Convert to primary currency at current rate (consistent with summary)
        amt_primary = None
        if rec.currency != primary_currency:
            converted, _ = await convert(
                session, Decimal(str(rec.amount)), rec.currency, primary_currency,
            )
            amt_primary = float(converted)

        for occ_date in occurrences:
            projections.append(ProjectedTransaction(
                recurring_id=str(rec.id),
                description=rec.description,
                amount=float(rec.amount),
                amount_primary=amt_primary,
                currency=rec.currency,
                type=rec.type,
                date=occ_date.isoformat(),
                category_id=str(rec.category_id) if rec.category_id else None,
                category_name=cat_name,
                category_icon=cat_icon,
                category_color=cat_color,
            ))

    return projections


def _signed_balance_expr(account_currency: str = ""):
    """Reusable SQL expression: credit → +amount, debit → −amount.
    Uses amount_primary when tx currency differs from account currency."""
    if account_currency:
        effective = case(
            (Transaction.currency == account_currency, Transaction.amount),
            else_=func.coalesce(Transaction.amount_primary, Transaction.amount),
        )
    else:
        effective = Transaction.amount
    return case(
        (Transaction.type == "credit", effective),
        else_=-effective,
    )


def _primary_amount_expr():
    """Amount in primary currency: uses amount_primary when available, falls back to amount."""
    return func.coalesce(Transaction.amount_primary, Transaction.amount)


def _signed_primary_expr():
    """Signed amount in primary currency: credit → +, debit → −."""
    amt = _primary_amount_expr()
    return case(
        (Transaction.type == "credit", amt),
        else_=-amt,
    )


async def _get_open_accounts(
    session: AsyncSession, user_id: uuid.UUID
) -> list[Account]:
    """Get all non-closed accounts for a user."""
    result = await session.execute(
        select(Account)
        .outerjoin(BankConnection)
        .where(
            or_(Account.user_id == user_id, BankConnection.user_id == user_id),
            Account.is_closed == False,
        )
    )
    return [row[0] for row in result.all()]


async def _account_balance_at(
    session: AsyncSession, account: Account, cutoff: date
) -> float:
    """Get balance for a single account at a specific date.

    For bank-connected accounts, backtrack from the provider's current balance
    by subtracting transaction deltas that occurred after the cutoff.
    For manual accounts, sum transactions up to the cutoff date.
    """
    if account.connection_id:
        # Start from the provider's authoritative current balance
        current_bal = float(account.balance)
        if account.type == "credit_card":
            current_bal = -current_bal
        # Subtract activity after cutoff to get the balance AT cutoff
        delta_after = await session.scalar(
            select(func.coalesce(func.sum(_signed_balance_expr(account.currency)), 0))
            .where(
                Transaction.account_id == account.id,
                Transaction.date > cutoff,
            )
        )
        return current_bal - float(delta_after or 0)
    else:
        # Manual: sum signed transactions up to cutoff
        result = await session.scalar(
            select(func.coalesce(func.sum(_signed_balance_expr(account.currency)), 0))
            .where(
                Transaction.account_id == account.id,
                Transaction.date <= cutoff,
            )
        )
        return float(result or 0)


async def _total_balance_by_currency(
    session: AsyncSession, user_id: uuid.UUID, cutoff: date
) -> dict[str, float]:
    """Get total balance across all open accounts at a date, grouped by currency."""
    accounts = await _get_open_accounts(session, user_id)
    totals: dict[str, float] = {}
    for account in accounts:
        bal = await _account_balance_at(session, account, cutoff)
        totals[account.currency] = totals.get(account.currency, 0) + bal
    return totals


async def _balance_at(
    session: AsyncSession, user_id: uuid.UUID, cutoff: date
) -> float:
    """Get total balance across all open accounts at a specific date, converted to primary currency."""
    totals = await _total_balance_by_currency(session, user_id, cutoff)

    # If all same currency, just sum
    if len(totals) <= 1:
        return sum(totals.values())

    # Convert to primary currency
    user = await session.get(User, user_id)
    primary_currency = user.primary_currency if user else get_settings().default_currency

    total = 0.0
    for currency, amount in totals.items():
        converted, _ = await convert(session, Decimal(str(amount)), currency, primary_currency)
        total += float(converted)
    return total


async def _daily_deltas(
    session: AsyncSession, user_id: uuid.UUID, start: date, end: date
) -> dict[int, float]:
    """Get daily balance deltas for a date range [start, end).
    Computes per-account in native currency (using amount_primary only for
    foreign txs within an account), grouped by day and account currency,
    then converts each currency to primary. This is consistent with _balance_at."""
    # Use amount_primary only when tx currency differs from account currency
    effective = case(
        (Transaction.currency == Account.currency, Transaction.amount),
        else_=func.coalesce(Transaction.amount_primary, Transaction.amount),
    )
    signed = case(
        (Transaction.type == "credit", effective),
        else_=-effective,
    )
    result = await session.execute(
        select(
            func.extract("day", Transaction.date).label("day"),
            Account.currency,
            func.sum(signed),
        )
        .join(Account, Transaction.account_id == Account.id)
        .where(
            Transaction.user_id == user_id,
            Account.is_closed == False,
            Transaction.date >= start,
            Transaction.date < end,
        )
        .group_by("day", Account.currency)
    )
    rows = result.all()

    # Check if all same currency — skip conversion
    currencies_seen = {row[1] for row in rows}
    if len(currencies_seen) <= 1:
        return {int(row[0]): float(row[2] or 0) for row in rows}

    # Multiple currencies: convert each to primary
    user = await session.get(User, user_id)
    primary_currency = user.primary_currency if user else get_settings().default_currency

    deltas: dict[int, float] = {}
    for row in rows:
        day = int(row[0])
        currency = row[1]
        amount = float(row[2] or 0)
        if currency != primary_currency:
            converted, _ = await convert(session, Decimal(str(amount)), currency, primary_currency)
            amount = float(converted)
        deltas[day] = deltas.get(day, 0) + amount
    return deltas


async def get_balance_history(
    session: AsyncSession, user_id: uuid.UUID, month: Optional[date] = None
) -> BalanceHistory:
    if not month:
        month = date.today().replace(day=1)

    month_start, month_end = _month_range(month)
    prev_month_start = (month_start - timedelta(days=1)).replace(day=1)
    prev_month_end = month_start

    today = date.today()
    is_current = month_start.year == today.year and month_start.month == today.month
    days_in_month = (month_end - month_start).days
    cutoff_day = today.day if is_current else days_in_month

    prev_days_in_month = (prev_month_end - prev_month_start).days

    # Starting balances
    current_start = await _balance_at(session, user_id, month_start - timedelta(days=1))
    prev_start = await _balance_at(session, user_id, prev_month_start - timedelta(days=1))

    # Daily deltas from real transactions
    current_deltas = await _daily_deltas(session, user_id, month_start, month_end)
    prev_deltas = await _daily_deltas(session, user_id, prev_month_start, prev_month_end)

    # Recurring projections for future days of current month (converted to primary currency)
    proj_deltas: dict[int, float] = {}
    if month_end > today:
        user = await session.get(User, user_id)
        primary_currency = user.primary_currency if user else get_settings().default_currency
        proj_start = max(month_start, today + timedelta(days=1))
        projections = await _get_recurring_projections(session, user_id, proj_start, month_end)
        for proj in projections:
            day = proj["date"].day
            proj_converted, _ = await convert(
                session, Decimal(str(proj["amount"])), proj["currency"], primary_currency,
            )
            amount = float(proj_converted)
            signed = amount if proj["type"] == "credit" else -amount
            proj_deltas[day] = proj_deltas.get(day, 0) + signed

    # Build current month daily balances
    current_daily = []
    balance = current_start
    for day in range(1, days_in_month + 1):
        balance += current_deltas.get(day, 0) + proj_deltas.get(day, 0)
        current_daily.append(DailyBalance(
            day=day,
            balance=round(balance, 2) if day <= cutoff_day else None,
        ))

    # Build previous month daily balances
    prev_daily = []
    balance = prev_start
    for day in range(1, prev_days_in_month + 1):
        balance += prev_deltas.get(day, 0)
        prev_daily.append(DailyBalance(day=day, balance=round(balance, 2)))

    return BalanceHistory(current=current_daily, previous=prev_daily)
