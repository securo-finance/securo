"""transaction review workflow: triage state, provenance stamps, flag, policy

Adds the two-state triage axis to transactions — issue #636's New/Read
badge flow and issue #56's flag — plus the per-workspace ingest/manual
review policy.

  - `transactions.review_status` ('needs_review' | 'reviewed') with a
    fail-quiet ``server_default 'reviewed'``: existing rows backfill to
    reviewed at catalog speed (no UPDATE statements), and `needs_review`
    is applied explicitly at ingest only — an inbox flood on upgrade is
    the failure mode being designed out.
  - `reviewed_at` / `reviewed_by_id`: provenance populated together by
    explicit actor-driven transitions into 'reviewed', NULL together on
    legacy/exempt rows. Never exactly one of the two (gate §15-A1).
  - `transactions.flagged`: the #56 bookmark boolean. Independent of
    review state, zero monetary effect.
  - `workspaces.review_policy`: {"ingest": "needs_review"|"reviewed",
    "manual": ...}; NULL means defaults (ingest lands needs_review,
    manual lands reviewed).
  - Index (workspace_id, review_status) serves the needs-review chip
    count and list filter on large ledgers.

Forward-only additive migration: no column rewrites, no data backfill
statements, no destructive ops. Rollback drops the new columns (review
state only).

Revision ID: 098
Revises: 097
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "103"
down_revision = "102"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "transactions",
        sa.Column(
            "review_status",
            sa.String(length=20),
            nullable=False,
            server_default="reviewed",
        ),
    )
    op.add_column(
        "transactions",
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "transactions",
        sa.Column("reviewed_by_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "transactions",
        sa.Column("flagged", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_foreign_key(
        "fk_transactions_reviewed_by_id",
        "transactions",
        "users",
        ["reviewed_by_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_transactions_workspace_review_status",
        "transactions",
        ["workspace_id", "review_status"],
    )

    op.add_column(
        "workspaces",
        sa.Column("review_policy", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workspaces", "review_policy")

    op.drop_index(
        "ix_transactions_workspace_review_status", table_name="transactions"
    )
    op.drop_constraint(
        "fk_transactions_reviewed_by_id", "transactions", type_="foreignkey"
    )
    op.drop_column("transactions", "flagged")
    op.drop_column("transactions", "reviewed_by_id")
    op.drop_column("transactions", "reviewed_at")
    op.drop_column("transactions", "review_status")
