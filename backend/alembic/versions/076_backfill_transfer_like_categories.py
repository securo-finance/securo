"""backfill category for uncategorized credit-card-payment / self-transfer rows

PLUGGY_CATEGORY_MAP didn't recognize Pluggy's "Credit card payment" and
"Same person transfer" categories (they're level-2 categories under
"Transfers", not "Transfer - X" strings, so the " - " prefix split never
matched them). Transactions synced before the mapping fix landed uncategorized
and count toward income/expense totals, double-counting money that already
moved once as the original card spend (or the outgoing leg of the self
transfer).

Only touches rows the sync left uncategorized (category_id IS NULL) and only
when the workspace already has a "Transferências" category to file them
under, so a user's own manual categorization is never overridden.

Idempotent (re-running only finds rows that are still NULL). Issue #<TBD>.

Revision ID: 076
Revises: 075
Create Date: 2026-08-25
"""

from alembic import op

revision = "076"
down_revision = "075"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE transactions AS t
        SET category_id = c.id
        FROM categories AS c
        WHERE t.category_id IS NULL
          AND c.workspace_id = t.workspace_id
          AND c.name = 'Transferências'
          AND (
            t.raw_data ->> 'category' IN ('Credit card payment', 'Same person transfer')
            OR t.raw_data ->> 'category' LIKE 'Same person transfer - %'
          )
        """
    )


def downgrade() -> None:
    # No-op: clearing category_id wholesale would also wipe categorization
    # the user applied by hand afterwards. An operator who really needs to
    # reverse should run the equivalent UPDATE manually.
    pass
