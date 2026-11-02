"""loan contract terms: kind, original amount, rate, term and amortization

Revision ID: 099
Revises: 098
"""

import sqlalchemy as sa

from alembic import op

revision = "099"
down_revision = "098"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("accounts", sa.Column("loan_kind", sa.String(length=20), nullable=True))
    op.add_column(
        "accounts", sa.Column("loan_original_amount", sa.Numeric(precision=15, scale=2), nullable=True)
    )
    op.add_column(
        "accounts", sa.Column("loan_interest_rate", sa.Numeric(precision=9, scale=6), nullable=True)
    )
    op.add_column("accounts", sa.Column("loan_rate_period", sa.String(length=20), nullable=True))
    op.add_column("accounts", sa.Column("loan_term_months", sa.SmallInteger(), nullable=True))
    op.add_column("accounts", sa.Column("loan_first_payment_date", sa.Date(), nullable=True))
    op.add_column("accounts", sa.Column("loan_amortization", sa.String(length=10), nullable=True))


def downgrade() -> None:
    for column in (
        "loan_amortization",
        "loan_first_payment_date",
        "loan_term_months",
        "loan_rate_period",
        "loan_interest_rate",
        "loan_original_amount",
        "loan_kind",
    ):
        op.drop_column("accounts", column)
