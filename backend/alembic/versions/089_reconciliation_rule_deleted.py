"""letting a workspace delete a rule that ships with the product

Shipped rules are a document in the image, not rows in the database, so
there is nothing to delete. That is what makes them keep improving with
the product, and it also meant a workspace could turn one off but never
be rid of it, which reads as us knowing better than the person whose
money it is.

So the row that already records "I changed this rule" learns to record
"I do not want this rule". A tombstone: the strategy still ships, this
workspace no longer runs it, and the page can offer it back because we
still know its name.

The column is not null with a false default, so every existing override
means exactly what it meant yesterday.
"""
import sqlalchemy as sa
from alembic import op

revision = "089"
down_revision = "088"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "reconciliation_rules",
        sa.Column(
            "deleted",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("reconciliation_rules", "deleted")
