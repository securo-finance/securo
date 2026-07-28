"""add account archive history preference

Revision ID: 066
Revises: 065
Create Date: 2026-07-27
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "066"
down_revision: Union[str, None] = "065"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column(
            "exclude_from_history",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    # Closed accounts were historically excluded everywhere. Preserve that
    # behavior until their owner explicitly reopens or re-archives them.
    op.execute(
        sa.text(
            "UPDATE accounts "
            "SET exclude_from_history = true "
            "WHERE is_closed = true"
        )
    )


def downgrade() -> None:
    op.drop_column("accounts", "exclude_from_history")
