"""The catalog: what a workspace sells, and for how much.

Two tables, shaped after the split that has served billing systems for a
decade: a **product** is the thing (a name, what kind of thing it is,
what its quantity counts), and a **price** is one way of charging for
it (a currency, an amount, optionally a cadence). One product carries as
many prices as it has currencies or plans.

The rule that keeps the catalog optional: **an invoice line never
depends on the catalog**. Picking a product fills the line's own
fields and remembers which product it was; the line then stands on its
own copy. Renaming a product in October does not touch September's
invoice, archiving it does not blank a line, and a workspace that never
creates a product writes lines exactly as before. `product_id` on a
line is provenance and grouping, the same way `schedule_id` is.

Deliberately not here: stock, SKUs, fiscal classification per product,
tiered or metered pricing. Those are where an ERP begins.
"""
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    pass

#: What kind of thing is sold. Free text would do for the document, but
#: the fiscal layer will need to know (a service note and a goods note
#: are different documents in more than one jurisdiction), and a column
#: added later is a migration over every row in every install.
PRODUCT_KINDS = ("service", "product")

#: How a price charges. `one_time` is a unit price; `recurring` is the
#: same unit price with a cadence, which the recurring-invoice form and
#: a gateway import both read as a hint.
PRICE_BILLINGS = ("one_time", "recurring")

#: Same vocabulary as recurring invoices, so a recurring price can seed
#: an agreement without translation.
PRICE_INTERVALS = ("weekly", "biweekly", "monthly", "quarterly", "semiannual", "yearly")

#: Who authored the row. `imported` mirrors a payment gateway's catalog:
#: that system owns it, and a re-sync converges on the same row through
#: the external id.
PRODUCT_ORIGINS = ("local", "imported")


class Product(Base):
    """One thing this workspace sells."""

    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "external_source", "external_id", name="uq_products_workspace_external"
        ),
        # Every list is "this workspace's live products", so the two
        # columns index together.
        Index("ix_products_workspace_active", "workspace_id", "active"),
        CheckConstraint("kind IN ('service', 'product')", name="ck_products_kind"),
        CheckConstraint("origin IN ('local', 'imported')", name="ck_products_origin"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    # Who created it, not who owns it: the catalog belongs to the
    # workspace, so a member leaving takes nothing with them.
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    kind: Mapped[str] = mapped_column(String(20), default="service", server_default="service")
    # The default unit a line for this product counts in: hours, words,
    # pieces. Copied onto the line, where it stays editable.
    unit: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    # Archived rather than deleted once an invoice names it: the line
    # keeps its copy either way, but a product that disappears from the
    # picker while its invoices still say "that one" is confusing, and
    # a product that keeps being offered after it stopped being sold is
    # worse.
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    origin: Mapped[str] = mapped_column(String(20), default="local", server_default="local")
    external_source: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    external_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    custom_fields: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    # What a fiscal document will need per line: a goods classification,
    # a service code, a barcode. Keys are suggested by the jurisdiction
    # pack (`ncm`, `hs_code`, `service_code`...), values are text, and
    # any key is storable because a pack suggests and never restricts.
    # Copied onto the invoice line when the product fills it, so the
    # document is drawn from what the line says, not from what the
    # product says today.
    fiscal_refs: Mapped[Optional[dict[str, str]]] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    prices: Mapped[list["ProductPrice"]] = relationship(
        back_populates="product",
        cascade="all, delete-orphan",
        order_by="ProductPrice.created_at",
        lazy="selectin",
    )


class ProductPrice(Base):
    """One way of charging for a product: a currency and an amount."""

    __tablename__ = "product_prices"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "external_source",
            "external_id",
            name="uq_product_prices_workspace_external",
        ),
        # A name of the workspace's own choosing (`pro_monthly`), unique
        # among its prices, so an import or a script can find a price
        # without knowing its id.
        UniqueConstraint("workspace_id", "lookup_key", name="uq_product_prices_lookup_key"),
        Index("ix_product_prices_product", "product_id"),
        CheckConstraint("unit_price >= 0", name="ck_product_prices_unit_price"),
        CheckConstraint(
            "billing IN ('one_time', 'recurring')", name="ck_product_prices_billing"
        ),
        # A cadence belongs to a recurring price and to nothing else.
        CheckConstraint(
            "(billing = 'one_time' AND interval IS NULL)"
            " OR (billing = 'recurring' AND interval IN"
            " ('weekly', 'biweekly', 'monthly', 'quarterly', 'semiannual', 'yearly'))",
            name="ck_product_prices_interval",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )

    currency: Mapped[str] = mapped_column(String(3))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(precision=15, scale=2))
    # A rate, nullable: most workspaces never fill it in.
    tax_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(precision=7, scale=4), nullable=True)
    billing: Mapped[str] = mapped_column(String(20), default="one_time", server_default="one_time")
    interval: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    # "Monthly", "Annual, 2 months free": a name for the picker when a
    # product has more than one price in a currency.
    nickname: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    lookup_key: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    external_source: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    external_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    product: Mapped["Product"] = relationship(back_populates="prices")
