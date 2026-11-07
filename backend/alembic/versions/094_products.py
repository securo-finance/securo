"""the catalog: products, their prices, and the link from an invoice line

  - `products` is what a workspace sells: a name, a kind (service or
    goods), a default unit, an active flag, and the external identity a
    gateway import converges on.
  - `product_prices` is how each one is charged: currency, amount, an
    optional tax rate, and an optional cadence. One product, many prices.
  - `invoice_lines.product_id` / `price_id` say where a line came from,
    and `fiscal_refs` is the product's fiscal references (a goods or
    service classification) as copied at that moment. Provenance and a
    copy: the line keeps its own values, so nothing about existing
    invoices changes and every new column starts null.

Revision ID: 094
Revises: 093
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "094"
down_revision = "093"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "products",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("kind", sa.String(length=20), server_default="service", nullable=False),
        sa.Column("unit", sa.String(length=20), nullable=True),
        sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("origin", sa.String(length=20), server_default="local", nullable=False),
        sa.Column("external_source", sa.String(length=50), nullable=True),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("custom_fields", sa.JSON(), nullable=True),
        sa.Column("fiscal_refs", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id", "external_source", "external_id", name="uq_products_workspace_external"
        ),
        sa.CheckConstraint("kind IN ('service', 'product')", name="ck_products_kind"),
        sa.CheckConstraint("origin IN ('local', 'imported')", name="ck_products_origin"),
    )
    op.create_index("ix_products_workspace_id", "products", ["workspace_id"])
    op.create_index("ix_products_workspace_active", "products", ["workspace_id", "active"])

    op.create_table(
        "product_prices",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("unit_price", sa.Numeric(precision=15, scale=2), nullable=False),
        sa.Column("tax_rate", sa.Numeric(precision=7, scale=4), nullable=True),
        sa.Column("billing", sa.String(length=20), server_default="one_time", nullable=False),
        sa.Column("interval", sa.String(length=20), nullable=True),
        sa.Column("nickname", sa.String(length=100), nullable=True),
        sa.Column("lookup_key", sa.String(length=200), nullable=True),
        sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("external_source", sa.String(length=50), nullable=True),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "external_source",
            "external_id",
            name="uq_product_prices_workspace_external",
        ),
        sa.UniqueConstraint("workspace_id", "lookup_key", name="uq_product_prices_lookup_key"),
        sa.CheckConstraint("unit_price >= 0", name="ck_product_prices_unit_price"),
        sa.CheckConstraint(
            "billing IN ('one_time', 'recurring')", name="ck_product_prices_billing"
        ),
        sa.CheckConstraint(
            "(billing = 'one_time' AND interval IS NULL)"
            " OR (billing = 'recurring' AND interval IN"
            " ('weekly', 'biweekly', 'monthly', 'quarterly', 'semiannual', 'yearly'))",
            name="ck_product_prices_interval",
        ),
    )
    op.create_index("ix_product_prices_product", "product_prices", ["product_id"])
    op.create_index("ix_product_prices_workspace_id", "product_prices", ["workspace_id"])

    op.add_column(
        "invoice_lines", sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column(
        "invoice_lines", sa.Column("price_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column("invoice_lines", sa.Column("fiscal_refs", sa.JSON(), nullable=True))
    op.create_foreign_key(
        "fk_invoice_lines_product_id",
        "invoice_lines",
        "products",
        ["product_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_invoice_lines_price_id",
        "invoice_lines",
        "product_prices",
        ["price_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_invoice_lines_product_id", "invoice_lines", ["product_id"])


def downgrade() -> None:
    # The lines keep every value they were given; only the memory of
    # which catalog entry they came from is lost.
    op.drop_index("ix_invoice_lines_product_id", table_name="invoice_lines")
    op.drop_constraint("fk_invoice_lines_price_id", "invoice_lines", type_="foreignkey")
    op.drop_constraint("fk_invoice_lines_product_id", "invoice_lines", type_="foreignkey")
    op.drop_column("invoice_lines", "fiscal_refs")
    op.drop_column("invoice_lines", "price_id")
    op.drop_column("invoice_lines", "product_id")

    op.drop_index("ix_product_prices_workspace_id", table_name="product_prices")
    op.drop_index("ix_product_prices_product", table_name="product_prices")
    op.drop_table("product_prices")

    op.drop_index("ix_products_workspace_active", table_name="products")
    op.drop_index("ix_products_workspace_id", table_name="products")
    op.drop_table("products")
