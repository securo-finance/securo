"""register physical and virtual cards under a credit-card account

Revision ID: 078
Revises: 077
Create Date: 2026-08-30

An account statement can aggregate several physical or virtual cards. The
provider-owned final four is registered once per account so the user can give
it an optional local label, without copying a label into every transaction.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "078"
down_revision: Union[str, None] = "077"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "account_cards",
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
        sa.Column("masked_number", sa.String(length=4), nullable=False),
        sa.Column("label", sa.String(length=80), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("account_id", "masked_number", name="uq_account_cards_account_masked_number"),
    )
    op.create_index("ix_account_cards_workspace_id", "account_cards", ["workspace_id"])
    op.create_index(
        "ix_account_cards_workspace_account",
        "account_cards",
        ["workspace_id", "account_id"],
    )

    # Seed only normalized final fours already persisted in 077. Labels stay
    # null until the user chooses one; no raw card number enters the table.
    op.execute(
        """
        INSERT INTO account_cards (id, workspace_id, account_id, masked_number)
        SELECT gen_random_uuid(), tx.workspace_id, tx.account_id, tx.card_masked_number
        FROM transactions AS tx
        JOIN accounts AS account ON account.id = tx.account_id
        WHERE account.type = 'credit_card'
          AND tx.card_masked_number IS NOT NULL
        GROUP BY tx.workspace_id, tx.account_id, tx.card_masked_number
        ON CONFLICT (account_id, masked_number) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index("ix_account_cards_workspace_account", table_name="account_cards")
    op.drop_index("ix_account_cards_workspace_id", table_name="account_cards")
    op.drop_table("account_cards")
