"""MCP proposal tools for manual/growth assets (#1260)."""

from __future__ import annotations

import re
import uuid
from datetime import date as _date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.money import bounded_asset_value
from app.models.asset import Asset
from app.models.asset_group import AssetGroup
from app.models.asset_transaction import AssetTransaction
from app.models.user import User
from app.schemas.asset import AssetCreate, AssetUpdate
from app.services import asset_service
from mcp_server.auth import CallContext
from mcp_server.registry import tool
from mcp_server.tools._helpers import (
    like_literal,
    num,
    parse_date,
    parse_uuid,
    parse_uuid_list,
    resolve_workspace_id,
)
from mcp_server.tools.proposals import _APPLY_FIELD, _PROPOSAL_PREFACE, _can_apply

_ASSET_TYPES = (
    "real_estate",
    "vehicle",
    "valuable",
    "investment",
    "stock",
    "etf",
    "crypto",
    "fund",
    "other",
)
_VALUATION_METHODS = ("manual", "growth_rule")
_GROWTH_TYPES = ("percentage", "absolute")
_GROWTH_FREQUENCIES = ("daily", "weekly", "monthly", "yearly")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


def _finite_decimal(
    value: float,
    field: str,
    *,
    allow_zero: bool = True,
    allow_negative: bool = False,
) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a number")
    dec = Decimal(str(value))
    if not dec.is_finite():
        raise ValueError(f"{field} must be finite")
    if not allow_negative:
        if allow_zero and dec < 0:
            raise ValueError(f"{field} cannot be negative")
        if not allow_zero and dec <= 0:
            raise ValueError(f"{field} must be greater than zero")
    elif not allow_zero and dec == 0:
        raise ValueError(f"{field} cannot be zero")
    return dec


async def _default_currency(session: AsyncSession, user_id: uuid.UUID) -> str:
    user = await session.get(User, user_id)
    if user and user.primary_currency:
        return user.primary_currency.upper()
    return get_settings().default_currency.upper()


async def _asset_tx_count(
    session: AsyncSession, asset_id: uuid.UUID, workspace_id: uuid.UUID
) -> int:
    return int(
        await session.scalar(
            select(func.count())
            .select_from(AssetTransaction)
            .where(
                AssetTransaction.asset_id == asset_id,
                AssetTransaction.workspace_id == workspace_id,
            )
        )
        or 0
    )


def _manual_asset_refusal(asset: Asset, tx_count: int) -> str | None:
    if asset.valuation_method == "market_price" or asset.ticker is not None:
        return "market-priced assets are not supported by this tool"
    if asset.average_price is not None or tx_count > 0:
        return (
            "ledger-backed assets cannot use this tool; record transactions in the ledger instead"
        )
    if asset.connection_id is not None or asset.source != "manual":
        return "synced assets are managed by the provider connection"
    return None


async def _asset_snapshot(session: AsyncSession, asset: Asset) -> dict[str, Any]:
    read = await asset_service.get_asset(session, asset.id, asset.workspace_id)
    group_name: str | None = None
    if asset.group_id is not None:
        group = await session.get(AssetGroup, asset.group_id)
        if group is not None and group.workspace_id == asset.workspace_id:
            group_name = group.name
    return {
        "id": str(asset.id),
        "name": asset.name,
        "type": asset.type,
        "currency": asset.currency,
        "valuation_method": asset.valuation_method,
        "current_value": num(read.current_value) if read else None,
        "units": num(asset.units),
        "purchase_date": asset.purchase_date.isoformat() if asset.purchase_date else None,
        "purchase_price": num(asset.purchase_price),
        "sell_date": asset.sell_date.isoformat() if asset.sell_date else None,
        "sell_price": num(asset.sell_price),
        "growth_type": asset.growth_type,
        "growth_rate": num(asset.growth_rate),
        "growth_frequency": asset.growth_frequency,
        "growth_start_date": (
            asset.growth_start_date.isoformat() if asset.growth_start_date else None
        ),
        "group_id": str(asset.group_id) if asset.group_id else None,
        "group_name": group_name,
        "is_archived": bool(asset.is_archived),
    }


@tool(
    name="propose_create_asset",
    description=_PROPOSAL_PREFACE
    + (
        "Build a preview for creating a manual or growth-rule asset "
        "(real estate, vehicle, valuable, fixed-income/CDB, etc.). "
        "Market-priced ticker holdings are outside this tool's scope. "
        "Warns on case-insensitive name collisions without blocking."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "minLength": 1, "maxLength": 255},
            "type": {"type": "string", "enum": list(_ASSET_TYPES)},
            "currency": {"type": "string", "pattern": "^[A-Za-z]{3}$"},
            "valuation_method": {
                "type": "string",
                "enum": list(_VALUATION_METHODS),
                "default": "manual",
            },
            "current_value": {"type": "number", "minimum": 0},
            "units": {"type": "number", "exclusiveMinimum": 0},
            "purchase_date": {"type": "string", "format": "date"},
            "purchase_price": {"type": "number", "minimum": 0},
            "growth_type": {"type": "string", "enum": list(_GROWTH_TYPES)},
            "growth_rate": {"type": "number"},
            "growth_frequency": {"type": "string", "enum": list(_GROWTH_FREQUENCIES)},
            "growth_start_date": {"type": "string", "format": "date"},
            "group_id": {"type": "string", "format": "uuid"},
            "apply": _APPLY_FIELD,
        },
        "required": ["name", "type"],
        "additionalProperties": False,
    },
    is_proposal=True,
    tags=["propose", "assets"],
)
async def propose_create_asset(
    *,
    session: AsyncSession,
    ctx: CallContext,
    name: str,
    type: str,
    currency: str | None = None,
    valuation_method: str = "manual",
    current_value: float | None = None,
    units: float | None = None,
    purchase_date: str | None = None,
    purchase_price: float | None = None,
    growth_type: str | None = None,
    growth_rate: float | None = None,
    growth_frequency: str | None = None,
    growth_start_date: str | None = None,
    group_id: str | None = None,
    apply: bool = False,
) -> dict[str, Any]:
    ws_id = await resolve_workspace_id(session, ctx)
    cleaned_name = name.strip()
    if not cleaned_name:
        return {"error": "name is required"}
    if len(cleaned_name) > 255:
        return {"error": "name must be at most 255 characters"}

    norm_type = type.strip().lower()
    if norm_type not in _ASSET_TYPES:
        return {"error": f"type must be one of: {', '.join(_ASSET_TYPES)}"}

    if valuation_method == "market_price":
        return {"error": "market_price assets are not supported by propose_create_asset"}
    if valuation_method not in _VALUATION_METHODS:
        return {"error": f"valuation_method must be one of: {', '.join(_VALUATION_METHODS)}"}

    if currency is not None:
        norm_currency = currency.strip().upper()
        if not _CURRENCY_RE.fullmatch(norm_currency):
            return {"error": "currency must be a three-letter code"}
    else:
        norm_currency = await _default_currency(session, ctx.user_id)

    try:
        dec_current = (
            _finite_decimal(current_value, "current_value", allow_zero=True)
            if current_value is not None
            else None
        )
        dec_units = (
            _finite_decimal(units, "units", allow_zero=False)
            if units is not None
            else None
        )
        dec_purchase_price = (
            _finite_decimal(purchase_price, "purchase_price", allow_zero=True)
            if purchase_price is not None
            else None
        )
        dec_growth_rate = (
            _finite_decimal(growth_rate, "growth_rate", allow_zero=True, allow_negative=True)
            if growth_rate is not None
            else None
        )
    except (ValueError, ArithmeticError) as exc:
        return {"error": str(exc)}

    try:
        parsed_purchase_date = parse_date(purchase_date) if purchase_date is not None else None
        parsed_growth_start = (
            parse_date(growth_start_date) if growth_start_date is not None else None
        )
    except ValueError as exc:
        return {"error": f"invalid date: {exc}"}

    if valuation_method == "growth_rule":
        if growth_type not in _GROWTH_TYPES:
            return {"error": "growth_type ('percentage' or 'absolute') is required for growth_rule"}
        if dec_growth_rate is None:
            return {"error": "growth_rate is required for growth_rule"}
        if growth_frequency not in _GROWTH_FREQUENCIES:
            return {
                "error": (
                    "growth_frequency ('daily', 'weekly', 'monthly', 'yearly') "
                    "is required for growth_rule"
                )
            }
        if dec_purchase_price is None and dec_current is None:
            return {"error": "purchase_price or current_value is required for growth_rule"}

    group_uuid: uuid.UUID | None = None
    group_name: str | None = None
    if group_id is not None and group_id != "":
        try:
            group_uuid = parse_uuid(group_id)
        except ValueError:
            return {"error": "group not found"}
        group = (
            await session.execute(
                select(AssetGroup).where(
                    AssetGroup.id == group_uuid,
                    AssetGroup.workspace_id == ws_id,
                )
            )
        ).scalar_one_or_none()
        if group is None:
            return {"error": "group not found"}
        group_name = group.name

    collision = (
        await session.execute(
            select(Asset.id, Asset.name).where(
                Asset.workspace_id == ws_id,
                Asset.name.ilike(like_literal(cleaned_name), escape="\\"),
            )
        )
    ).first()

    proposed: dict[str, Any] = {
        "name": cleaned_name,
        "type": norm_type,
        "currency": norm_currency,
        "valuation_method": valuation_method,
    }
    if dec_current is not None:
        proposed["current_value"] = float(dec_current)
    if dec_units is not None:
        proposed["units"] = float(dec_units)
    if parsed_purchase_date is not None:
        proposed["purchase_date"] = parsed_purchase_date.isoformat()
    if dec_purchase_price is not None:
        proposed["purchase_price"] = float(dec_purchase_price)
    if growth_type is not None:
        proposed["growth_type"] = growth_type
    if dec_growth_rate is not None:
        proposed["growth_rate"] = float(dec_growth_rate)
    if growth_frequency is not None:
        proposed["growth_frequency"] = growth_frequency
    if parsed_growth_start is not None:
        proposed["growth_start_date"] = parsed_growth_start.isoformat()
    if group_uuid is not None:
        proposed["group_id"] = str(group_uuid)
        proposed["group_name"] = group_name

    preview: dict[str, Any] = {
        "kind": "create_asset",
        "proposed": proposed,
        **(
            {"name_collision": {"id": str(collision.id), "name": collision.name}}
            if collision is not None
            else {}
        ),
        "apply_endpoint": "POST /api/assets",
    }

    if _can_apply(ctx, apply):
        created = await asset_service.create_asset(
            session,
            ws_id,
            ctx.user_id,
            AssetCreate(
                name=cleaned_name,
                type=norm_type,
                currency=norm_currency,
                valuation_method=valuation_method,
                current_value=dec_current,
                units=dec_units,
                purchase_date=parsed_purchase_date,
                purchase_price=dec_purchase_price,
                growth_type=growth_type,
                growth_rate=dec_growth_rate,
                growth_frequency=growth_frequency,
                growth_start_date=parsed_growth_start,
                group_id=group_uuid,
            ),
        )
        return {**preview, "applied": True, "id": str(created.id)}

    return preview


@tool(
    name="propose_update_asset",
    description=_PROPOSAL_PREFACE
    + (
        "Build a preview for updating a manual or growth-rule asset's metadata "
        "(name, type, currency, units, purchase/sell metadata, growth rule "
        "parameters, group_id — pass '' to ungroup — or regenerate_growth). "
        "Eligibility refusals for market-priced, ticker-linked, synced, or "
        "ledger-backed holdings apply when building the proposal preview; "
        "applying via PATCH /api/assets/{asset_id} follows standard asset "
        "update semantics and always protects ledger-derived fields."
    ),
    parameters={
        "type": "object",
        "properties": {
            "asset_id": {"type": "string", "format": "uuid"},
            "name": {"type": "string", "minLength": 1, "maxLength": 255},
            "type": {"type": "string", "enum": list(_ASSET_TYPES)},
            "currency": {"type": "string", "pattern": "^[A-Za-z]{3}$"},
            "units": {"type": "number", "exclusiveMinimum": 0},
            "purchase_date": {"type": "string", "format": "date"},
            "purchase_price": {"type": "number", "minimum": 0},
            "sell_date": {"type": "string", "format": "date"},
            "sell_price": {"type": "number", "minimum": 0},
            "growth_type": {"type": "string", "enum": list(_GROWTH_TYPES)},
            "growth_rate": {"type": "number"},
            "growth_frequency": {"type": "string", "enum": list(_GROWTH_FREQUENCIES)},
            "growth_start_date": {"type": "string", "format": "date"},
            "group_id": {
                "type": "string",
                "description": "Asset group UUID, or empty string '' to ungroup",
            },
            "regenerate_growth": {"type": "boolean", "default": False},
            "apply": _APPLY_FIELD,
        },
        "required": ["asset_id"],
        "additionalProperties": False,
    },
    is_proposal=True,
    tags=["propose", "assets"],
)
async def propose_update_asset(
    *,
    session: AsyncSession,
    ctx: CallContext,
    asset_id: str,
    name: str | None = None,
    type: str | None = None,
    currency: str | None = None,
    units: float | None = None,
    purchase_date: str | None = None,
    purchase_price: float | None = None,
    sell_date: str | None = None,
    sell_price: float | None = None,
    growth_type: str | None = None,
    growth_rate: float | None = None,
    growth_frequency: str | None = None,
    growth_start_date: str | None = None,
    group_id: str | None = None,
    regenerate_growth: bool = False,
    apply: bool = False,
) -> dict[str, Any]:
    ws_id = await resolve_workspace_id(session, ctx)
    try:
        aid = parse_uuid(asset_id)
    except ValueError:
        return {"error": "asset not found"}
    asset = (
        await session.execute(
            select(Asset).where(Asset.id == aid, Asset.workspace_id == ws_id)
        )
    ).scalar_one_or_none()
    if asset is None:
        return {"error": "asset not found"}

    tx_count = await _asset_tx_count(session, asset.id, ws_id)
    refusal = _manual_asset_refusal(asset, tx_count)
    if refusal:
        return {"error": refusal}

    if regenerate_growth and asset.valuation_method != "growth_rule":
        return {"error": "regenerate_growth is only supported for growth_rule assets"}

    update_kwargs: dict[str, Any] = {}
    changes: dict[str, Any] = {}
    change_labels: dict[str, dict[str, str | None]] = {}

    if name is not None:
        cleaned = name.strip()
        if not cleaned:
            return {"error": "name cannot be empty"}
        if len(cleaned) > 255:
            return {"error": "name must be at most 255 characters"}
        if cleaned != asset.name:
            update_kwargs["name"] = cleaned
            changes["name"] = cleaned

    if type is not None:
        norm_type = type.strip().lower()
        if norm_type not in _ASSET_TYPES:
            return {"error": f"type must be one of: {', '.join(_ASSET_TYPES)}"}
        if norm_type != asset.type:
            update_kwargs["type"] = norm_type
            changes["type"] = norm_type

    if currency is not None:
        norm_curr = currency.strip().upper()
        if not _CURRENCY_RE.fullmatch(norm_curr):
            return {"error": "currency must be a three-letter code"}
        if norm_curr != asset.currency:
            update_kwargs["currency"] = norm_curr
            changes["currency"] = norm_curr

    try:
        if units is not None:
            dec_units = _finite_decimal(units, "units", allow_zero=False)
            if dec_units != asset.units:
                update_kwargs["units"] = dec_units
                changes["units"] = float(dec_units)
        if purchase_price is not None:
            dec_pp = _finite_decimal(purchase_price, "purchase_price", allow_zero=True)
            if dec_pp != asset.purchase_price:
                update_kwargs["purchase_price"] = dec_pp
                changes["purchase_price"] = float(dec_pp)
        if sell_price is not None:
            dec_sp = _finite_decimal(sell_price, "sell_price", allow_zero=True)
            if dec_sp != asset.sell_price:
                update_kwargs["sell_price"] = dec_sp
                changes["sell_price"] = float(dec_sp)
        if growth_rate is not None:
            dec_gr = _finite_decimal(
                growth_rate, "growth_rate", allow_zero=True, allow_negative=True
            )
            if dec_gr != asset.growth_rate:
                update_kwargs["growth_rate"] = dec_gr
                changes["growth_rate"] = float(dec_gr)
    except (ValueError, ArithmeticError) as exc:
        return {"error": str(exc)}

    for field_name, raw_date in (
        ("purchase_date", purchase_date),
        ("sell_date", sell_date),
        ("growth_start_date", growth_start_date),
    ):
        if raw_date is not None:
            try:
                parsed = parse_date(raw_date)
            except ValueError:
                return {"error": f"invalid {field_name} {raw_date!r}; use YYYY-MM-DD"}
            if parsed is None:
                return {"error": f"{field_name} cannot be empty"}
            if parsed != getattr(asset, field_name):
                update_kwargs[field_name] = parsed
                changes[field_name] = parsed.isoformat()

    if growth_type is not None:
        if growth_type not in _GROWTH_TYPES:
            return {"error": f"growth_type must be one of: {', '.join(_GROWTH_TYPES)}"}
        if growth_type != asset.growth_type:
            update_kwargs["growth_type"] = growth_type
            changes["growth_type"] = growth_type

    if growth_frequency is not None:
        if growth_frequency not in _GROWTH_FREQUENCIES:
            return {"error": f"growth_frequency must be one of: {', '.join(_GROWTH_FREQUENCIES)}"}
        if growth_frequency != asset.growth_frequency:
            update_kwargs["growth_frequency"] = growth_frequency
            changes["growth_frequency"] = growth_frequency

    if "purchase_date" in update_kwargs or "sell_date" in update_kwargs:
        effective_purchase = update_kwargs.get("purchase_date", asset.purchase_date)
        effective_sell = update_kwargs.get("sell_date", asset.sell_date)
        if effective_purchase is not None and effective_sell is not None and effective_purchase > effective_sell:
            return {"error": "purchase_date cannot be after sell_date"}

    if group_id is not None:
        current_group_name: str | None = None
        if asset.group_id is not None:
            cg = await session.get(AssetGroup, asset.group_id)
            if cg is not None and cg.workspace_id == ws_id:
                current_group_name = cg.name
        if group_id == "":
            if asset.group_id is not None:
                update_kwargs["group_id"] = None
                changes["group_id"] = None
                change_labels["group_id"] = {"before": current_group_name, "after": None}
        else:
            try:
                gid = parse_uuid(group_id)
            except ValueError:
                return {"error": "group not found"}
            group = (
                await session.execute(
                    select(AssetGroup).where(AssetGroup.id == gid, AssetGroup.workspace_id == ws_id)
                )
            ).scalar_one_or_none()
            if group is None:
                return {"error": "group not found"}
            if group.id != asset.group_id:
                update_kwargs["group_id"] = group.id
                changes["group_id"] = str(group.id)
                change_labels["group_id"] = {"before": current_group_name, "after": group.name}

    if not changes and not regenerate_growth:
        return {"error": "no changes provided"}

    target_snap = await _asset_snapshot(session, asset)
    preview: dict[str, Any] = {
        "kind": "update_asset",
        "target": target_snap,
        "changes": changes,
        **({"change_labels": change_labels} if change_labels else {}),
        "regenerate_growth": bool(regenerate_growth),
        "apply_endpoint": f"PATCH /api/assets/{asset.id}",
    }

    if _can_apply(ctx, apply):
        try:
            updated = await asset_service.update_asset(
                session,
                asset.id,
                ws_id,
                ctx.user_id,
                AssetUpdate(**update_kwargs),
                regenerate_growth=bool(regenerate_growth),
            )
        except ValueError as exc:
            await session.rollback()
            return {**preview, "error": str(exc)}
        if updated is None:
            return {**preview, "error": "asset not found"}
        return {**preview, "applied": True, "id": str(updated.id)}

    return preview


@tool(
    name="propose_archive_assets",
    description=_PROPOSAL_PREFACE
    + (
        "Build a preview for archiving or unarchiving one or more manual/growth "
        "assets. Refuses market-priced, synced, or ledger-backed holdings."
    ),
    parameters={
        "type": "object",
        "properties": {
            "asset_ids": {
                "type": "array",
                "items": {"type": "string", "format": "uuid"},
                "minItems": 1,
            },
            "is_archived": {"type": "boolean", "default": True},
            "apply": _APPLY_FIELD,
        },
        "required": ["asset_ids"],
        "additionalProperties": False,
    },
    is_proposal=True,
    tags=["propose", "assets"],
)
async def propose_archive_assets(
    *,
    session: AsyncSession,
    ctx: CallContext,
    asset_ids: list[str],
    is_archived: bool = True,
    apply: bool = False,
) -> dict[str, Any]:
    ws_id = await resolve_workspace_id(session, ctx)
    try:
        parsed_ids = list(dict.fromkeys(parse_uuid_list(asset_ids) or []))
    except ValueError:
        return {"error": "invalid asset_ids"}
    if not parsed_ids:
        return {"error": "asset_ids is required"}

    rows = list(
        (
            await session.execute(
                select(Asset).where(
                    Asset.id.in_(parsed_ids),
                    Asset.workspace_id == ws_id,
                )
            )
        )
        .scalars()
        .all()
    )
    by_id = {a.id: a for a in rows}
    missing_ids = [str(uid) for uid in parsed_ids if uid not in by_id]
    if not rows:
        return {"error": "no matching assets found"}

    tx_counts_result = await session.execute(
        select(AssetTransaction.asset_id, func.count())
        .where(
            AssetTransaction.asset_id.in_([a.id for a in rows]),
            AssetTransaction.workspace_id == ws_id,
        )
        .group_by(AssetTransaction.asset_id)
    )
    tx_counts = {aid: int(cnt) for aid, cnt in tx_counts_result.all()}

    for asset in rows:
        refusal = _manual_asset_refusal(asset, tx_counts.get(asset.id, 0))
        if refusal:
            return {"error": f"{asset.name}: {refusal}"}

    target_state = bool(is_archived)
    to_change = [a for a in rows if bool(a.is_archived) != target_state]
    if not to_change:
        return {"error": "all selected assets already have that archive state"}

    affected = [
        {
            "id": str(a.id),
            "name": a.name,
            "type": a.type,
            "currency": a.currency,
            "is_archived": bool(a.is_archived),
        }
        for a in to_change
    ]
    preview = {
        "kind": "archive_assets",
        "is_archived": target_state,
        "affected_count": len(affected),
        "affected": affected,
        "missing_ids": missing_ids,
        "apply_endpoint": "POST /api/assets/archive",
    }

    if _can_apply(ctx, apply):
        try:
            updated_count = await asset_service.set_assets_archived(
                session,
                ws_id,
                [a.id for a in rows],
                is_archived=target_state,
            )
        except ValueError as exc:
            await session.rollback()
            return {**preview, "error": str(exc)}
        return {**preview, "applied": True, "updated_count": updated_count}

    return preview


@tool(
    name="propose_asset_purchase_backfill",
    description=_PROPOSAL_PREFACE
    + (
        "Build a preview for backfilling purchase metadata (purchase_date, "
        "purchase_price) and/or historical valuation snapshots (values) on a "
        "manual or growth-rule asset. Refuses market-priced, synced, and "
        "ledger-backed (asset_transactions) holdings."
    ),
    parameters={
        "type": "object",
        "properties": {
            "asset_id": {"type": "string", "format": "uuid"},
            "purchase_date": {"type": "string", "format": "date"},
            "purchase_price": {"type": "number", "minimum": 0},
            "values": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "date": {"type": "string", "format": "date"},
                        "amount": {"type": "number", "minimum": 0},
                    },
                    "required": ["date", "amount"],
                    "additionalProperties": False,
                },
            },
            "apply": _APPLY_FIELD,
        },
        "required": ["asset_id"],
        "additionalProperties": False,
    },
    is_proposal=True,
    tags=["propose", "assets"],
)
async def propose_asset_purchase_backfill(
    *,
    session: AsyncSession,
    ctx: CallContext,
    asset_id: str,
    purchase_date: str | None = None,
    purchase_price: float | None = None,
    values: list[dict[str, Any]] | None = None,
    apply: bool = False,
) -> dict[str, Any]:
    ws_id = await resolve_workspace_id(session, ctx)
    try:
        aid = parse_uuid(asset_id)
    except ValueError:
        return {"error": "asset not found"}
    asset = (
        await session.execute(
            select(Asset).where(Asset.id == aid, Asset.workspace_id == ws_id)
        )
    ).scalar_one_or_none()
    if asset is None:
        return {"error": "asset not found"}

    tx_count = await _asset_tx_count(session, asset.id, ws_id)
    if asset.valuation_method == "market_price" or asset.ticker is not None:
        return {"error": "market-priced assets are not supported by purchase backfill"}
    if asset.average_price is not None or tx_count > 0:
        return {
            "error": (
                "ledger-backed assets cannot use purchase backfill; "
                "record transactions in the ledger instead"
            )
        }
    if asset.connection_id is not None or asset.source != "manual":
        return {"error": "synced assets are managed by the provider connection"}

    if purchase_date is None and purchase_price is None and not values:
        return {"error": "provide at least one of purchase_date, purchase_price, or values"}

    parsed_purchase_date: _date | None = None
    if purchase_date is not None:
        try:
            parsed_purchase_date = parse_date(purchase_date)
        except ValueError:
            return {"error": f"invalid purchase_date {purchase_date!r}; use YYYY-MM-DD"}
        if parsed_purchase_date is None:
            return {"error": "purchase_date cannot be empty"}
        if asset.sell_date is not None and parsed_purchase_date > asset.sell_date:
            return {"error": "purchase_date cannot be after sell_date"}

    dec_purchase_price: Decimal | None = None
    if purchase_price is not None:
        try:
            dec_purchase_price = _finite_decimal(
                purchase_price, "purchase_price", allow_zero=True
            )
        except (ValueError, ArithmeticError) as exc:
            return {"error": str(exc)}

    parsed_values: list[tuple[_date, Decimal]] = []
    if values is not None:
        if not values:
            return {"error": "values must not be empty"}
        seen_dates: set[_date] = set()
        for entry in values:
            if not isinstance(entry, dict):
                return {"error": "each entry in values must be an object with date and amount"}
            raw_dt = entry.get("date")
            raw_amt = entry.get("amount")
            if raw_dt is None or raw_amt is None:
                return {"error": "each value entry requires date and amount"}
            try:
                dt = parse_date(raw_dt)
            except ValueError:
                return {"error": f"invalid value date {raw_dt!r}; use YYYY-MM-DD"}
            if dt is None:
                return {"error": "value date cannot be empty"}
            if dt in seen_dates:
                return {"error": f"duplicate value date: {dt.isoformat()}"}
            seen_dates.add(dt)
            if isinstance(raw_amt, bool):
                return {"error": "amount must be a number"}
            try:
                amt = _finite_decimal(float(raw_amt), "amount", allow_zero=True)
                bounded_asset_value(amt, "amount")
            except (ValueError, TypeError, ArithmeticError) as exc:
                return {"error": str(exc)}
            parsed_values.append((dt, amt))

    changes: dict[str, Any] = {}
    if parsed_purchase_date is not None and parsed_purchase_date != asset.purchase_date:
        changes["purchase_date"] = parsed_purchase_date.isoformat()
    if dec_purchase_price is not None and dec_purchase_price != asset.purchase_price:
        changes["purchase_price"] = float(dec_purchase_price)

    if not changes and not parsed_values:
        return {"error": "no changes provided"}

    values_preview = [
        {"date": dt.isoformat(), "amount": float(amt)} for dt, amt in parsed_values
    ]
    preview = {
        "kind": "asset_purchase_backfill",
        "target": await _asset_snapshot(session, asset),
        "changes": changes,
        "values": values_preview,
        "apply_endpoint": f"POST /api/assets/{asset.id}/backfill",
    }

    if _can_apply(ctx, apply):
        try:
            res = await asset_service.backfill_asset_purchase_history(
                session,
                asset.id,
                ws_id,
                ctx.user_id,
                purchase_date=parsed_purchase_date if "purchase_date" in changes else None,
                purchase_price=dec_purchase_price if "purchase_price" in changes else None,
                values=parsed_values,
            )
        except ValueError as exc:
            await session.rollback()
            return {**preview, "error": str(exc)}
        if res is None:
            return {**preview, "error": "asset not found"}
        updated_read, created_count = res
        return {
            **preview,
            "applied": True,
            "id": str(updated_read.id),
            "values_created": created_count,
        }

    return preview
