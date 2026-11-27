"""stamp each budget with the currency its limit was typed in

Revision ID: 125
Revises: 124

A budget's limit is now read in its own currency and converted to the
viewer's display currency, like the spending it is compared with. Until now
nothing wrote `budgets.currency`: every row holds the column default from
migration 017 ('BRL'), whatever currency its owner used, and the limit was
read as a bare number in the viewer's currency.

Each row takes its owner's current display currency, or the instance default
when the owner never chose one, the same fallback as `User.primary_currency`.
That is the currency the limit has been shown in, so no number on screen
moves with this migration; from here on a display switch converts the limit
instead of relabeling it.
"""

import sqlalchemy as sa
from alembic import op

from app.core.config import get_settings

revision = "125"
down_revision = "124"
branch_labels = None
depends_on = None

BACKFILL_SQL = """
UPDATE budgets
SET currency = COALESCE(users.preferences::json ->> 'currency_display', :fallback)
FROM users
WHERE users.id = budgets.user_id
"""


def upgrade() -> None:
    op.execute(
        sa.text(BACKFILL_SQL).bindparams(fallback=get_settings().default_currency)
    )


def downgrade() -> None:
    # The old reading ignored the column, so there is nothing to restore.
    pass
