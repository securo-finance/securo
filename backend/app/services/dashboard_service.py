import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import select, func, case, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

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
    reporting_date_col,
    viewer_shared_pnl,
    viewer_shared_spending_by_category,
)
from app.services.admin_service import get_credit_card_accounting_mode
from app.services.recurring_transaction_service import get_occurrences_in_range
from app.services.asset_service import get_asset_values_at
from app.services.fx_rate_service import _resolve_rate, convert
from app.models.user import User


def _month_range(month: date) -> tuple[date, date]:
    """Return (month_start, month_end) for a given date."""
    month_start = month.replace(day=1)
    if month.month == 12:
        month_end = month.replace(year=month.year + 1, month=1, day=1)
    else:
        month_end = month.replace(month=month.month + 1, day=1)
    return month_start, month_end


async def _materialized_recurring_occurrences(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    recurring_ids: set[uuid.UUID],
    range_start: date,
    range_end: date,
) -> set[tuple[uuid.UUID, date]]:
    """Return linked recurring occurrences already represented by real rows."""
    if not recurring_ids:
        return set()

    result = await session.execute(
        select(Transaction.recurring_transaction_id, Transaction.date).where(
            Transaction.workspace_id == workspace_id,
            Transaction.recurring_transaction_id.in_(recurring_ids),
            Transaction.date >= range_start,
            Transaction.date < range_end,
        )
    )
    return {
        (recurring_id, transaction_date)
        for recurring_id, transaction_date in result.all()
        if recurring_id is not None
    }


async def _get_recurring_projections(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    month_start: date,
    month_end: date,
    account_ids: Optional[list[uuid.UUID]] = None,
    *,
    include_transfer_like: bool = False,
) -> list[dict]:
    """Compute virtual recurring transaction projections for a month.
    Pure read — no DB writes. Returns list of dicts with category_id, amount, type, currency.

    When ``account_ids`` is given (Collection filter), only recurring rules on
    those accounts are projected. Empty list → no projections.

    Transfer-like categories are excluded by default because most callers use
    these rows for P&L, budgets, reports, or spending. Balance projections can
    opt in because transfers and investment applications still move cash in
    the affected account. Ignored categories never participate."""
    if account_ids is not None and len(account_ids) == 0:
        return []
    filters = [
        RecurringTransaction.workspace_id == workspace_id,
        RecurringTransaction.is_active == True,
        RecurringTransaction.start_date < month_end + timedelta(days=2),
        or_(
            RecurringTransaction.category_id.is_(None),
            Category.is_ignored.is_not(True),
        ),
    ]
    if not include_transfer_like:
        filters.append(or_(
            RecurringTransaction.category_id.is_(None),
            Category.treat_as_transfer.is_not(True),
        ))
    stmt = (
        select(RecurringTransaction)
        .outerjoin(Category, RecurringTransaction.category_id == Category.id)
        .where(*filters)
    )
    if account_ids:
        stmt = stmt.where(RecurringTransaction.account_id.in_(account_ids))
    result = await session.execute(stmt)
    recurring_list = list(result.scalars().all())

    # A recurrence may already have been materialized without its nominal
    # pointer being advanced yet (for example by generate-ahead workflows).
    # Suppress the matching virtual row so clients never render or total the
    # same occurrence twice.
    materialized_occurrences = await _materialized_recurring_occurrences(
        session,
        workspace_id,
        {rec.id for rec in recurring_list},
        month_start,
        month_end,
    )

    projections = []
    for rec in recurring_list:
        # Compute occurrences from the nominal pointer; linked rows are
        # filtered explicitly below because the pointer can lag materialization.
        occurrences = get_occurrences_in_range(
            start=rec.next_occurrence,
            frequency=rec.frequency,
            end_date=rec.end_date,
            range_start=month_start,
            range_end=month_end,
            intended_day=rec.day_of_month or rec.start_date.day,
            weekend_adjustment=rec.weekend_adjustment,
        )
        for occ_date in occurrences:
            if (rec.id, occ_date) in materialized_occurrences:
                continue
            projections.append({
                "category_id": rec.category_id,
                "amount": float(rec.amount),
                "type": rec.type,
                "currency": rec.currency,
                "date": occ_date,
            })
    return projections


async def _get_forecast_transactions(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    range_start: date,
    range_end: date,
    account_ids: Optional[list[uuid.UUID]] = None,
    range_date_col=None,
) -> list[Transaction]:
    """Return real rows that belong to the forecast, not the current balance.

    A pending row is forecast regardless of its date. A posted row dated after
    today is also forecast: this is how future installments and generate-ahead
    recurring rows follow the same rule as bank pending rows without needing a
    source-specific branch at every consumer.
    """
    if account_ids is not None and len(account_ids) == 0:
        return []

    today = date.today()
    bucket_date = range_date_col if range_date_col is not None else Transaction.date
    stmt = (
        select(Transaction)
        .join(Account, Transaction.account_id == Account.id)
        .outerjoin(Category, Transaction.category_id == Category.id)
        .where(
            Transaction.workspace_id == workspace_id,
            Account.is_closed == False,
            bucket_date >= range_start,
            bucket_date < range_end,
            Transaction.source != "opening_balance",
            or_(
                Transaction.status == "pending",
                bucket_date > today,
            ),
        )
        .options(
            selectinload(Transaction.account),
            selectinload(Transaction.category),
        )
        .order_by(Transaction.date.asc(), Transaction.created_at.asc())
    )
    if account_ids:
        stmt = stmt.where(Transaction.account_id.in_(account_ids))
    result = await session.execute(stmt)
    return list(result.scalars().all())


def _counts_as_user_pnl_row(tx: Transaction) -> bool:
    """Python equivalent of ``counts_as_user_pnl`` for loaded forecast rows."""
    category = tx.category
    return not (
        tx.transfer_pair_id
        or tx.is_ignored
        or tx.exclude_from_pnl
        or tx.source == "settlement"
        or (category and (category.treat_as_transfer or category.is_ignored))
    )


async def get_summary(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    month: Optional[date] = None,
    balance_date: Optional[date] = None,
    account_ids: Optional[list[uuid.UUID]] = None,
    asset_group_ids: Optional[list[uuid.UUID]] = None,
) -> DashboardSummary:
    if not month:
        month = date.today().replace(day=1)

    month_start, month_end = _month_range(month)
    today = date.today()

    # Collection filter (issue #105): show the *raw* P&L/balances of the
    # collection's accounts, plus the assets in its wallets (asset_group_ids);
    # group-split redistribution and cross-group shares are gated off so the
    # filtered numbers stay self-consistent. A wallet-only collection (wallets
    # but no accounts) still filters — coerce accounts to empty.
    if asset_group_ids is not None and account_ids is None:
        account_ids = []
    filtered = account_ids is not None
    acct_filter = [Transaction.account_id.in_(account_ids)] if filtered else []

    # Reporting mode is a global app setting (admin-controlled). When "accrual",
    # aggregation queries bucket credit card transactions by the bill's due
    # date (effective_date) instead of the purchase date — gives a true
    # cash-flow view.
    user = await session.get(User, user_id)
    accounting_mode = await get_credit_card_accounting_mode(session)
    report_date = reporting_date_col(accounting_mode)

    # Compute the effective cutoff date for balance calculation
    if balance_date:
        cutoff = balance_date
    elif month_end <= today:
        # Past month: last day of that month
        cutoff = month_end - timedelta(days=1)
    else:
        # Current or future month: today
        cutoff = today

    # ``total_balance`` is deliberately current/posted-only. Forecast rows are
    # accumulated separately so a future installment or pending bank row never
    # silently changes the number labelled as the current balance.
    total_balance = await _total_balance_by_currency(session, workspace_id, cutoff, account_ids)
    projected_balance = dict(total_balance)

    # For current/future months, project the balance by adding recurring
    # projections and real forecast rows from cutoff+1 through month_end.
    # A month that has already elapsed has no forecast: `cutoff` is its own
    # last day there, so `month_end > cutoff` alone would still let the whole
    # pending backlog land on a month that is already history.
    if month_end > cutoff and month_end > today:
        projection_start = cutoff + timedelta(days=1)
        balance_projections = await _get_recurring_projections(
            session,
            workspace_id,
            projection_start,
            month_end,
            account_ids,
            include_transfer_like=True,
        )
        for proj in balance_projections:
            signed = proj["amount"] if proj["type"] == "credit" else -proj["amount"]
            projected_balance[proj["currency"]] = projected_balance.get(proj["currency"], 0.0) + signed

        # Pending rows are forecast regardless of their transaction date. A
        # future-month dashboard still needs to carry an old/stale pending row
        # into the projected balance, so do not narrow this query to the
        # selected month's start.
        balance_forecast_start = date.min
        balance_forecast_transactions = await _get_forecast_transactions(
            session, workspace_id, balance_forecast_start, month_end, account_ids
        )
        for tx in balance_forecast_transactions:
            if tx.is_ignored or (tx.category and tx.category.is_ignored):
                continue
            # A connected provider is authoritative for today's current
            # number. If it exposes a pending row dated today or earlier, do
            # not add that same row again to the projected walk. A recurring
            # placeholder is the exception: we generated it from a schedule, so
            # it is missing from that number and still has to be applied.
            if (
                tx.account
                and tx.account.connection_id
                and tx.status == "pending"
                and tx.date <= today
                and tx.source != "recurring"
            ):
                continue
            account_currency = tx.account.currency if tx.account else tx.currency
            amount = (
                tx.amount
                if tx.currency == account_currency
                else (tx.amount_primary or tx.amount)
            )
            signed = amount if tx.type == "credit" else -amount
            projected_balance[account_currency] = (
                projected_balance.get(account_currency, 0.0) + float(signed)
            )

    # Monthly income and expenses — exclude opening_balance so initial deposits
    # don't inflate the month's income figure. counts_as_user_pnl() skips
    # paired transfers, transfer-like categories AND settlement movements
    # (whose offset is already in the owner's share, see share-only model).
    # Only posted (settled) transactions count in the actual period totals;
    # pending and future rows are layered into the forecast fields below.
    monthly_result = await session.execute(
        select(
            func.sum(case((Transaction.type == "credit", Transaction.amount), else_=0)),
            func.sum(case((Transaction.type == "debit", Transaction.amount), else_=0)),
        )
        .join(Account, Transaction.account_id == Account.id)
        .where(
            Transaction.workspace_id == workspace_id,
            Account.is_closed == False,
            report_date >= month_start,
            report_date < month_end,
            report_date <= today,
            Transaction.source != "opening_balance",
            Transaction.status == "posted",
            counts_as_user_pnl(),
            *acct_filter,
        )
    )
    monthly_row = monthly_result.one()
    monthly_income = float(monthly_row[0] or 0)
    monthly_expenses = float(monthly_row[1] or 0)

    if not filtered:
        # Subtract non-owner shares of the user's own split txs — they paid
        # for the others, so those amounts aren't their actual cost.
        own_offset_inc, own_offset_exp = await owner_split_offset_pnl(
            session,
            user_id,
            month_start,
            month_end,
            use_effective_date=False,
            workspace_id=workspace_id,
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

    # Keep actual and forecast totals separate. Pending rows, future-dated
    # installments and generate-ahead rows belong only to the projected view.
    real_monthly_income = monthly_income
    real_monthly_expenses = monthly_expenses
    projected_monthly_income = real_monthly_income
    projected_monthly_expenses = real_monthly_expenses

    # Add virtual recurring projections
    projections = await _get_recurring_projections(
        session, workspace_id, month_start, month_end, account_ids
    )
    forecast_transactions = await _get_forecast_transactions(
        session, workspace_id, month_start, month_end, account_ids,
        range_date_col=report_date,
    )
    for proj in projections:
        if proj["type"] == "credit":
            projected_monthly_income += proj["amount"]
        else:
            projected_monthly_expenses += proj["amount"]
    for tx in forecast_transactions:
        if not _counts_as_user_pnl_row(tx):
            continue
        if tx.type == "credit":
            projected_monthly_income += float(tx.amount)
        else:
            projected_monthly_expenses += float(tx.amount)

    # Account count — the active collection's accounts when filtered, else all
    # accounts in this workspace (manual + bank-connected).
    if filtered:
        accounts_count = len(account_ids)
    else:
        accounts_count = await session.scalar(
            select(func.count())
            .select_from(Account)
            .where(Account.workspace_id == workspace_id)
        ) or 0

    # Pending categorization — exclude opening_balance and transfer pairs
    pending_cat_filters = [
        Transaction.workspace_id == workspace_id,
        Transaction.category_id.is_(None),
        Transaction.source != "opening_balance",
        # Settlement-sourced rows are auto-generated movements (paying
        # back / receiving back a group debt). They aren't expenses or
        # income that need a category, so exclude them.
        Transaction.source != "settlement",
        Transaction.transfer_pair_id.is_(None),
        *acct_filter,
    ]
    pending_categorization_result = await session.execute(
        select(
            func.count(),
            func.coalesce(func.sum(func.abs(Transaction.amount)), 0),
        )
        .select_from(Transaction)
        .where(*pending_cat_filters)
    )
    pending_categorization, pending_categorization_amount = (
        pending_categorization_result.one()
    )
    pending_categorization_amount = abs(float(pending_categorization_amount or 0))

    # Get user's primary currency (user already loaded above for reporting mode)
    primary_currency = user.primary_currency if user else get_settings().default_currency

    # Asset values — use cutoff so past months show historical values. Under a
    # collection filter, include only assets in the collection's wallets
    # (asset_group_ids); a collection with no wallets → no assets.
    assets_value, assets_value_primary = await get_asset_values_at(
        session, workspace_id, as_of_date=cutoff, primary_currency=primary_currency,
        by_workspace=True,
        group_ids=(asset_group_ids or []) if filtered else None,
    )

    # Add asset values to total balance
    for currency, amount in assets_value.items():
        total_balance[currency] = total_balance.get(currency, 0.0) + amount
        projected_balance[currency] = projected_balance.get(currency, 0.0) + amount

    # Convert totals to primary currency
    total_balance_primary = 0.0
    for currency, amount in total_balance.items():
        converted, _ = await convert(session, Decimal(str(amount)), currency, primary_currency, cutoff)
        total_balance_primary += float(converted)

    projected_balance_primary = 0.0
    for currency, amount in projected_balance.items():
        converted, _ = await convert(session, Decimal(str(amount)), currency, primary_currency, cutoff)
        projected_balance_primary += float(converted)

    # Convert income/expenses to primary currency using amount_primary when available
    # Use real-only totals (without projections) to avoid double-counting;
    # projections are added separately below via convert().
    monthly_income_primary = real_monthly_income
    monthly_expenses_primary = abs(real_monthly_expenses)

    # Use amount_primary sums for more accurate multi-currency income/expenses.
    # Same posted-only rule as the native-currency totals above.
    primary_result = await session.execute(
        select(
            func.sum(case((Transaction.type == "credit", Transaction.amount_primary), else_=0)),
            func.sum(case((Transaction.type == "debit", Transaction.amount_primary), else_=0)),
        )
        .join(Account, Transaction.account_id == Account.id)
        .where(
            Transaction.workspace_id == workspace_id,
            Account.is_closed == False,
            report_date >= month_start,
            report_date < month_end,
            report_date <= today,
            Transaction.source != "opening_balance",
            Transaction.status == "posted",
            counts_as_user_pnl(),
            Transaction.amount_primary.isnot(None),
            *acct_filter,
        )
    )
    primary_row = primary_result.one()
    if primary_row[0] is not None or primary_row[1] is not None:
        monthly_income_primary = float(primary_row[0] or 0)
        monthly_expenses_primary = abs(float(primary_row[1] or 0))

    if not filtered:
        # Apply share-only offset in primary currency (FX-converted).
        own_offset_inc_pri, own_offset_exp_pri = await owner_split_offset_pnl(
            session,
            user_id,
            month_start,
            month_end,
            use_effective_date=False,
            primary_currency=primary_currency,
            workspace_id=workspace_id,
        )
        monthly_income_primary -= own_offset_inc_pri
        monthly_expenses_primary -= own_offset_exp_pri

    # Add the viewer's shared shares to primary totals too. The shares
    # are stored in the parent transaction's currency, so we convert
    # each currency bucket separately rather than re-using shared_income
    # / shared_expenses (which were summed without conversion).
    # Gated under a collection filter — those parent transactions live in
    # other users'/workspaces' accounts, outside the filtered account set.
    if not filtered:
        from app.models.group import GroupMember
        from app.models.transaction_split import TransactionSplit

        viewer_member_ids = select(GroupMember.id).where(
            GroupMember.linked_user_id == user_id,
            GroupMember.is_self.is_(False),
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
                report_date <= today,
                Transaction.status == "posted",
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

    projected_income_primary = monthly_income_primary
    projected_expenses_primary = monthly_expenses_primary

    # Add recurring projections to primary forecast totals (convert each)
    for proj in projections:
        proj_converted, _ = await convert(
            session, Decimal(str(proj["amount"])),
            proj["currency"], primary_currency,
        )
        if proj["type"] == "credit":
            projected_income_primary += float(proj_converted)
        else:
            projected_expenses_primary += float(proj_converted)
    for tx in forecast_transactions:
        if not _counts_as_user_pnl_row(tx):
            continue
        if tx.amount_primary is not None:
            forecast_converted = Decimal(str(tx.amount_primary))
        else:
            forecast_converted, _ = await convert(
                session, Decimal(str(tx.amount)), tx.currency, primary_currency,
            )
        if tx.type == "credit":
            projected_income_primary += abs(float(forecast_converted))
        else:
            projected_expenses_primary += abs(float(forecast_converted))

    # Aggregate the user's net pending balance across all groups they
    # participate in. We reuse the group balance computation so partial
    # settlements are already netted out.
    pending_shares_net = (
        0.0
        if filtered
        else await _compute_pending_shares_net(
            session, workspace_id, user_id, primary_currency
        )
    )

    return DashboardSummary(
        total_balance=total_balance,
        total_balance_primary=round(total_balance_primary, 2),
        projected_balance=projected_balance,
        projected_balance_primary=round(projected_balance_primary, 2),
        balance_date=cutoff.isoformat(),
        monthly_income=real_monthly_income,
        monthly_expenses=abs(real_monthly_expenses),
        monthly_income_primary=round(monthly_income_primary, 2),
        monthly_expenses_primary=round(monthly_expenses_primary, 2),
        projected_income=projected_monthly_income,
        projected_expenses=abs(projected_monthly_expenses),
        projected_income_primary=round(projected_income_primary, 2),
        projected_expenses_primary=round(projected_expenses_primary, 2),
        accounts_count=accounts_count,
        pending_categorization=pending_categorization,
        pending_categorization_amount=pending_categorization_amount,
        assets_value=assets_value,
        assets_value_primary=round(assets_value_primary, 2),
        primary_currency=primary_currency,
        pending_shares_net=round(pending_shares_net, 2),
    )


async def _compute_pending_shares_net(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    primary_currency: str,
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

    # Scope to groups that live in the CURRENT workspace (where the
    # user is the creator) PLUS groups they're a linked member of from
    # other workspaces (cross-workspace projection). Drop `is_self`
    # links — those are the user's own self-member in their own group,
    # already accounted for by the workspace-scoped path.
    owned_q = await session.execute(
        select(Group.id).where(
            Group.user_id == user_id,
            Group.workspace_id == workspace_id,
        )
    )
    owned_ids = {row[0] for row in owned_q.all()}
    linked_q = await session.execute(
        select(GroupMember.group_id, GroupMember.id).where(
            GroupMember.linked_user_id == user_id,
            GroupMember.is_self.is_(False),
        )
    )
    linked_rows = list(linked_q.all())
    linked_ids = {row.group_id for row in linked_rows} - owned_ids
    member_id_for_group = {row.group_id: row.id for row in linked_rows}

    total_primary = 0.0
    for gid in owned_ids | linked_ids:
        balances = await compute_balances(session, gid, workspace_id, user_id)
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
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    month: Optional[date] = None,
    account_ids: Optional[list[uuid.UUID]] = None,
) -> list[SpendingByCategory]:
    if not month:
        month = date.today().replace(day=1)

    month_start, month_end = _month_range(month)
    today = date.today()
    filtered = account_ids is not None
    acct_filter = [Transaction.account_id.in_(account_ids)] if filtered else []

    user = await session.get(User, user_id)
    accounting_mode = await get_credit_card_accounting_mode(session)
    primary_currency = user.primary_currency if user else get_settings().default_currency
    report_date = reporting_date_col(accounting_mode)

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
            Transaction.workspace_id == workspace_id,
            Account.is_closed == False,
            Transaction.type == "debit",
            report_date >= month_start,
            report_date < month_end,
            report_date <= today,
            Transaction.status == "posted",
            counts_as_user_pnl(),
            *acct_filter,
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
            "projected": 0.0,
        }

    # Subtract non-owner shares per category — owner-side splits should
    # contribute only the owner's share, not the full amount.
    owner_offset = {} if filtered else await owner_split_offset_by_category(
        session,
        user_id,
        month_start,
        month_end,
        use_effective_date=accounting_mode == "accrual",
        primary_currency=primary_currency,
        workspace_id=workspace_id,
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
    shared_by_cat = {} if filtered else await viewer_shared_spending_by_category(
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
                    "projected": 0.0,
                }

    # Add virtual recurring projections (debit only), converted to primary currency
    projections = await _get_recurring_projections(
        session, workspace_id, month_start, month_end, account_ids
    )
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
            spending_map[cat_id]["projected"] += proj_amount_float
        else:
            info = cat_cache.get(cat_id, {"name": "Sem categoria", "icon": "circle-help", "color": "#6B7280"})
            spending_map[cat_id] = {
                "name": info["name"],
                "icon": info["icon"],
                "color": info["color"],
                "total": 0.0,
                "projected": proj_amount_float,
            }

    # Pending and future-dated real transactions are forecast rows. They are
    # deliberately layered after posted actuals, alongside recurring rules.
    forecast_transactions = await _get_forecast_transactions(
        session, workspace_id, month_start, month_end, account_ids,
        range_date_col=report_date,
    )
    for tx in forecast_transactions:
        if tx.type != "debit" or tx.is_ignored:
            continue
        category = tx.category
        if not _counts_as_user_pnl_row(tx):
            continue
        cat_id = str(tx.category_id) if tx.category_id else None
        if cat_id and cat_id not in cat_cache:
            cat_cache[cat_id] = {
                "name": category.name if category else "Sem categoria",
                "icon": category.icon if category else "circle-help",
                "color": category.color if category else "#6B7280",
            }
        if tx.amount_primary is not None:
            projected_amount = float(abs(tx.amount_primary))
        else:
            amount, _ = await convert(
                session,
                Decimal(str(abs(tx.amount))),
                tx.currency,
                primary_currency,
            )
            projected_amount = float(amount)
        if cat_id in spending_map:
            spending_map[cat_id]["projected"] += projected_amount
        else:
            info = cat_cache.get(
                cat_id,
                {"name": "Sem categoria", "icon": "circle-help", "color": "#6B7280"},
            )
            spending_map[cat_id] = {**info, "total": 0.0, "projected": projected_amount}

    # `total` stays posted-only so the breakdown adds up to the expenses card,
    # which is also posted-only. The forecast rides along in `projected_total`
    # (actual + forecast) for callers that want the same split the card shows.
    grand_total = sum(entry["total"] for entry in spending_map.values())
    spending = []
    for cat_id, entry in sorted(spending_map.items(), key=lambda x: x[1]["total"], reverse=True):
        spending.append(SpendingByCategory(
            category_id=cat_id,
            category_name=entry["name"],
            category_icon=entry["icon"],
            category_color=entry["color"],
            total=entry["total"],
            projected_total=entry["total"] + entry.get("projected", 0.0),
            percentage=(entry["total"] / grand_total * 100) if grand_total else 0,
        ))

    return spending


async def get_monthly_trend(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    months: int = 6,
    account_ids: Optional[list[uuid.UUID]] = None,
) -> list[MonthlyTrend]:
    filtered = account_ids is not None
    acct_filter = [Transaction.account_id.in_(account_ids)] if filtered else []
    today = date.today()
    accounting_mode = await get_credit_card_accounting_mode(session)
    report_date = reporting_date_col(accounting_mode)
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
            Transaction.workspace_id == workspace_id,
            Account.is_closed == False,
            Transaction.source != "opening_balance",
            report_date <= today,
            Transaction.status == "posted",
            counts_as_user_pnl(),
            *acct_filter,
        )
        .group_by(month_label)
        .order_by(month_label.desc())
        .limit(months)
    )

    user = await session.get(User, user_id)
    primary_currency = user.primary_currency if user else get_settings().default_currency

    trend_map: dict[str, list[float]] = {}
    for row in result.all():
        trend_map[row[0]] = [float(row[1] or 0), abs(float(row[2] or 0))]

    # Keep the dashboard trend's current month useful even when its only
    # activity is still pending or future-dated. Those rows are forecast data,
    # not actuals, but this endpoint has no separate actual/projected series.
    forecast_start = today.replace(day=1)
    for _ in range(max(months - 1, 0)):
        forecast_start = (
            forecast_start.replace(year=forecast_start.year - 1, month=12)
            if forecast_start.month == 1
            else forecast_start.replace(month=forecast_start.month - 1)
        )
    forecast_end = _month_range(today.replace(day=1))[1]
    forecast_transactions = await _get_forecast_transactions(
        session, workspace_id, forecast_start, forecast_end, account_ids,
        range_date_col=report_date,
    )
    for tx in forecast_transactions:
        if not _counts_as_user_pnl_row(tx):
            continue
        tx_report_date = (
            tx.effective_bill_date
            or (tx.effective_date if accounting_mode == "accrual" else tx.date)
        )
        month_key = f"{tx_report_date.year:04d}-{tx_report_date.month:02d}"
        bucket = trend_map.setdefault(month_key, [0.0, 0.0])
        if tx.amount_primary is not None:
            amount = abs(float(tx.amount_primary))
        else:
            converted, _ = await convert(
                session, Decimal(str(abs(tx.amount))), tx.currency, primary_currency,
            )
            amount = abs(float(converted))
        if tx.type == "credit":
            bucket[0] += amount
        else:
            bucket[1] += amount

    trends_raw = sorted(
        ((month, values[0], values[1]) for month, values in trend_map.items()),
        key=lambda row: row[0],
        reverse=True,
    )[:months]

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
        if filtered:
            own_inc, own_exp, shared_inc, shared_exp = 0.0, 0.0, 0.0, 0.0
        else:
            own_inc, own_exp = await owner_split_offset_pnl(
                session, user_id, m_start, m_end,
                use_effective_date=accounting_mode == "accrual",
                primary_currency=primary_currency,
                workspace_id=workspace_id,
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
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    month: Optional[date] = None,
    *,
    account_id: Optional[uuid.UUID] = None,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
) -> list[ProjectedTransaction]:
    """Return virtual recurring transaction projections for a month,
    enriched with description and category info for display.

    A date range is inclusive at the API boundary. It is useful for Account
    Detail, while the month argument remains the compact dashboard contract.

    Transfer-like rules stay in: this lists upcoming movements rather than
    P&L, and an investment contribution is money that will leave the account.
    The P&L-shaped callers exclude them on their own side, through
    ``_get_recurring_projections(include_transfer_like=False)``.
    """
    if (from_date is None) != (to_date is None):
        raise ValueError("from_date and to_date must be provided together")
    if from_date and to_date:
        if from_date > to_date:
            raise ValueError("from_date must be before or equal to to_date")
        range_start, range_end = from_date, to_date + timedelta(days=1)
    else:
        if not month:
            month = date.today().replace(day=1)
        range_start, range_end = _month_range(month)

    # Get user's primary currency for live conversion
    user = await session.get(User, user_id)
    primary_currency = user.primary_currency if user else get_settings().default_currency

    stmt = (
        select(RecurringTransaction)
        .outerjoin(Category, RecurringTransaction.category_id == Category.id)
        .where(
            RecurringTransaction.workspace_id == workspace_id,
            RecurringTransaction.is_active == True,
            RecurringTransaction.start_date < range_end + timedelta(days=2),
            or_(
                RecurringTransaction.category_id.is_(None),
                Category.is_ignored.is_not(True),
            ),
        )
    )
    if account_id is not None:
        stmt = stmt.where(RecurringTransaction.account_id == account_id)
    result = await session.execute(stmt)
    recurring_list = list(result.scalars().all())

    materialized_occurrences = await _materialized_recurring_occurrences(
        session,
        workspace_id,
        {rec.id for rec in recurring_list},
        range_start,
        range_end,
    )

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
            range_start=range_start,
            range_end=range_end,
            intended_day=rec.day_of_month or rec.start_date.day,
            weekend_adjustment=rec.weekend_adjustment,
        )
        cat_name, cat_icon, cat_color = cat_map.get(rec.category_id, (None, None, None)) if rec.category_id else (None, None, None)

        # Convert to primary currency at the latest real rate. A missing rate
        # stays unknown; virtual projections must never manufacture a 1:1
        # conversion just to render a number.
        amt_primary = None
        if rec.currency != primary_currency:
            rate = await _resolve_rate(
                session, rec.currency, primary_currency,
            )
            if rate is not None:
                amt_primary = float(
                    (Decimal(str(rec.amount)) * rate).quantize(Decimal("0.01"))
                )

        for occ_date in occurrences:
            if (rec.id, occ_date) in materialized_occurrences:
                continue
            projections.append(ProjectedTransaction(
                recurring_id=str(rec.id),
                account_id=str(rec.account_id) if rec.account_id else None,
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


def _signed_transaction_amount(tx: Transaction) -> float:
    """Return a loaded transaction's signed amount in its account currency."""
    account_currency = tx.account.currency if tx.account else tx.currency
    amount = (
        tx.amount
        if tx.currency == account_currency
        else (tx.amount_primary or tx.amount)
    )
    signed = amount if tx.type == "credit" else -amount
    return float(signed)


async def _signed_forecast_amount_primary(
    session: AsyncSession, tx: Transaction, primary_currency: str
) -> float:
    """Convert a loaded forecast row's signed account amount to primary."""
    account_currency = tx.account.currency if tx.account else tx.currency
    converted, _ = await convert(
        session,
        Decimal(str(_signed_transaction_amount(tx))),
        account_currency,
        primary_currency,
    )
    return float(converted)


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
    session: AsyncSession,
    workspace_id: uuid.UUID,
    account_ids: Optional[list[uuid.UUID]] = None,
) -> list[Account]:
    """Get all non-closed accounts for a workspace.

    When ``account_ids`` is provided (an active Collection filter, issue #105),
    restrict to that subset. An empty list returns no accounts — a collection
    with no members shows nothing rather than everything.
    """
    if account_ids is not None and len(account_ids) == 0:
        return []
    stmt = (
        select(Account)
        .outerjoin(BankConnection)
        .where(
            or_(
                Account.workspace_id == workspace_id,
                BankConnection.workspace_id == workspace_id,
            ),
            Account.is_closed == False,
        )
    )
    if account_ids:
        stmt = stmt.where(Account.id.in_(account_ids))
    result = await session.execute(stmt)
    return [row[0] for row in result.all()]


async def _account_balance_at(
    session: AsyncSession,
    account: Account,
    cutoff: date,
    *,
    include_pending: bool = False,
) -> float:
    """Get balance for a single account at a specific date.

    The default is the current/posted view: pending rows and future-dated rows
    do not affect it. Connected accounts are the one explicit exception at the
    current cutoff: their provider balance is returned verbatim so it keeps
    matching the bank app. Forecast callers add pending/future rows separately.
    """
    today = date.today()
    balance_cutoff = min(cutoff, today)
    if account.connection_id:
        # Start from the provider's authoritative current balance
        current_bal = float(account.balance)
        if account.type == "credit_card":
            current_bal = -current_bal

        # The provider number is the source of truth for the current balance,
        # including whatever pending entries the provider happens to include.
        if cutoff >= today:
            return current_bal

        status_filter = [] if include_pending else [Transaction.status == "posted"]
        # Subtract activity after cutoff to get the balance AT cutoff
        # Exclude ignored transactions from balance calculation
        delta_after = await session.scalar(
            select(func.coalesce(func.sum(_signed_balance_expr(account.currency)), 0))
            .outerjoin(Category, Transaction.category_id == Category.id)
            .where(
                Transaction.account_id == account.id,
                Transaction.date > cutoff,
                Transaction.date <= today,
                Transaction.is_ignored == False,
                *status_filter,
                or_(
                    Transaction.category_id.is_(None),
                    Category.is_ignored == False,
                ),
            )
        )
        historical = current_bal - float(delta_after or 0)
        if not include_pending:
            # Provider snapshots can include pending rows. Remove those from
            # historical posted-only snapshots; the current snapshot above is
            # intentionally left untouched to preserve provider parity.
            pending_delta = await session.scalar(
                select(func.coalesce(func.sum(_signed_balance_expr(account.currency)), 0))
                .outerjoin(Category, Transaction.category_id == Category.id)
                .where(
                    Transaction.account_id == account.id,
                    Transaction.date <= today,
                    Transaction.status == "pending",
                    Transaction.is_ignored == False,
                    or_(
                        Transaction.category_id.is_(None),
                        Category.is_ignored == False,
                    ),
                )
            )
            historical -= float(pending_delta or 0)
        return historical
    else:
        # Manual: sum signed transactions up to cutoff
        # Exclude ignored transactions from balance calculation
        status_filter = [] if include_pending else [Transaction.status == "posted"]
        result = await session.scalar(
            select(func.coalesce(func.sum(_signed_balance_expr(account.currency)), 0))
            .outerjoin(Category, Transaction.category_id == Category.id)
            .where(
                Transaction.account_id == account.id,
                Transaction.date <= balance_cutoff,
                Transaction.is_ignored == False,
                *status_filter,
                or_(
                    Transaction.category_id.is_(None),
                    Category.is_ignored == False,
                ),
            )
        )
        return float(result or 0)


async def _total_balance_by_currency(
    session: AsyncSession, workspace_id: uuid.UUID, cutoff: date,
    account_ids: Optional[list[uuid.UUID]] = None,
    *,
    include_pending: bool = False,
) -> dict[str, float]:
    """Get total balance across all open accounts at a date, grouped by currency."""
    accounts = await _get_open_accounts(session, workspace_id, account_ids)
    if not accounts:
        return {}

    today = date.today()
    balance_cutoff = min(cutoff, today)
    connected = [account for account in accounts if account.connection_id]
    manual = [account for account in accounts if not account.connection_id]

    # Resolve every account of the same kind in one aggregate instead of one
    # query per account. This matters especially for balance-history, which
    # asks for two historical cutoffs and used to multiply the round-trips by
    # the number of accounts.
    effective = case(
        (Transaction.currency == Account.currency, Transaction.amount),
        else_=func.coalesce(Transaction.amount_primary, Transaction.amount),
    )
    signed = case(
        (Transaction.type == "credit", effective),
        else_=-effective,
    )

    async def grouped_sums(account_ids: list[uuid.UUID], *filters) -> dict[uuid.UUID, float]:
        if not account_ids:
            return {}
        result = await session.execute(
            select(Transaction.account_id, func.coalesce(func.sum(signed), 0))
            .join(Account, Transaction.account_id == Account.id)
            .outerjoin(Category, Transaction.category_id == Category.id)
            .where(
                Transaction.account_id.in_(account_ids),
                Transaction.is_ignored == False,
                *filters,
                or_(
                    Transaction.category_id.is_(None),
                    Category.is_ignored == False,
                ),
            )
            .group_by(Transaction.account_id)
        )
        return {row[0]: float(row[1] or 0) for row in result.all()}

    manual_sums = await grouped_sums(
        [account.id for account in manual],
        Transaction.date <= balance_cutoff,
        *([] if include_pending else [Transaction.status == "posted"]),
    )

    connected_deltas: dict[uuid.UUID, float] = {}
    connected_pending: dict[uuid.UUID, float] = {}
    if connected and cutoff < today:
        connected_ids = [account.id for account in connected]
        connected_deltas = await grouped_sums(
            connected_ids,
            Transaction.date > cutoff,
            Transaction.date <= today,
            *([] if include_pending else [Transaction.status == "posted"]),
        )
        if not include_pending:
            connected_pending = await grouped_sums(
                connected_ids,
                Transaction.date <= today,
                Transaction.status == "pending",
            )

    totals: dict[str, float] = {}
    for account in accounts:
        if account.connection_id:
            bal = float(account.balance)
            if account.type == "credit_card":
                bal = -bal
            if cutoff < today:
                bal -= connected_deltas.get(account.id, 0.0)
                if not include_pending:
                    bal -= connected_pending.get(account.id, 0.0)
        else:
            bal = manual_sums.get(account.id, 0.0)
        totals[account.currency] = totals.get(account.currency, 0) + bal
    return totals


async def _balance_at(
    session: AsyncSession, workspace_id: uuid.UUID, cutoff: date,
    *, primary_currency_hint: Optional[str] = None,
    account_ids: Optional[list[uuid.UUID]] = None,
    include_pending: bool = False,
) -> float:
    """Get total balance across all open accounts at a specific date, converted to primary currency.

    `primary_currency_hint` lets callers avoid an extra User lookup when they
    already know the workspace's primary currency.
    """
    totals = await _total_balance_by_currency(
        session, workspace_id, cutoff, account_ids, include_pending=include_pending
    )
    if not totals:
        return 0.0

    primary_currency = primary_currency_hint or get_settings().default_currency

    total = 0.0
    for currency, amount in totals.items():
        if currency == primary_currency:
            total += amount
            continue
        converted, _ = await convert(session, Decimal(str(amount)), currency, primary_currency)
        total += float(converted)
    return total


async def _daily_balance_deltas_by_date(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    start: date,
    end: date,
    *,
    primary_currency_hint: Optional[str] = None,
    account_ids: Optional[list[uuid.UUID]] = None,
    include_pending: bool = False,
) -> dict[date, float]:
    """Get daily balance deltas for a date range [start, end), keyed by date.

    Computes per-account in native currency (using amount_primary only for
    foreign txs within an account), grouped by date and account currency,
    then converts each currency to primary. This is consistent with
    ``_balance_at`` and avoids per-transaction FX conversion loops.
    """
    effective = case(
        (Transaction.currency == Account.currency, Transaction.amount),
        else_=func.coalesce(Transaction.amount_primary, Transaction.amount),
    )
    signed = case(
        (Transaction.type == "credit", effective),
        else_=-effective,
    )
    status_filter = [] if include_pending else [Transaction.status == "posted"]
    result = await session.execute(
        select(
            Transaction.date,
            Account.currency,
            func.sum(signed),
        )
        .join(Account, Transaction.account_id == Account.id)
        .outerjoin(Category, Transaction.category_id == Category.id)
        .where(
            Transaction.workspace_id == workspace_id,
            Account.is_closed == False,
            Transaction.date >= start,
            Transaction.date < end,
            Transaction.date <= date.today(),
            Transaction.is_ignored == False,
            *status_filter,
            or_(
                Transaction.category_id.is_(None),
                Category.is_ignored == False,
            ),
            *( [Transaction.account_id.in_(account_ids)] if account_ids is not None else [] ),
        )
        .group_by(Transaction.date, Account.currency)
    )

    primary_currency = primary_currency_hint or get_settings().default_currency
    deltas: dict[date, float] = {}
    for tx_date, currency, raw_amount in result.all():
        amount = raw_amount or 0
        if currency != primary_currency:
            converted, _ = await convert(session, Decimal(str(amount)), currency, primary_currency)
            amount = converted
        deltas[tx_date] = deltas.get(tx_date, 0.0) + float(amount)
    return deltas


async def _daily_deltas(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    start: date,
    end: date,
    *,
    primary_currency_hint: Optional[str] = None,
    account_ids: Optional[list[uuid.UUID]] = None,
    include_pending: bool = False,
) -> dict[int, float]:
    """Get daily balance deltas for a date range [start, end), keyed by day-of-month."""
    by_date = await _daily_balance_deltas_by_date(
        session,
        workspace_id,
        start,
        end,
        primary_currency_hint=primary_currency_hint,
        account_ids=account_ids,
        include_pending=include_pending,
    )
    return {tx_date.day: amount for tx_date, amount in by_date.items()}


async def get_balance_history(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    month: Optional[date] = None,
    account_ids: Optional[list[uuid.UUID]] = None,
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

    # Look up the user's primary currency once so the helpers can avoid
    # re-querying.
    user = await session.get(User, user_id)
    primary_currency = user.primary_currency if user else get_settings().default_currency

    # Starting balances
    current_start = await _balance_at(
        session, workspace_id, month_start - timedelta(days=1),
        primary_currency_hint=primary_currency, account_ids=account_ids,
    )
    prev_start = await _balance_at(
        session, workspace_id, prev_month_start - timedelta(days=1),
        primary_currency_hint=primary_currency, account_ids=account_ids,
    )

    # Daily deltas from real transactions
    current_deltas = await _daily_deltas(
        session, workspace_id, month_start, month_end,
        primary_currency_hint=primary_currency, account_ids=account_ids,
    )
    prev_deltas = await _daily_deltas(
        session, workspace_id, prev_month_start, prev_month_end,
        primary_currency_hint=primary_currency, account_ids=account_ids,
    )

    # Recurring projections for future days of current month (converted to primary currency)
    proj_deltas: dict[int, float] = {}
    if month_end > today:
        # A future month's chart starts from the posted-only current balance.
        # Carry forecast rows dated before that month into its projected
        # opening position. Connected accounts are the one exception: their
        # provider snapshot is authoritative and may already include pending
        # rows dated today or earlier.
        if month_start > today:
            carried_forecasts = await _get_forecast_transactions(
                session, workspace_id, date.min, month_start, account_ids
            )
            for tx in carried_forecasts:
                if tx.is_ignored or (tx.category and tx.category.is_ignored):
                    continue
                if tx.account and tx.account.connection_id and tx.status == "pending" and tx.date <= today:
                    continue
                current_start += await _signed_forecast_amount_primary(
                    session, tx, primary_currency
                )

        proj_start = max(month_start, today + timedelta(days=1))
        forecast_transactions = await _get_forecast_transactions(
            session,
            workspace_id,
            proj_start,
            month_end,
            account_ids,
        )
        for tx in forecast_transactions:
            if tx.is_ignored or (tx.category and tx.category.is_ignored):
                continue
            amount = _signed_transaction_amount(tx)
            if tx.account and tx.account.connection_id and tx.date <= today:
                continue
            converted, _ = await convert(
                session,
                Decimal(str(amount)),
                tx.account.currency if tx.account else tx.currency,
                primary_currency,
            )
            proj_deltas[tx.date.day] = proj_deltas.get(tx.date.day, 0) + float(converted)

        projections = await _get_recurring_projections(
            session, workspace_id, proj_start, month_end, account_ids
        )
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
