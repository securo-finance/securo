"""add transaction reporting date override

Revision ID: 090
Revises: 089
Create Date: 2026-09-08
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "090"
down_revision: Union[str, None] = "089"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "transactions",
        sa.Column("reporting_date_override", sa.Date(), nullable=True),
    )

    # Keep the hot period queries on expression indexes matching
    # reporting_date_col() exactly.
    op.drop_index(
        "ix_transactions_workspace_cash_report_date",
        table_name="transactions",
    )
    op.drop_index(
        "ix_transactions_workspace_accrual_report_date",
        table_name="transactions",
    )
    op.create_index(
        "ix_transactions_workspace_cash_report_date",
        "transactions",
        [
            "workspace_id",
            sa.text("coalesce(reporting_date_override, effective_bill_date, date)"),
        ],
    )
    op.create_index(
        "ix_transactions_workspace_accrual_report_date",
        "transactions",
        [
            "workspace_id",
            sa.text("coalesce(reporting_date_override, effective_bill_date, effective_date)"),
        ],
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
    op.drop_column("transactions", "reporting_date_override")
