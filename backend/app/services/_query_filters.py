"""Shared SQLAlchemy filter fragments for report/dashboard queries.

Centralizes the "what counts as real income/expense" definition so every
aggregation site agrees. Changes to the rule (e.g. adding a new exclusion
signal) only need to be made here.
"""
import uuid
from datetime import date
from typing import Optional

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.category import Category
from app.models.transaction import Transaction


def counts_as_pnl():
    """SQL filter: True when a transaction should contribute to income/expense totals.

    Excludes:
      - paired transfers (both legs were matched; already cancel out),
      - transactions in categories flagged `treat_as_transfer` (one-sided
        movements like investment applications where the counterpart is
        an Asset/Holding, not another Account),
      - transactions flagged `is_ignored=True` (user-marked as not to be reported),
      - transactions in categories flagged `is_ignored=True` (user-marked as not to be reported).

    Does NOT exclude `source='opening_balance'` — callers that already
    filter those keep doing so; this helper only handles the transfer-like
    exclusion family so both rules stay visible at each call site.
    """
    return and_(
        Transaction.transfer_pair_id.is_(None),
        Transaction.is_ignored.is_(False),
        # Settlement *debits* are repayments of debts that were already
        # booked as an expense via the share. Counting them would
        # double-count. Settlement *credits*, however, represent the
        # receiver actually getting the cash back — they offset the
        # over-recorded expense from when the receiver paid the full
        # parent transaction. So we keep credits, drop debits.
        ~and_(Transaction.source == "settlement", Transaction.type == "debit"),
        or_(
            Transaction.category_id.is_(None),
            Transaction.category_id.not_in(
                select(Category.id).where(
                    or_(
                        Category.treat_as_transfer.is_(True),
                        Category.is_ignored.is_(True),
                    )
                )
            ),
        ),
    )


def counts_as_user_pnl():
    """SQL filter for *user-level* P/L (dashboard, reports, budgets).

    Stricter than `counts_as_pnl`: also drops settlement *credits*. Under
    the share-only model an owner's expense for a split tx is just their
    share, so the corresponding settlement credits would double-count.
    Per-account stats still use `counts_as_pnl` because account ledgers
    track real cash through the account, not user P/L.
    """
    return and_(
        counts_as_pnl(),
        Transaction.source != "settlement",
    )


async def owner_split_offset_pnl(
    session: AsyncSession,
    user_id: uuid.UUID,
    month_start: date,
    month_end: date,
    use_effective_date: bool = False,
    primary_currency: Optional[str] = None,
) -> tuple[float, float]:
    """Return (income_offset, expense_offset) — the totals to *subtract*
    from the owner's full-amount aggregations so only their own share
    remains. We sum every split share that belongs to a non-owner
    member; subtracting that from the parent's full amount leaves the
    owner's share."""
    from app.models.group import Group, GroupMember
    from app.models.transaction_split import TransactionSplit

    # The user's own member entries: linked_user_id matches OR is_self=true
    # in a group they own. The first form covers groups they're invited to;
    # the second covers their own groups (where is_self=true marks the owner).
    viewer_member_ids = (
        select(GroupMember.id)
        .outerjoin(Group, Group.id == GroupMember.group_id)
        .where(
            or_(
                GroupMember.linked_user_id == user_id,
                and_(GroupMember.is_self == True, Group.user_id == user_id),
            )
        )
    )
    date_col = Transaction.effective_date if use_effective_date else Transaction.date

    result = await session.execute(
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
        .join(Transaction, TransactionSplit.transaction_id == Transaction.id)
        .where(
            Transaction.user_id == user_id,
            TransactionSplit.group_member_id.notin_(viewer_member_ids),
            Transaction.source != "opening_balance",
            date_col >= month_start,
            date_col < month_end,
            counts_as_user_pnl(),
        )
        .group_by(Transaction.currency)
    )

    if primary_currency is None:
        income_total = 0.0
        expense_total = 0.0
        for row in result.all():
            income_total += float(row[1] or 0)
            expense_total += float(row[2] or 0)
        return income_total, expense_total

    from decimal import Decimal as _Decimal

    from app.services.fx_rate_service import convert as _convert

    income_total = 0.0
    expense_total = 0.0
    for row in result.all():
        cur, inc_raw, exp_raw = row[0], row[1] or 0, row[2] or 0
        if inc_raw:
            inc_pri, _ = await _convert(session, _Decimal(str(inc_raw)), cur, primary_currency)
            income_total += float(inc_pri)
        if exp_raw:
            exp_pri, _ = await _convert(session, _Decimal(str(exp_raw)), cur, primary_currency)
            expense_total += float(exp_pri)
    return income_total, expense_total


async def owner_split_offset_by_category(
    session: AsyncSession,
    user_id: uuid.UUID,
    month_start: date,
    month_end: date,
    use_effective_date: bool = False,
    primary_currency: Optional[str] = None,
) -> dict:
    """Per-category, sum of non-owner shares on owner-side debit splits —
    subtract from full owner debits to get the owner's category share."""
    from app.models.group import Group, GroupMember
    from app.models.transaction_split import TransactionSplit

    viewer_member_ids = (
        select(GroupMember.id)
        .outerjoin(Group, Group.id == GroupMember.group_id)
        .where(
            or_(
                GroupMember.linked_user_id == user_id,
                and_(GroupMember.is_self == True, Group.user_id == user_id),
            )
        )
    )
    date_col = Transaction.effective_date if use_effective_date else Transaction.date

    result = await session.execute(
        select(
            Transaction.category_id,
            Transaction.currency,
            func.sum(TransactionSplit.share_amount),
        )
        .join(Transaction, TransactionSplit.transaction_id == Transaction.id)
        .where(
            Transaction.user_id == user_id,
            Transaction.type == "debit",
            TransactionSplit.group_member_id.notin_(viewer_member_ids),
            Transaction.source != "opening_balance",
            date_col >= month_start,
            date_col < month_end,
            counts_as_user_pnl(),
        )
        .group_by(Transaction.category_id, Transaction.currency)
    )

    out: dict = {}
    if primary_currency is None:
        for cat_id, _cur, total in result.all():
            out[cat_id] = out.get(cat_id, 0.0) + float(total or 0)
        return out

    from decimal import Decimal as _Decimal

    from app.services.fx_rate_service import convert as _convert

    for cat_id, cur, total in result.all():
        if not total:
            continue
        converted, _ = await _convert(session, _Decimal(str(total)), cur, primary_currency)
        out[cat_id] = out.get(cat_id, 0.0) + float(converted)
    return out


async def viewer_shared_pnl(
    session: AsyncSession,
    user_id: uuid.UUID,
    month_start: date,
    month_end: date,
    use_effective_date: bool = False,
    primary_currency: Optional[str] = None,
) -> tuple[float, float]:
    """Return (income, expense) totals contributed by transactions the
    viewer doesn't own but participates in via a group split.

    Concert tickets paid by a friend show up as the viewer's share in
    their own spending picture — without inflating account balances.

    When `primary_currency` is given, mixed-currency shares are
    converted to that currency via the FX service. Otherwise, sums
    are taken in raw nominal terms (only safe when all shares are
    same-currency, e.g., during single-currency tests).
    """
    from app.models.group import GroupMember
    from app.models.transaction_split import TransactionSplit

    member_ids = select(GroupMember.id).where(GroupMember.linked_user_id == user_id)
    date_col = Transaction.effective_date if use_effective_date else Transaction.date

    result = await session.execute(
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
        .join(Transaction, TransactionSplit.transaction_id == Transaction.id)
        .where(
            TransactionSplit.group_member_id.in_(member_ids),
            # Avoid double-counting if the viewer also owns the parent.
            Transaction.user_id != user_id,
            Transaction.source != "opening_balance",
            date_col >= month_start,
            date_col < month_end,
            counts_as_pnl(),
        )
        .group_by(Transaction.currency)
    )

    if primary_currency is None:
        # Backward-compatible nominal sum (caller is responsible for
        # currency homogeneity). Acceptable when only one currency is
        # in play.
        income_total = 0.0
        expense_total = 0.0
        for row in result.all():
            income_total += float(row[1] or 0)
            expense_total += float(row[2] or 0)
        return income_total, expense_total

    # FX-aware: convert each currency bucket to primary.
    from decimal import Decimal as _Decimal

    from app.services.fx_rate_service import convert as _convert

    income_total = 0.0
    expense_total = 0.0
    for row in result.all():
        cur, inc_raw, exp_raw = row[0], row[1] or 0, row[2] or 0
        if inc_raw:
            inc_pri, _ = await _convert(session, _Decimal(str(inc_raw)), cur, primary_currency)
            income_total += float(inc_pri)
        if exp_raw:
            exp_pri, _ = await _convert(session, _Decimal(str(exp_raw)), cur, primary_currency)
            expense_total += float(exp_pri)
    return income_total, expense_total


async def viewer_shared_spending_by_category(
    session: AsyncSession,
    user_id: uuid.UUID,
    month_start: date,
    month_end: date,
    use_effective_date: bool = False,
    primary_currency: Optional[str] = None,
) -> dict:
    """Return {category_id (uuid|None): total_share_expense_float} for
    transactions where the viewer participates via a group split.

    With `primary_currency`, totals are FX-converted; otherwise raw
    nominal sums (single-currency only)."""
    from app.models.group import GroupMember
    from app.models.transaction_split import TransactionSplit

    member_ids = select(GroupMember.id).where(GroupMember.linked_user_id == user_id)
    date_col = Transaction.effective_date if use_effective_date else Transaction.date

    result = await session.execute(
        select(
            Transaction.category_id,
            Transaction.currency,
            func.sum(TransactionSplit.share_amount),
        )
        .join(Transaction, TransactionSplit.transaction_id == Transaction.id)
        .where(
            TransactionSplit.group_member_id.in_(member_ids),
            Transaction.user_id != user_id,
            Transaction.type == "debit",
            Transaction.source != "opening_balance",
            date_col >= month_start,
            date_col < month_end,
            counts_as_pnl(),
        )
        .group_by(Transaction.category_id, Transaction.currency)
    )

    out: dict = {}
    if primary_currency is None:
        for cat_id, _cur, total in result.all():
            out[cat_id] = out.get(cat_id, 0.0) + float(total or 0)
        return out

    from decimal import Decimal as _Decimal

    from app.services.fx_rate_service import convert as _convert

    for cat_id, cur, total in result.all():
        if not total:
            continue
        converted, _ = await _convert(session, _Decimal(str(total)), cur, primary_currency)
        out[cat_id] = out.get(cat_id, 0.0) + float(converted)
    return out
