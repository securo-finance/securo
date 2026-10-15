"""Catalog routes.

Gated like `/api/invoices`: the catalog exists to write invoice lines,
so a workspace without the invoices module has no catalog, and gets the
same 404 the invoice routes give it.
"""
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.invoices import _http
from app.core.database import get_async_session
from app.core.module_gate import require_module, require_module_write
from app.core.workspace_context import WorkspaceContext
from app.models.product import Product
from app.schemas.product import (
    PriceInput,
    PriceRead,
    PriceUpdate,
    ProductCreate,
    ProductRead,
    ProductUpdate,
)
from app.services import product_service as svc
from app.services.invoice_service import InvoiceError
from app.services.module_service import ModuleId

router = APIRouter(prefix="/api/products", tags=["products"])

read_ctx = require_module(ModuleId.INVOICES)
write_ctx = require_module_write(ModuleId.INVOICES)


def _serialize(product: Product, invoice_count: int = 0) -> ProductRead:
    payload = ProductRead.model_validate(product, from_attributes=True)
    payload.invoice_count = invoice_count
    return payload


async def _load(session: AsyncSession, product_id: uuid.UUID, workspace_id: uuid.UUID) -> Product:
    product = await svc.get_product(session, product_id, workspace_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    return product


async def _read(session: AsyncSession, product_id: uuid.UUID, workspace_id: uuid.UUID) -> ProductRead:
    product = await _load(session, product_id, workspace_id)
    counts = await svc.usage_counts(session, workspace_id, [product.id])
    return _serialize(product, counts.get(product.id, 0))


@router.get("", response_model=list[ProductRead])
async def list_products(
    active: Optional[bool] = Query(True, description="None for every product, archived included"),
    kind: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    ctx: WorkspaceContext = Depends(read_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    products = await svc.list_products(session, ctx.workspace.id, active=active, kind=kind, q=q)
    counts = await svc.usage_counts(session, ctx.workspace.id, [p.id for p in products])
    return [_serialize(p, counts.get(p.id, 0)) for p in products]


@router.post("", response_model=ProductRead, status_code=status.HTTP_201_CREATED)
async def create_product(
    payload: ProductCreate,
    ctx: WorkspaceContext = Depends(write_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    data = payload.model_dump(exclude_unset=True)
    data["prices"] = [dict(p) for p in data.get("prices") or []]
    try:
        product = await svc.create_product(session, ctx.workspace.id, ctx.user_id, data)
    except InvoiceError as exc:
        raise _http(exc)
    await session.commit()
    return await _read(session, product.id, ctx.workspace.id)


@router.get("/{product_id}", response_model=ProductRead)
async def get_product(
    product_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(read_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    return await _read(session, product_id, ctx.workspace.id)


@router.patch("/{product_id}", response_model=ProductRead)
async def update_product(
    product_id: uuid.UUID,
    payload: ProductUpdate,
    ctx: WorkspaceContext = Depends(write_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    product = await _load(session, product_id, ctx.workspace.id)
    try:
        await svc.update_product(session, product, payload.model_dump(exclude_unset=True))
    except InvoiceError as exc:
        raise _http(exc)
    await session.commit()
    return await _read(session, product_id, ctx.workspace.id)


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product(
    product_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(write_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    product = await _load(session, product_id, ctx.workspace.id)
    try:
        await svc.delete_product(session, product)
    except InvoiceError as exc:
        raise _http(exc)
    await session.commit()


@router.post("/{product_id}/prices", response_model=ProductRead, status_code=status.HTTP_201_CREATED)
async def add_price(
    product_id: uuid.UUID,
    payload: PriceInput,
    ctx: WorkspaceContext = Depends(write_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    product = await _load(session, product_id, ctx.workspace.id)
    try:
        await svc.add_price(session, product, payload.model_dump(exclude_unset=True))
    except InvoiceError as exc:
        raise _http(exc)
    await session.commit()
    return await _read(session, product_id, ctx.workspace.id)


@router.patch("/{product_id}/prices/{price_id}", response_model=ProductRead)
async def update_price(
    product_id: uuid.UUID,
    price_id: uuid.UUID,
    payload: PriceUpdate,
    ctx: WorkspaceContext = Depends(write_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    product = await _load(session, product_id, ctx.workspace.id)
    price = next((p for p in product.prices if p.id == price_id), None)
    if price is None:
        raise HTTPException(status_code=404, detail="Price not found")
    try:
        await svc.update_price(session, product, price, payload.model_dump(exclude_unset=True))
    except InvoiceError as exc:
        raise _http(exc)
    await session.commit()
    return await _read(session, product_id, ctx.workspace.id)


@router.delete("/{product_id}/prices/{price_id}", response_model=ProductRead)
async def delete_price(
    product_id: uuid.UUID,
    price_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(write_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    product = await _load(session, product_id, ctx.workspace.id)
    price = next((p for p in product.prices if p.id == price_id), None)
    if price is None:
        raise HTTPException(status_code=404, detail="Price not found")
    try:
        await svc.delete_price(session, product, price)
    except InvoiceError as exc:
        raise _http(exc)
    await session.commit()
    return await _read(session, product_id, ctx.workspace.id)


# `PriceRead` is exported for the OpenAPI schema of the nested list.
__all__ = ["router", "PriceRead"]
