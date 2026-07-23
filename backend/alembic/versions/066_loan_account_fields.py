"""add loan account fields

Revision ID: 066
Revises: 065
Create Date: 2026-07-23

Add fields for loan accounts: interest_rate, interest_type (flat/reducing),
loan_term_months, original_principal, and disburse_as_income.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "066"
down_revision: Union[str, None] = "065"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("accounts", sa.Column("interest_rate", sa.Numeric(precision=5, scale=2), nullable=True))
    op.add_column("accounts", sa.Column("interest_type", sa.String(20), nullable=True))
    op.add_column("accounts", sa.Column("loan_term_months", sa.SmallInteger(), nullable=True))
    op.add_column("accounts", sa.Column("original_principal", sa.Numeric(precision=15, scale=2), nullable=True))
    op.add_column("accounts", sa.Column("disburse_as_income", sa.Boolean(), server_default=sa.text("false"), nullable=True))


def downgrade() -> None:
    op.drop_column("accounts", "disburse_as_income")
    op.drop_column("accounts", "original_principal")
    op.drop_column("accounts", "loan_term_months")
    op.drop_column("accounts", "interest_type")
    op.drop_column("accounts", "interest_rate")
