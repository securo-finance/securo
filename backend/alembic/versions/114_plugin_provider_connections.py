"""Link plugin connections to the bank connections they sync into.

An integration that declares `provides` gets a bank connection of its own,
with `provider = "plugin:<plugin id>"`. Plugin ids are up to 104
characters, so the columns that hold a provider name grow to 128: the
connection's, and the source of the assets, wallets and trades it syncs.

Revision ID: 114
Revises: 113
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "114"
down_revision = "113"
branch_labels = None
depends_on = None

_WIDENED = (
    ("bank_connections", "provider", 50),
    ("assets", "source", 50),
    ("asset_groups", "source", 50),
    ("asset_transactions", "source", 20),
)


def upgrade() -> None:
    for table, column, width in _WIDENED:
        op.alter_column(table, column, existing_type=sa.String(width), type_=sa.String(128))
    with op.batch_alter_table("plugin_connections") as batch:
        batch.add_column(sa.Column("bank_connection_id", UUID(as_uuid=True), nullable=True))
        batch.create_foreign_key(
            "fk_plugin_connections_bank_connection_id",
            "bank_connections",
            ["bank_connection_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_unique_constraint("uq_plugin_connections_bank_connection_id", ["bank_connection_id"])


def downgrade() -> None:
    # Narrowing fails while a plugin's bank connection, or its assets and
    # trades, hold a longer name: remove those connections before rolling back.
    with op.batch_alter_table("plugin_connections") as batch:
        batch.drop_constraint("uq_plugin_connections_bank_connection_id", type_="unique")
        batch.drop_constraint("fk_plugin_connections_bank_connection_id", type_="foreignkey")
        batch.drop_column("bank_connection_id")
    for table, column, width in _WIDENED:
        op.alter_column(table, column, existing_type=sa.String(128), type_=sa.String(width))
