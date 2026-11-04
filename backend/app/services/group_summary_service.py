"""Group spending aggregates.

The group page shows three figures that describe the group as a whole:
how much has moved through it, how that splits by month, and how it
splits by category. They are computed here, in SQL, for the same reason
balances are: the recent-transactions list is capped at 20 rows, so
deriving totals from it describes the last few weeks while claiming to
describe the group.

Amounts follow the convention the rest of the group page already uses —
``amount_primary`` when the sync stored one, the native ``amount``
otherwise.
"""

import uuid
from decimal import Decimal
from typing import Optional

from sqlalchemy import String, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.category import Category
from app.models.group import GroupMember
from app.models.transaction import Transaction
from app.models.transaction_split import TransactionSplit


def _amount_expr():
    return func.coalesce(Transaction.amount_primary, Transaction.amount)


def _group_transactions(group_id: uuid.UUID):
    """Transactions carrying a split for any member of this group."""
    member_ids = select(GroupMember.id).where(GroupMember.group_id == group_id)
    return (
        select(TransactionSplit.transaction_id)
        .where(TransactionSplit.group_member_id.in_(member_ids))
        .distinct()
    )


async def compute_summary(
    session: AsyncSession,
    group_id: uuid.UUID,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> Optional[dict]:
    # Same visibility rule as balances: workspace members plus
    # cross-workspace linked members all see the same totals.
    from app.services.group_service import get_group_visible

    group = await get_group_visible(session, group_id, workspace_id, user_id)
    if not group:
        return None

    tx_ids = _group_transactions(group_id)
    amount = _amount_expr()

    # "YYYY-MM" without to_char: the date renders as YYYY-MM-DD on both
    # PostgreSQL and the SQLite the test suite runs on, so the bucket
    # works everywhere instead of 500ing on one of them.
    month_label = func.substr(cast(Transaction.date, String), 1, 7).label("month")
    months_result = await session.execute(
        select(month_label, func.sum(amount))
        .where(Transaction.id.in_(tx_ids))
        .group_by(month_label)
        .order_by(month_label)
    )
    months = [{"month": row[0], "total": row[1] or 0} for row in months_result.all()]
    # The headline figure is the same row set, bucketed differently —
    # summing the buckets keeps the two from ever disagreeing.
    total = sum((m["total"] for m in months), Decimal("0"))

    # Spending only — credits coming back into the group aren't spend,
    # which is the rule the stacked bar already applied client-side.
    categories_result = await session.execute(
        select(
            Category.id,
            Category.name,
            Category.color,
            func.sum(amount),
        )
        .select_from(Transaction)
        .outerjoin(Category, Transaction.category_id == Category.id)
        .where(Transaction.id.in_(tx_ids), Transaction.type == "debit")
        .group_by(Category.id, Category.name, Category.color)
        .order_by(func.sum(amount).desc())
    )
    categories = [
        {
            "category_id": row[0],
            "name": row[1],
            "color": row[2],
            "total": row[3] or 0,
        }
        for row in categories_result.all()
    ]

    return {
        "group_id": group_id,
        "default_currency": group.default_currency,
        "total_moved": total,
        "months": months,
        "categories": categories,
    }
