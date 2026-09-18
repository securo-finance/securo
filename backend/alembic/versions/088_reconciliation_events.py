"""a chronological record of what matching did

Most of this is already stored, and that is the point of keeping the
table small. An automatic link is an `invoice_allocations` row with the
rule id in `method` and a timestamp; an answered suggestion is a
`reconciliation_suggestions` row with a status and who resolved it.
Reading a history out of the two means joining tables with different
shapes and no common ordering, which is the friction rather than the
missing data.

One thing genuinely leaves no trace today: **undoing a link deletes the
row**, so a match that was made and then reversed is indistinguishable
from one that never happened. That is the event this table exists for
first, and the reason the others are copied here is so the stream reads
in one order rather than two.

Deliberately **not** a log of everything the engine considered. A sync of
three hundred transactions where two hundred and ninety match nothing
would write two hundred and ninety rows saying so, and a history nobody
can scan is the same as no history.

Revision ID: 087
Revises: 086
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "088"
down_revision = "087"
branch_labels = None
depends_on = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "reconciliation_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("action", sa.String(length=16), nullable=False),
        # SET NULL rather than CASCADE: a deleted transaction should not
        # erase the record that something was once matched to it. The
        # history is the one place that has to survive the tidying up.
        sa.Column("transaction_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("expectation_kind", sa.String(length=16), nullable=False),
        sa.Column("expectation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("amount", sa.Numeric(precision=15, scale=2), nullable=False),
        sa.Column("strategy_id", sa.String(length=64), nullable=True),
        # Null means the system did it on its own, which is the difference
        # a reader most often wants: was this me, or was this the rules?
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("detail", _JSON, nullable=False, server_default="{}"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["transaction_id"], ["transactions.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "action IN ('linked', 'suggested', 'accepted', 'declined',"
            " 'expired', 'unlinked')",
            name="ck_reconciliation_event_action",
        ),
    )
    # The only two ways this is ever read: the workspace's stream, newest
    # first, and everything that happened to one promise.
    op.create_index(
        "ix_reconciliation_events_stream",
        "reconciliation_events",
        ["workspace_id", "at"],
    )
    op.create_index(
        "ix_reconciliation_events_expectation",
        "reconciliation_events",
        ["expectation_id"],
    )


def downgrade() -> None:
    op.drop_table("reconciliation_events")
