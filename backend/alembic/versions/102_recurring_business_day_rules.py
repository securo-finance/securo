"""recurring schedule rules: business day, holidays, grace, keywords, tolerance

Revision ID: 102
Revises: 101

Adds the rules a salary paid on the fifth business day and a rent that may be
paid a day late need, and repairs two kinds of rows the old API let through:

- ``type`` values other than ``debit``/``credit`` (``expense``/``income``),
  which every balance and total silently left out;
- a pointer that sits on ``start_date`` when ``day_of_month`` says another
  day (start 1 Aug, day 17), so the first occurrence was due on the wrong day
  and a charge on the 17th could never be matched to it.
"""
import calendar
from datetime import date

import sqlalchemy as sa

from alembic import op

revision = "102"
down_revision = "101"
branch_labels = None
depends_on = None

_MONTHS_BY_FREQUENCY = {"monthly": 1, "quarterly": 3, "semiannual": 6, "yearly": 12}


def _on_day(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _snap(pointer: date, frequency: str, intended_day: int) -> date:
    candidate = _on_day(pointer.year, pointer.month, intended_day)
    if candidate >= pointer:
        return candidate
    month_index = pointer.month - 1 + _MONTHS_BY_FREQUENCY[frequency]
    return _on_day(pointer.year + month_index // 12, month_index % 12 + 1, intended_day)


def upgrade() -> None:
    # Installs that ran this while it was numbered 099 already have the columns;
    # there only the repairs below rerun, and they leave fixed rows alone.
    columns = {
        c["name"] for c in sa.inspect(op.get_bind()).get_columns("recurring_transactions")
    }
    if "business_day" not in columns:
        _add_columns()
    _repair_rows()


def _add_columns() -> None:
    op.add_column(
        "recurring_transactions",
        sa.Column("business_day", sa.Integer(), nullable=True),
    )
    op.add_column(
        "recurring_transactions",
        sa.Column(
            "count_saturdays", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column(
        "recurring_transactions",
        sa.Column("holiday_calendar", sa.String(length=10), nullable=True),
    )
    op.add_column(
        "recurring_transactions",
        sa.Column("grace_days", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "recurring_transactions",
        sa.Column("match_keywords", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "recurring_transactions",
        sa.Column(
            "amount_tolerance_percent",
            sa.Numeric(precision=5, scale=2),
            nullable=False,
            server_default="0",
        ),
    )
    op.create_check_constraint(
        "ck_recurring_transactions_amount_tolerance",
        "recurring_transactions",
        "amount_tolerance_percent >= 0 AND amount_tolerance_percent <= 50",
    )
    op.create_check_constraint(
        "ck_recurring_transactions_business_day",
        "recurring_transactions",
        "business_day IS NULL OR (business_day >= 1 AND business_day <= 23)",
    )
    op.create_check_constraint(
        "ck_recurring_transactions_grace_days",
        "recurring_transactions",
        "grace_days >= 0 AND grace_days <= 15",
    )
    op.create_check_constraint(
        "ck_recurring_transactions_holiday_calendar",
        "recurring_transactions",
        "holiday_calendar IS NULL OR holiday_calendar IN ('BR')",
    )


def _repair_rows() -> None:
    conn = op.get_bind()
    for table in ("recurring_transactions", "transactions"):
        conn.execute(sa.text(f"UPDATE {table} SET type = 'debit' WHERE type = 'expense'"))
        conn.execute(sa.text(f"UPDATE {table} SET type = 'credit' WHERE type = 'income'"))

    rows = conn.execute(
        sa.text(
            "SELECT id, frequency, day_of_month, next_occurrence "
            "FROM recurring_transactions "
            "WHERE day_of_month IS NOT NULL "
            "AND frequency IN ('monthly', 'quarterly', 'semiannual', 'yearly')"
        )
    ).all()
    for row_id, frequency, day_of_month, pointer in rows:
        snapped = _snap(pointer, frequency, day_of_month)
        if snapped != pointer:
            conn.execute(
                sa.text(
                    "UPDATE recurring_transactions SET next_occurrence = :d WHERE id = :id"
                ),
                {"d": snapped, "id": row_id},
            )


def downgrade() -> None:
    for name in (
        "ck_recurring_transactions_amount_tolerance",
        "ck_recurring_transactions_holiday_calendar",
        "ck_recurring_transactions_grace_days",
        "ck_recurring_transactions_business_day",
    ):
        op.drop_constraint(name, "recurring_transactions", type_="check")
    for column in (
        "amount_tolerance_percent",
        "match_keywords",
        "grace_days",
        "holiday_calendar",
        "count_saturdays",
        "business_day",
    ):
        op.drop_column("recurring_transactions", column)
