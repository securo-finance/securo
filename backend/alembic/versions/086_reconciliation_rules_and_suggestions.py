"""matching rules a workspace can change, and the doubtful space

Two tables that make reconciliation something a person can see and argue
with rather than something the code decides on their behalf.

  - `reconciliation_rules` stores **only what a workspace changed**. The
    rules ship with the image; copying them in here at workspace creation
    would freeze them, and a better default would never reach anyone who
    had already opened the page.
  - `reconciliation_suggestions` stores the matches that are plausible
    without being certain. `declined` is a stored status on purpose:
    without it, a suggestion somebody rejected returns on the next sync.

Revision ID: 085
Revises: 084
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "086"
down_revision = "085"
branch_labels = None
depends_on = None

#: jsonb where it exists, JSON elsewhere. Matching reads these on every
#: transaction, so on Postgres they should be the queryable type.
_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "reconciliation_rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("node", sa.String(length=64), nullable=False),
        sa.Column("strategy_id", sa.String(length=64), nullable=False),
        sa.Column(
            "origin", sa.String(length=16), nullable=False, server_default="default"
        ),
        sa.Column("name", sa.String(length=120), nullable=True),
        sa.Column("position", sa.Integer(), nullable=True),
        sa.Column("config", _JSON, nullable=False, server_default="{}"),
        sa.Column(
            "policy_version", sa.Integer(), nullable=False, server_default="1"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id", "node", "strategy_id", name="uq_reconciliation_rule_strategy"
        ),
        sa.CheckConstraint(
            "origin IN ('default', 'custom')", name="ck_reconciliation_rule_origin"
        ),
    )
    op.create_index(
        "ix_reconciliation_rules_workspace_id", "reconciliation_rules", ["workspace_id"]
    )
    op.create_index(
        "ix_reconciliation_rules_workspace_node",
        "reconciliation_rules",
        ["workspace_id", "node"],
    )

    op.create_table(
        "reconciliation_suggestions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("transaction_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("expectation_kind", sa.String(length=16), nullable=False),
        sa.Column("expectation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("strategy_id", sa.String(length=64), nullable=False),
        sa.Column("node", sa.String(length=64), nullable=False),
        sa.Column("amount", sa.Numeric(precision=15, scale=2), nullable=False),
        sa.Column("scores", _JSON, nullable=False, server_default="{}"),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="pending"
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["transaction_id"], ["transactions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["resolved_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "transaction_id",
            "expectation_kind",
            "expectation_id",
            name="uq_reconciliation_suggestion_pair",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'declined', 'expired')",
            name="ck_reconciliation_suggestion_status",
        ),
        sa.CheckConstraint(
            "expectation_kind IN ('invoice', 'recurring')",
            name="ck_reconciliation_suggestion_kind",
        ),
    )
    op.create_index(
        "ix_reconciliation_suggestions_workspace_id",
        "reconciliation_suggestions",
        ["workspace_id"],
    )
    op.create_index(
        "ix_reconciliation_suggestions_transaction_id",
        "reconciliation_suggestions",
        ["transaction_id"],
    )
    op.create_index(
        "ix_reconciliation_suggestions_expectation_id",
        "reconciliation_suggestions",
        ["expectation_id"],
    )
    op.create_index(
        "ix_reconciliation_suggestions_status", "reconciliation_suggestions", ["status"]
    )
    op.create_index(
        "ix_reconciliation_suggestions_open",
        "reconciliation_suggestions",
        ["workspace_id", "status", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("reconciliation_suggestions")
    op.drop_table("reconciliation_rules")
