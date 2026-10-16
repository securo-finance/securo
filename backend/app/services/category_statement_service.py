"""Income & expense statement: net amounts per category, month by month.

Each category group (or ungrouped category) becomes one row holding its net
amount (credits minus debits) for the selected month and the months before
it, so a refund lowers the spending of the category it belongs to instead of
showing up as income. A row lands in the income or the expense section by the
sign of its all-time net, which keeps it in the same place as the user steps
through months. Uncategorized money has no such history to lean on, so it is
split by transaction type into one row per section.
"""

import uuid
from datetime import date
from typing import Optional

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.models.account import Account
from app.models.category import Category
from app.models.category_group import CategoryGroup
from app.models.transaction import Transaction
from app.schemas.report import (
    CategoryStatementResponse,
    CategoryStatementRow,
    CategoryStatementSection,
)
from app.services._query_filters import (
    counts_as_user_pnl,
    owner_split_offset_by_category,
    reporting_date_col,
    viewer_shared_spending_by_category,
)
from app.services.admin_service import get_credit_card_accounting_mode

UNCATEGORIZED_KEY = "uncategorized"

# {category_id or None: (credits, debits)} as positive magnitudes.
_MonthTotals = dict[Optional[uuid.UUID], tuple[float, float]]


def _shift_month(month_start: date, delta: int) -> date:
    index = month_start.year * 12 + month_start.month - 1 + delta
    return date(index // 12, index % 12 + 1, 1)


def _sum_values(rows: list[CategoryStatementRow], width: int) -> list[float]:
    return [round(sum(row.values[i] for row in rows), 2) for i in range(width)]


async def get_category_statement(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    month: date,
    months: int = 3,
    primary_currency: str = "USD",
    account_ids: Optional[list[uuid.UUID]] = None,
) -> CategoryStatementResponse:
    """Build the statement for ``month`` and the ``months - 1`` months before it.

    Posted transactions only, bucketed by the same reporting date as the other
    reports, and never past today. Split shares follow the budget rules: the
    owner keeps only their share and the viewer gains the shares other people
    put on them, both skipped when the report is scoped to some accounts.
    """
    filtered = account_ids is not None
    acct_filter = [Transaction.account_id.in_(account_ids)] if filtered else []
    accounting_mode = await get_credit_card_accounting_mode(session)
    report_date = reporting_date_col(accounting_mode)
    amount_expr = func.abs(func.coalesce(Transaction.amount_primary, Transaction.amount))
    today = app_today()

    selected = month.replace(day=1)
    month_starts = [_shift_month(selected, -i) for i in range(months)]
    base_filters = [
        Transaction.workspace_id == workspace_id,
        Account.is_closed == False,
        Transaction.source != "opening_balance",
        Transaction.status == "posted",
        report_date <= today,
        counts_as_user_pnl(),
        *acct_filter,
    ]

    cats_result = await session.execute(
        select(Category, CategoryGroup)
        .outerjoin(CategoryGroup, Category.group_id == CategoryGroup.id)
        .where(Category.workspace_id == workspace_id)
    )
    categories = cats_result.all()
    known_ids = {cat.id for cat, _ in categories}

    per_month: list[_MonthTotals] = []
    for m_start in month_starts:
        totals: _MonthTotals = {}
        if m_start > today:
            per_month.append(totals)
            continue
        m_end = _shift_month(m_start, 1)
        result = await session.execute(
            select(
                Transaction.category_id,
                func.sum(case((Transaction.type == "credit", amount_expr), else_=0)),
                func.sum(case((Transaction.type == "debit", amount_expr), else_=0)),
            )
            .select_from(Transaction)
            .join(Account, Transaction.account_id == Account.id)
            .where(*base_filters, report_date >= m_start, report_date < m_end)
            .group_by(Transaction.category_id)
        )
        for cat_id, credits, debits in result.all():
            totals[cat_id] = (float(credits or 0), float(debits or 0))

        if not filtered:
            use_effective_date = accounting_mode == "accrual"
            own_offset = await owner_split_offset_by_category(
                session, user_id, m_start, m_end,
                use_effective_date=use_effective_date,
                primary_currency=primary_currency,
                workspace_id=workspace_id,
            )
            for cat_id, offset in own_offset.items():
                credits, debits = totals.get(cat_id, (0.0, 0.0))
                totals[cat_id] = (credits, max(0.0, debits - float(offset)))
            shared = await viewer_shared_spending_by_category(
                session, user_id, m_start, m_end,
                use_effective_date=use_effective_date,
                primary_currency=primary_currency,
            )
            for cat_id, share in shared.items():
                # Shares of someone else's transaction carry that person's
                # category, which this workspace can't show.
                key = cat_id if cat_id in known_ids else None
                credits, debits = totals.get(key, (0.0, 0.0))
                totals[key] = (credits, debits + float(share))
        per_month.append(totals)

    # All-time net per category, up to the end of the selected month, decides
    # which section each row belongs to.
    lifetime_result = await session.execute(
        select(
            Transaction.category_id,
            func.sum(case((Transaction.type == "credit", amount_expr), else_=-amount_expr)),
        )
        .select_from(Transaction)
        .join(Account, Transaction.account_id == Account.id)
        .where(
            *base_filters,
            Transaction.category_id.isnot(None),
            report_date < _shift_month(selected, 1),
        )
        .group_by(Transaction.category_id)
    )
    lifetime_net = {cat_id: float(net or 0) for cat_id, net in lifetime_result.all()}

    def category_values(cat_id: uuid.UUID) -> list[float]:
        values = []
        for totals in per_month:
            credits, debits = totals.get(cat_id, (0.0, 0.0))
            values.append(round(credits - debits, 2))
        return values

    # Group categories into rows: one per group, one per ungrouped category.
    grouped: dict[uuid.UUID, tuple[CategoryGroup, list[Category]]] = {}
    ungrouped: list[Category] = []
    for cat, group in categories:
        if group is None:
            ungrouped.append(cat)
        else:
            grouped.setdefault(group.id, (group, []))[1].append(cat)

    income_rows: list[CategoryStatementRow] = []
    expense_rows: list[CategoryStatementRow] = []

    def place(row: CategoryStatementRow, cat_ids: list[uuid.UUID]) -> None:
        net = sum(lifetime_net.get(cat_id, 0.0) for cat_id in cat_ids)
        (income_rows if net > 0 else expense_rows).append(row)

    for group, cats in sorted(
        grouped.values(), key=lambda item: (item[0].position, item[0].name.lower())
    ):
        children = []
        for cat in sorted(cats, key=lambda c: c.name.lower()):
            values = category_values(cat.id)
            if any(values):
                children.append(
                    CategoryStatementRow(
                        key=str(cat.id), kind="category", label=cat.name,
                        icon=cat.icon, color=cat.color,
                        category_ids=[str(cat.id)], values=values,
                    )
                )
        if not children:
            continue
        place(
            CategoryStatementRow(
                key=str(group.id), kind="group", label=group.name,
                icon=group.icon, color=group.color,
                category_ids=[str(cat.id) for cat in cats],
                values=_sum_values(children, months),
                children=children,
            ),
            [cat.id for cat in cats],
        )

    for cat in sorted(ungrouped, key=lambda c: c.name.lower()):
        values = category_values(cat.id)
        if any(values):
            place(
                CategoryStatementRow(
                    key=str(cat.id), kind="category", label=cat.name,
                    icon=cat.icon, color=cat.color,
                    category_ids=[str(cat.id)], values=values,
                ),
                [cat.id],
            )

    uncategorized_credits = [round(t.get(None, (0.0, 0.0))[0], 2) for t in per_month]
    # `or 0.0` turns the -0.0 of an empty month back into a plain zero.
    uncategorized_debits = [-round(t.get(None, (0.0, 0.0))[1], 2) or 0.0 for t in per_month]
    if any(uncategorized_credits):
        income_rows.append(
            CategoryStatementRow(
                key=UNCATEGORIZED_KEY, kind="uncategorized", label=None,
                txn_type="credit", values=uncategorized_credits,
            )
        )
    if any(uncategorized_debits):
        expense_rows.append(
            CategoryStatementRow(
                key=UNCATEGORIZED_KEY, kind="uncategorized", label=None,
                txn_type="debit", values=uncategorized_debits,
            )
        )

    return CategoryStatementResponse(
        currency=primary_currency,
        months=[m.strftime("%Y-%m") for m in month_starts],
        income=CategoryStatementSection(
            totals=_sum_values(income_rows, months), rows=income_rows
        ),
        expenses=CategoryStatementSection(
            totals=_sum_values(expense_rows, months), rows=expense_rows
        ),
    )
