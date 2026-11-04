"""let an import log describe an order import, not only a statement import

Revision ID: 073
Revises: 072
Create Date: 2026-08-21

`import_logs` was written when the only thing anyone imported was a bank
statement: every row points at an account, counts transactions and sums
credits and debits. Investment orders are imported the same way and deserve
the same record, including the delete that undoes them, but they belong to
holdings rather than to an account.

So the table grows an `entity` telling the two apart, `account_id` becomes
nullable because an order import has no account, and `asset_transactions`
gains the `import_id` that makes an undo possible at all. Existing rows are
statement imports by definition, which is what the server default records.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "073"
down_revision: Union[str, None] = "072"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "import_logs",
        sa.Column("entity", sa.String(20), nullable=False, server_default="transactions"),
    )
    op.alter_column("import_logs", "account_id", existing_type=postgresql.UUID(), nullable=True)

    op.add_column(
        "asset_transactions",
        sa.Column("import_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_asset_transactions_import_id",
        "asset_transactions",
        "import_logs",
        ["import_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_asset_transactions_import_id", "asset_transactions", ["import_id"])


def downgrade() -> None:
    op.drop_index("ix_asset_transactions_import_id", table_name="asset_transactions")
    op.drop_constraint("fk_asset_transactions_import_id", "asset_transactions", type_="foreignkey")
    op.drop_column("asset_transactions", "import_id")

    # An order import has no account, so its rows cannot satisfy the old NOT
    # NULL. They are deleted rather than pointed at an arbitrary account.
    op.execute("DELETE FROM import_logs WHERE entity = 'asset_orders'")
    op.alter_column("import_logs", "account_id", existing_type=postgresql.UUID(), nullable=False)
    op.drop_column("import_logs", "entity")
