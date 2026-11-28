"""the moment an account above its limits starts to feel it

A nullable timestamp on `user_quotas`, written by the operator and read only
when MANAGED_INSTANCE is on (ADR 0055). Null keeps ADR 0049's rule: an excess
has no effect.

`has_limits` tells a row the operator wrote numbers on (true, the only kind
before this) from one the date alone created (false), so setting a date on an
account held by the operator defaults (ADR 0056) keeps it on the defaults.

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
    op.add_column(
        "user_quotas",
        sa.Column("has_limits", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("user_quotas", "has_limits")
    op.drop_column("user_quotas", "excess_effective_at")
