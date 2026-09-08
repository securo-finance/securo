"""add composite indexes for dashboard transaction scans

Revision ID: 084
Revises: 083
Create Date: 2026-08-29
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "084"
down_revision: Union[str, None] = "083"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Balance calculations filter by account and cutoff together. The prior
    # single-column indexes forced PostgreSQL to fetch all rows for each
    # account and apply the date predicate afterwards.
    op.create_index(
        "ix_transactions_account_date",
        "transactions",
        ["account_id", "date"],
    )

    # Period aggregates use the manually overridden bill date first. These
    # expressions exactly match reporting_date_col() in cash and accrual mode;
    # ordinary indexes on date/effective_date cannot serve COALESCE predicates.
    op.create_index(
        "ix_transactions_workspace_cash_report_date",
        "transactions",
        ["workspace_id", sa.text("coalesce(effective_bill_date, date)")],
    )
    op.create_index(
        "ix_transactions_workspace_accrual_report_date",
        "transactions",
        ["workspace_id", sa.text("coalesce(effective_bill_date, effective_date)")],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_transactions_workspace_accrual_report_date",
        table_name="transactions",
    )
    op.drop_index(
        "ix_transactions_workspace_cash_report_date",
        table_name="transactions",
    )
    op.drop_index("ix_transactions_account_date", table_name="transactions")
