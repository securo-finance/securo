"""cascade account-dependent tables on delete

Makes account deletion safe against the FK class of bugs (issue #110):
- import_logs.account_id            -> ON DELETE CASCADE
- recurring_transactions.account_id -> ON DELETE CASCADE
- goals.account_id                  -> ON DELETE SET NULL

Revision ID: 039
Revises: 038
Create Date: 2026-04-23
"""

from alembic import op

revision = "039"
down_revision = "038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # import_logs: cascade — logs belong to the account's lifetime
    op.drop_constraint("import_logs_account_id_fkey", "import_logs", type_="foreignkey")
    op.create_foreign_key(
        "import_logs_account_id_fkey",
        "import_logs",
        "accounts",
        ["account_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # recurring_transactions: cascade — a schedule without an account can't post
    op.drop_constraint(
        "recurring_transactions_account_id_fkey",
        "recurring_transactions",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "recurring_transactions_account_id_fkey",
        "recurring_transactions",
        "accounts",
        ["account_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # goals: set null — goals outlive any single tracked account; keep progress
    op.drop_constraint("goals_account_id_fkey", "goals", type_="foreignkey")
    op.create_foreign_key(
        "goals_account_id_fkey",
        "goals",
        "accounts",
        ["account_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("goals_account_id_fkey", "goals", type_="foreignkey")
    op.create_foreign_key(
        "goals_account_id_fkey",
        "goals",
        "accounts",
        ["account_id"],
        ["id"],
    )

    op.drop_constraint(
        "recurring_transactions_account_id_fkey",
        "recurring_transactions",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "recurring_transactions_account_id_fkey",
        "recurring_transactions",
        "accounts",
        ["account_id"],
        ["id"],
    )

    op.drop_constraint("import_logs_account_id_fkey", "import_logs", type_="foreignkey")
    op.create_foreign_key(
        "import_logs_account_id_fkey",
        "import_logs",
        "accounts",
        ["account_id"],
        ["id"],
    )
