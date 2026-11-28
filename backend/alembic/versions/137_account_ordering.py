"""add account ordering and preserve existing account order

Revision ID: 137
Revises: 136
Create Date: 2026-11-21 07:18:25.307398

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '137'
down_revision: Union[str, Sequence[str], None] = '136'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column("order", sa.Integer(), server_default="0", nullable=False),
    )
    # Before ordering was explicit, the list was ordered by account name.
    # Preserve that order for existing workspaces instead of making all rows
    # tie at zero and falling through to the balance-based sort.
    op.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT
                    id,
                    ROW_NUMBER() OVER (
                        PARTITION BY workspace_id
                        ORDER BY LOWER(name), id
                    ) - 1 AS position
                FROM accounts
            )
            UPDATE accounts
            SET "order" = ranked.position
            FROM ranked
            WHERE accounts.id = ranked.id
            """
        )
    )


def downgrade() -> None:
    op.drop_column("accounts", "order")
