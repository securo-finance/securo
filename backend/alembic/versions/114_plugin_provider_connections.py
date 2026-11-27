"""Link plugin connections to the bank connections they sync into, and keep plugin logos.

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
    # Logos a plugin ships in its package, served by Securo itself.
    op.create_table(
        "plugin_logos",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("plugin_id", sa.String(120), nullable=False),
        sa.Column("commit", sa.String(40), nullable=False),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("content_type", sa.String(20), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("plugin_id", "commit", "key", name="uq_plugin_logos_plugin_commit_key"),
    )
    op.create_index("ix_plugin_logos_sha256", "plugin_logos", ["sha256"])


def downgrade() -> None:
    op.drop_index("ix_plugin_logos_sha256", table_name="plugin_logos")
    op.drop_table("plugin_logos")
    # Narrowing fails while a plugin's bank connection, or its assets and
    # trades, hold a longer name: remove those connections before rolling back.
    with op.batch_alter_table("plugin_connections") as batch:
        batch.drop_constraint("uq_plugin_connections_bank_connection_id", type_="unique")
        batch.drop_constraint("fk_plugin_connections_bank_connection_id", type_="foreignkey")
        batch.drop_column("bank_connection_id")
    for table, column, width in _WIDENED:
        op.alter_column(table, column, existing_type=sa.String(128), type_=sa.String(width))
