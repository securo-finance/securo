"""add workspaces + workspace_members + workspace_id on financial tables

Revision ID: 052
Revises: 051
Create Date: 2026-05-26

Strategy
--------
1. Create `workspaces` + `workspace_members`.
2. For every existing user, insert one Personal workspace and an `owner`
   membership row.
3. Add nullable `workspace_id` to every financial table.
4. Backfill `workspace_id` from `user_id` -> that user's Personal workspace.
   For tables without a direct `user_id` (asset_value, group_member,
   group_settlement, transaction_split, payee_mapping), join through the
   owning parent.
5. Set `workspace_id` NOT NULL + index + FK.

`user_id` columns are kept in place. They now mean "created_by" / owner
for entities where authorship is meaningful (transactions, groups). For
purely workspace-shared catalog entities (categories, payees, rules,
budgets, goals), `user_id` becomes audit-only and stops being used for
visibility filtering — the query layer pivots to `workspace_id`.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "052"
down_revision = "051"
branch_labels = None
depends_on = None


# Tables that get a direct workspace_id column backfilled from their own
# user_id. Order doesn't matter for the schema change but matters for the
# backfill — none of these depend on each other.
TABLES_WITH_USER_ID = [
    "accounts",
    "asset_groups",
    "assets",
    "bank_connections",
    "budgets",
    "categories",
    "category_groups",
    "credit_card_bills",
    "goals",
    "groups",
    "import_logs",
    "payees",
    "recurring_transactions",
    "rules",
    "transaction_attachments",
    "transactions",
]


def upgrade() -> None:
    # 1. workspaces
    op.create_table(
        "workspaces",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False, server_default="personal"),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("is_archived", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("default_currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("locale", sa.String(10), nullable=True),
        sa.Column("icon", sa.String(50), nullable=True),
        sa.Column("color", sa.String(7), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_workspaces_kind", "workspaces", ["kind"])

    # 2. workspace_members
    op.create_table(
        "workspace_members",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(20), nullable=False, server_default="owner"),
        sa.Column(
            "invited_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("workspace_id", "user_id", name="uq_workspace_member"),
    )
    op.create_index("ix_workspace_members_user", "workspace_members", ["user_id"])
    op.create_index("ix_workspace_members_workspace", "workspace_members", ["workspace_id"])

    # 3. Backfill: one Personal workspace per existing user + owner membership.
    # Localized default name based on the user's `preferences->>'language'`.
    # The use of gen_random_uuid() requires the pgcrypto extension; Postgres
    # 13+ ships it but make the extension explicit to be safe.
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute(
        """
        INSERT INTO workspaces (id, name, kind, created_by_user_id, default_currency, locale)
        SELECT
            gen_random_uuid(),
            CASE
                WHEN COALESCE(preferences->>'language', 'en') LIKE 'pt%' THEN 'Pessoal'
                ELSE 'Personal'
            END,
            'personal',
            id,
            COALESCE(preferences->>'currency_display', 'USD'),
            COALESCE(preferences->>'language', 'en')
        FROM users
        """
    )
    op.execute(
        """
        INSERT INTO workspace_members (id, workspace_id, user_id, role)
        SELECT
            gen_random_uuid(),
            w.id,
            w.created_by_user_id,
            'owner'
        FROM workspaces w
        WHERE w.created_by_user_id IS NOT NULL
        """
    )

    # 4. Add nullable workspace_id to every financial table.
    for tbl in TABLES_WITH_USER_ID:
        op.add_column(
            tbl,
            sa.Column(
                "workspace_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
                nullable=True,
            ),
        )

    # 5. Backfill workspace_id from each row's user_id (each user has
    # exactly one Personal workspace post-step-3, so the join is 1:1).
    for tbl in TABLES_WITH_USER_ID:
        op.execute(
            f"""
            UPDATE {tbl} t
            SET workspace_id = w.id
            FROM workspaces w
            WHERE w.created_by_user_id = t.user_id
              AND w.kind = 'personal'
            """
        )

    # 6. NOT NULL + index.
    for tbl in TABLES_WITH_USER_ID:
        op.alter_column(tbl, "workspace_id", nullable=False)
        op.create_index(f"ix_{tbl}_workspace_id", tbl, ["workspace_id"])

    # 7. Tables without user_id but with a parent FK — fill workspace_id
    # transitively. Useful so per-row visibility checks don't have to
    # join through the parent every time.
    # asset_values -> assets.workspace_id
    op.add_column(
        "asset_values",
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.execute(
        "UPDATE asset_values av SET workspace_id = a.workspace_id "
        "FROM assets a WHERE a.id = av.asset_id"
    )
    op.alter_column("asset_values", "workspace_id", nullable=False)
    op.create_index("ix_asset_values_workspace_id", "asset_values", ["workspace_id"])

    # group_members -> groups.workspace_id
    op.add_column(
        "group_members",
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.execute(
        "UPDATE group_members gm SET workspace_id = g.workspace_id "
        "FROM groups g WHERE g.id = gm.group_id"
    )
    op.alter_column("group_members", "workspace_id", nullable=False)
    op.create_index("ix_group_members_workspace_id", "group_members", ["workspace_id"])

    # group_settlements -> groups.workspace_id
    op.add_column(
        "group_settlements",
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.execute(
        "UPDATE group_settlements s SET workspace_id = g.workspace_id "
        "FROM groups g WHERE g.id = s.group_id"
    )
    op.alter_column("group_settlements", "workspace_id", nullable=False)
    op.create_index("ix_group_settlements_workspace_id", "group_settlements", ["workspace_id"])

    # transaction_splits -> transactions.workspace_id
    op.add_column(
        "transaction_splits",
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.execute(
        "UPDATE transaction_splits ts SET workspace_id = t.workspace_id "
        "FROM transactions t WHERE t.id = ts.transaction_id"
    )
    op.alter_column("transaction_splits", "workspace_id", nullable=False)
    op.create_index("ix_transaction_splits_workspace_id", "transaction_splits", ["workspace_id"])

    # payee_mapping -> payees.workspace_id (via target_id which references payees)
    # The mapping has its own user_id today so handle it like the rest;
    # check first that the column exists.
    bind = op.get_bind()
    has_user_id_on_mapping = bind.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name='payee_mapping' AND column_name='user_id'"
        )
    ).scalar()
    if has_user_id_on_mapping:
        op.add_column(
            "payee_mapping",
            sa.Column(
                "workspace_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
                nullable=True,
            ),
        )
        op.execute(
            """
            UPDATE payee_mapping pm
            SET workspace_id = w.id
            FROM workspaces w
            WHERE w.created_by_user_id = pm.user_id
              AND w.kind = 'personal'
            """
        )
        op.alter_column("payee_mapping", "workspace_id", nullable=False)
        op.create_index("ix_payee_mapping_workspace_id", "payee_mapping", ["workspace_id"])


def downgrade() -> None:
    # Drop workspace_id columns first (FKs require this), then the
    # workspace tables themselves.
    extra_tables = [
        "asset_values",
        "group_members",
        "group_settlements",
        "transaction_splits",
        "payee_mapping",
    ]
    bind = op.get_bind()
    for tbl in TABLES_WITH_USER_ID + extra_tables:
        has_col = bind.execute(
            sa.text(
                f"SELECT 1 FROM information_schema.columns "
                f"WHERE table_name='{tbl}' AND column_name='workspace_id'"
            )
        ).scalar()
        if not has_col:
            continue
        try:
            op.drop_index(f"ix_{tbl}_workspace_id", table_name=tbl)
        except Exception:
            pass
        op.drop_column(tbl, "workspace_id")

    op.drop_index("ix_workspace_members_workspace", table_name="workspace_members")
    op.drop_index("ix_workspace_members_user", table_name="workspace_members")
    op.drop_table("workspace_members")
    op.drop_index("ix_workspaces_kind", table_name="workspaces")
    op.drop_table("workspaces")
