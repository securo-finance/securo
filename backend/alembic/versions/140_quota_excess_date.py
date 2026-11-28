"""the moment an account above its limits starts to feel it

A nullable timestamp on `user_quotas`, written by the operator and read only
when MANAGED_INSTANCE is on (ADR 0055). Null keeps ADR 0049's rule: an excess
has no effect.

Revision ID: 140
Revises: 139
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "140"
down_revision: Union[str, None] = "139"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user_quotas",
        sa.Column("excess_effective_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_quotas", "excess_effective_at")
