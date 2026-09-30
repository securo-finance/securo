"""The catalog: products, prices, and what an invoice line takes from them.

Two rules hold this module together.

**A line owns its values.** `resolve_line` is the only bridge between
the catalog and an invoice: it checks that the ids a line names belong
to this workspace and to each other, and hands them back. It never
fills a price in. The client that picked the product already copied
the values into the line, and a copy the server rewrote on save would
be a line the person did not write.

**Archive beats delete once anything names the row.** A product an
invoice line points at can be archived (gone from the picker, still
readable on the invoice) but not deleted, because the line would then
say "from the catalog" about nothing. A product nothing names yet can
simply go.
"""
from __future__ import annotations

import re
import uuid
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice import InvoiceLine
from app.models.product import (
    PRICE_BILLINGS,
    PRICE_INTERVALS,
    PRODUCT_KINDS,
    Product,
    ProductPrice,
)
from app.services.invoice_service import InvoiceError

ZERO = Decimal("0.00")

_REF_KEY = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


def clean_fiscal_refs(value: Any) -> Optional[dict[str, str]]:
    """Fiscal references as stored: lowercase keys, trimmed text values,
    empty values dropped. Any key is accepted (the pack suggests, it
    never restricts); a key that is not a key is refused."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise InvoiceError("invalid_fiscal_refs", "Fiscal references must be a map of text values")
    out: dict[str, str] = {}
    for key, raw in value.items():
        k = str(key).strip().lower()
        if not _REF_KEY.match(k):
            raise InvoiceError("invalid_fiscal_refs", f"{key!r} is not a valid reference key")
        text = "" if raw is None else str(raw).strip()
        if len(text) > 100:
            raise InvoiceError("invalid_fiscal_refs", f"The value for {k} is too long")
        if text:
            out[k] = text
    return out or None


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------
async def get_product(
    session: AsyncSession, product_id: uuid.UUID, workspace_id: uuid.UUID
) -> Optional[Product]:
    result = await session.execute(
        select(Product).where(Product.id == product_id, Product.workspace_id == workspace_id)
    )
    return result.unique().scalar_one_or_none()


async def find_by_external_id(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    external_source: Optional[str],
    external_id: Optional[str],
) -> Optional[Product]:
    if not external_source or not external_id:
        return None
    result = await session.execute(
        select(Product).where(
            Product.workspace_id == workspace_id,
            Product.external_source == external_source,
            Product.external_id == external_id,
        )
    )
    return result.unique().scalar_one_or_none()


async def list_products(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    active: Optional[bool] = True,
    kind: Optional[str] = None,
    q: Optional[str] = None,
) -> list[Product]:
    """The catalog, live rows by default, by name."""
    query = select(Product).where(Product.workspace_id == workspace_id)
    if active is not None:
        query = query.where(Product.active.is_(active))
    if kind:
        query = query.where(Product.kind == kind)
    if q:
        pattern = f"%{q.lower()}%"
        query = query.where(
            or_(func.lower(Product.name).like(pattern), func.lower(Product.description).like(pattern))
        )
    query = query.order_by(func.lower(Product.name))
    result = await session.execute(query)
    return list(result.unique().scalars().all())


async def usage_counts(
    session: AsyncSession, workspace_id: uuid.UUID, product_ids: list[uuid.UUID]
) -> dict[uuid.UUID, int]:
    """How many invoices name each product. Derived, for the list."""
    if not product_ids:
        return {}
    rows = (
        await session.execute(
            select(InvoiceLine.product_id, func.count(func.distinct(InvoiceLine.invoice_id)))
            .where(
                InvoiceLine.workspace_id == workspace_id,
                InvoiceLine.product_id.in_(product_ids),
            )
            .group_by(InvoiceLine.product_id)
        )
    ).all()
    counts = {pid: 0 for pid in product_ids}
    for product_id, n in rows:
        counts[product_id] = n
    return counts


def price_for(product: Product, currency: str) -> Optional[ProductPrice]:
    """The live price in a currency, one-time first. What the picker
    fills a line with; None means "fill the name, type the price"."""
    candidates = [p for p in product.prices if p.active and p.currency == currency.upper()]
    candidates.sort(key=lambda p: (p.billing != "one_time", p.created_at))
    return candidates[0] if candidates else None


# ---------------------------------------------------------------------------
# Writing products
# ---------------------------------------------------------------------------
def _clean_name(value: Any) -> str:
    name = (value or "").strip()
    if not name:
        raise InvoiceError("name_required", "Give the product a name")
    return name


async def create_product(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: Optional[uuid.UUID],
    data: dict[str, Any],
) -> Product:
    kind = data.get("kind") or "service"
    if kind not in PRODUCT_KINDS:
        raise InvoiceError("invalid_kind", "Unknown product kind")
    origin = data.get("origin") or "local"
    if origin == "imported" and not data.get("external_source"):
        raise InvoiceError(
            "external_source_required", "An imported product must say where it came from"
        )

    product = Product(
        workspace_id=workspace_id,
        user_id=user_id,
        name=_clean_name(data.get("name")),
        description=data.get("description"),
        kind=kind,
        unit=(data.get("unit") or None),
        active=data.get("active", True),
        origin=origin,
        external_source=data.get("external_source"),
        external_id=data.get("external_id"),
        custom_fields=data.get("custom_fields"),
        fiscal_refs=clean_fiscal_refs(data.get("fiscal_refs")),
    )
    product.prices = []
    session.add(product)
    try:
        await session.flush()
    except IntegrityError:
        # The unique external pair: a re-sync converges on the row that
        # already holds this product instead of duplicating it.
        await session.rollback()
        raise InvoiceError(
            "already_imported", "This product is already here", status_code=409
        ) from None

    for raw in data.get("prices") or []:
        await add_price(session, product, raw)
    return product


async def update_product(session: AsyncSession, product: Product, data: dict[str, Any]) -> Product:
    if "name" in data:
        product.name = _clean_name(data["name"])
    if "kind" in data and data["kind"] is not None:
        if data["kind"] not in PRODUCT_KINDS:
            raise InvoiceError("invalid_kind", "Unknown product kind")
        product.kind = data["kind"]
    for field in ("description", "unit", "custom_fields"):
        if field in data:
            setattr(product, field, data[field] or None)
    if "fiscal_refs" in data:
        product.fiscal_refs = clean_fiscal_refs(data["fiscal_refs"])
    if "active" in data and data["active"] is not None:
        product.active = bool(data["active"])
    await session.flush()
    return product


async def _named_by_lines(session: AsyncSession, product: Product) -> bool:
    result = await session.execute(
        select(InvoiceLine.id).where(InvoiceLine.product_id == product.id).limit(1)
    )
    return result.scalar_one_or_none() is not None


async def delete_product(session: AsyncSession, product: Product) -> None:
    """Only a product no invoice names. Anything else is archived."""
    if await _named_by_lines(session, product):
        raise InvoiceError(
            "product_in_use", "Invoices name this product; archive it instead of deleting it"
        )
    await session.delete(product)
    await session.flush()


# ---------------------------------------------------------------------------
# Prices
# ---------------------------------------------------------------------------
def _fill_price(price: ProductPrice, data: dict[str, Any]) -> None:
    if "currency" in data and data["currency"]:
        price.currency = str(data["currency"]).upper()
    if "unit_price" in data and data["unit_price"] is not None:
        # Quantised on the way in, so the row reads the same before and
        # after a round trip through the database.
        price.unit_price = Decimal(str(data["unit_price"])).quantize(Decimal("0.01"))
        if price.unit_price < ZERO:
            raise InvoiceError("negative_price", "A price cannot be negative")
    if "tax_rate" in data:
        price.tax_rate = (
            Decimal(str(data["tax_rate"])).quantize(Decimal("0.0001"))
            if data["tax_rate"] is not None
            else None
        )
    if "billing" in data and data["billing"] is not None:
        if data["billing"] not in PRICE_BILLINGS:
            raise InvoiceError("invalid_billing", "Unknown billing kind")
        price.billing = data["billing"]
    if "interval" in data:
        price.interval = data["interval"] or None
    if "nickname" in data:
        price.nickname = (data["nickname"] or "").strip() or None
    if "lookup_key" in data:
        price.lookup_key = (data["lookup_key"] or "").strip() or None
    if "active" in data and data["active"] is not None:
        price.active = bool(data["active"])

    # Refused here, where the client can branch on a code, rather than by
    # the CHECK that would answer with a 500.
    if price.billing == "recurring":
        if price.interval not in PRICE_INTERVALS:
            raise InvoiceError("interval_required", "A recurring price needs a cadence")
    else:
        price.interval = None


async def add_price(session: AsyncSession, product: Product, data: dict[str, Any]) -> ProductPrice:
    if not data.get("currency"):
        raise InvoiceError("currency_required", "A price needs a currency")
    if data.get("unit_price") is None:
        raise InvoiceError("unit_price_required", "A price needs an amount")
    price = ProductPrice(
        product_id=product.id,
        workspace_id=product.workspace_id,
        currency=str(data["currency"]).upper(),
        unit_price=ZERO,
        billing=data.get("billing") or "one_time",
        external_source=data.get("external_source"),
        external_id=data.get("external_id"),
    )
    _fill_price(price, data)
    session.add(price)
    product.prices.append(price)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise InvoiceError(
            "price_key_taken",
            "A price with this external id or lookup key is already here",
            status_code=409,
        ) from None
    return price


async def update_price(
    session: AsyncSession, product: Product, price: ProductPrice, data: dict[str, Any]
) -> ProductPrice:
    if price.product_id != product.id:
        raise InvoiceError("price_not_found", "Price not found on this product", 404)
    _fill_price(price, data)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise InvoiceError(
            "price_key_taken", "Another price already uses this lookup key", status_code=409
        ) from None
    return price


async def find_price_by_lookup_key(
    session: AsyncSession, workspace_id: uuid.UUID, lookup_key: str
) -> Optional[ProductPrice]:
    result = await session.execute(
        select(ProductPrice).where(
            ProductPrice.workspace_id == workspace_id, ProductPrice.lookup_key == lookup_key
        )
    )
    return result.scalar_one_or_none()


async def delete_price(session: AsyncSession, product: Product, price: ProductPrice) -> None:
    """Only a price no line names. A price an invoice was billed at is
    archived, so the invoice can still say which one."""
    if price.product_id != product.id:
        raise InvoiceError("price_not_found", "Price not found on this product", 404)
    named = await session.execute(
        select(InvoiceLine.id).where(InvoiceLine.price_id == price.id).limit(1)
    )
    if named.scalar_one_or_none() is not None:
        raise InvoiceError(
            "price_in_use", "Invoices were billed at this price; archive it instead of deleting it"
        )
    product.prices.remove(price)
    await session.delete(price)
    await session.flush()


# ---------------------------------------------------------------------------
# The bridge to invoice lines
# ---------------------------------------------------------------------------
def _as_uuid(value: Any) -> Optional[uuid.UUID]:
    if value is None or value == "":
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


async def resolve_lines(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    lines: list[dict[str, Any]],
    *,
    strict: bool = True,
) -> list[dict[str, Any]]:
    """Check the catalog ids a set of lines names, in one query.

    Returns the lines with `product_id` / `price_id` as UUIDs or None.
    A product must be this workspace's; a price must be that product's.
    `strict` (a person writing an invoice) answers a bad id with an
    error. Not strict (the job emitting from a recurring agreement whose
    term was written before a product was deleted) drops the id and
    keeps the line: the line has its own values and the invoice is owed
    either way.
    """
    wanted_products = {p for p in (_as_uuid(line.get("product_id")) for line in lines) if p}
    wanted_prices = {p for p in (_as_uuid(line.get("price_id")) for line in lines) if p}

    prices: dict[uuid.UUID, ProductPrice] = {}
    if wanted_prices:
        rows = await session.execute(
            select(ProductPrice).where(
                ProductPrice.workspace_id == workspace_id, ProductPrice.id.in_(wanted_prices)
            )
        )
        prices = {p.id: p for p in rows.scalars().all()}
        # A line that only named a price still came from that product.
        wanted_products |= {p.product_id for p in prices.values()}
    products: dict[uuid.UUID, Product] = {}
    if wanted_products:
        rows = await session.execute(
            select(Product).where(
                Product.workspace_id == workspace_id, Product.id.in_(wanted_products)
            )
        )
        products = {p.id: p for p in rows.unique().scalars().all()}

    out: list[dict[str, Any]] = []
    for raw in lines:
        line = dict(raw)
        product_id = _as_uuid(line.get("product_id"))
        price_id = _as_uuid(line.get("price_id"))
        if product_id and product_id not in products:
            if strict:
                raise InvoiceError("product_not_found", "Product not found in this workspace", 404)
            product_id = None
        if price_id:
            price = prices.get(price_id)
            if price is None or (product_id and price.product_id != product_id):
                if strict:
                    raise InvoiceError("price_not_found", "Price not found on this product", 404)
                price_id = None
            elif product_id is None:
                # A price names its product; a line that only knew the
                # price still came from that product.
                product_id = price.product_id
        line["product_id"] = product_id
        line["price_id"] = price_id
        # The product's fiscal references travel with the line, copied
        # now so the document later reads what the line says. A line
        # that brought its own keeps them.
        if line.get("fiscal_refs"):
            line["fiscal_refs"] = clean_fiscal_refs(line["fiscal_refs"])
        elif product_id and product_id in products:
            line["fiscal_refs"] = dict(products[product_id].fiscal_refs or {}) or None
        else:
            line["fiscal_refs"] = None
        out.append(line)
    return out
