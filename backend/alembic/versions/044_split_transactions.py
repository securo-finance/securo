"""split transactions: groups, members, splits, settlements

Adds the schema for splitting a transaction across multiple participants
(roommates, travel groups, cost centers, projects, clients). Independent
from the account ledger — the transaction keeps the full amount on the
owner's account; splits track the social/allocation debt separately.

Tables:
- groups: a bag of participants. `kind` makes the same table serve B2C
  social groups and B2B cost-center/project/client allocations without
  schema changes.
- group_members: participants. `linked_user_id` is nullable so shadow
  members (no Securo account) work from day one.
- transaction_splits: per-member share of a transaction. `share_amount`
  is always materialized in the transaction's currency; `share_type` is
  metadata for round-trip editing.
- group_settlements: payback / chargeback records, optionally linked to
  a real transaction when cash actually moves.

`groups.user_id` is the single owner today and will become workspace_id
when multi-user workspaces land — the FK shape is identical, so the
migration cost is just a column rename + backfill.

Revision ID: 044
Revises: 043
Create Date: 2026-04-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "044"
down_revision: Union[str, None] = "043"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "groups",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(100), nullable=False),
        # social | cost_center | project | client | other. Drives UI labels
        # and (later) settlement workflows; the data model is identical.
        sa.Column("kind", sa.String(20), server_default="social", nullable=False),
        sa.Column("default_currency", sa.String(3), server_default="USD", nullable=False),
        sa.Column("icon", sa.String(50), server_default="users", nullable=False),
        sa.Column("color", sa.String(7), server_default="#6B7280", nullable=False),
        sa.Column("is_archived", sa.Boolean, server_default=sa.text("false"), nullable=False),
        sa.Column("notes", sa.String(1000), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_groups_user_id", "groups", ["user_id"])
    op.create_unique_constraint("uq_groups_user_id_name", "groups", ["user_id", "name"])

    op.create_table(
        "group_members",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "group_id",
            UUID(as_uuid=True),
            sa.ForeignKey("groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(100), nullable=False),
        # Optional link to a real Securo user. SET NULL on delete so the
        # shadow record (and its history) survives if the linked account
        # is removed.
        sa.Column(
            "linked_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column(
            "is_self",
            sa.Boolean,
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_group_members_group_id", "group_members", ["group_id"])
    op.create_index("ix_group_members_linked_user_id", "group_members", ["linked_user_id"])
    op.create_unique_constraint(
        "uq_group_members_group_id_name", "group_members", ["group_id", "name"]
    )

    op.create_table(
        "transaction_splits",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "transaction_id",
            UUID(as_uuid=True),
            sa.ForeignKey("transactions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # RESTRICT: a member with active splits cannot be removed from
        # the group without first reassigning or deleting those splits.
        # Group-level CASCADE still works when no splits exist; otherwise
        # the user is forced to clean up first (UI-driven).
        sa.Column(
            "group_member_id",
            UUID(as_uuid=True),
            sa.ForeignKey("group_members.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        # Always materialized in the parent transaction's currency. The
        # rounding residual is assigned to the last share at write time
        # so the sum is exact.
        sa.Column("share_amount", sa.Numeric(precision=15, scale=2), nullable=False),
        # equal | exact | percent — metadata only; share_amount is the
        # source of truth.
        sa.Column("share_type", sa.String(10), server_default="exact", nullable=False),
        # Preserved only for share_type='percent' so an edit round-trips
        # without re-deriving.
        sa.Column("share_pct", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("notes", sa.String(500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_transaction_splits_transaction_id",
        "transaction_splits",
        ["transaction_id"],
    )
    op.create_index(
        "ix_transaction_splits_group_member_id",
        "transaction_splits",
        ["group_member_id"],
    )
    op.create_unique_constraint(
        "uq_transaction_splits_tx_member",
        "transaction_splits",
        ["transaction_id", "group_member_id"],
    )

    op.create_table(
        "group_settlements",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "group_id",
            UUID(as_uuid=True),
            sa.ForeignKey("groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "from_member_id",
            UUID(as_uuid=True),
            sa.ForeignKey("group_members.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "to_member_id",
            UUID(as_uuid=True),
            sa.ForeignKey("group_members.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(precision=15, scale=2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("date", sa.Date, nullable=False),
        # Optional pointer to a real bank transaction so the cash side
        # reconciles against the account ledger. SET NULL: settlement
        # record survives if the linked tx is later deleted.
        sa.Column(
            "transaction_id",
            UUID(as_uuid=True),
            sa.ForeignKey("transactions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("notes", sa.String(1000), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_group_settlements_group_id", "group_settlements", ["group_id"])
    op.create_index(
        "ix_group_settlements_transaction_id",
        "group_settlements",
        ["transaction_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_group_settlements_transaction_id", table_name="group_settlements")
    op.drop_index("ix_group_settlements_group_id", table_name="group_settlements")
    op.drop_table("group_settlements")

    op.drop_constraint("uq_transaction_splits_tx_member", "transaction_splits", type_="unique")
    op.drop_index("ix_transaction_splits_group_member_id", table_name="transaction_splits")
    op.drop_index("ix_transaction_splits_transaction_id", table_name="transaction_splits")
    op.drop_table("transaction_splits")

    op.drop_constraint("uq_group_members_group_id_name", "group_members", type_="unique")
    op.drop_index("ix_group_members_linked_user_id", table_name="group_members")
    op.drop_index("ix_group_members_group_id", table_name="group_members")
    op.drop_table("group_members")

    op.drop_constraint("uq_groups_user_id_name", "groups", type_="unique")
    op.drop_index("ix_groups_user_id", table_name="groups")
    op.drop_table("groups")
