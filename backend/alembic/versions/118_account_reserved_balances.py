"""account reserved balances: provider-held earmarked funds inside an account

account_reserved_balances stores the "saldo reservado" a provider reports
inside a connected account (Pluggy ``bankData.reservedBalances``: Mercado Pago
cofrinhos, caixinhas). They are money the account holds that ``balance`` does
not include: ``accounts.balance`` keeps the provider's figure as reported, and
the value paths count this sum on top of it once (ADR 0022). Identity is
``(account_id, external_id, currency)``.

Revision ID: 118
Revises: 117
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "118"
down_revision = "117"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "account_reserved_balances" in inspector.get_table_names():
        return

    op.create_table(
        "account_reserved_balances",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("amount", sa.Numeric(precision=15, scale=2), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "account_id",
            "external_id",
            "currency",
            name="uq_account_reserved_balances_identity",
        ),
    )
    op.create_index(
        "ix_account_reserved_balances_workspace_id",
        "account_reserved_balances",
        ["workspace_id"],
    )
    op.create_index(
        "ix_account_reserved_balances_account_id",
        "account_reserved_balances",
        ["account_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_account_reserved_balances_account_id", table_name="account_reserved_balances"
    )
    op.drop_index(
        "ix_account_reserved_balances_workspace_id", table_name="account_reserved_balances"
    )
    op.drop_table("account_reserved_balances")
