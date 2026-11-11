from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.models.account import Account
from app.models.transaction import Transaction
from app.schemas.account import AccountCreate, AccountUpdate
from app.services import account_service
from mcp_server.auth import CallContext
from mcp_server.registry import tool
from mcp_server.tools._helpers import num, parse_date, parse_uuid, resolve_workspace_id
from mcp_server.tools.proposals import _APPLY_FIELD, _PROPOSAL_PREFACE, _can_apply


_MANUAL_ACCOUNT_TYPES = frozenset({"checking", "savings", "wallet", "investment"})
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


@tool(
    name="list_accounts",
    description=(
        "List the user's accounts (checking, savings, credit cards, wallets, etc.) "
        "with current balances. Closed accounts are excluded by default."
    ),
    parameters={
        "type": "object",
        "properties": {
            "include_closed": {"type": "boolean", "default": False},
        },
        "additionalProperties": False,
    },
    tags=["read", "accounts"],
)
async def list_accounts(
    *,
    session: AsyncSession,
    ctx: CallContext,
    include_closed: bool = False,
) -> dict[str, Any]:
    ws_id = await resolve_workspace_id(session, ctx)
    rows = await account_service.get_accounts(session, ws_id, include_closed=include_closed)
    # rows is already a list of dicts (per service contract), but normalize keys.
    items: list[dict[str, Any]] = []
    for r in rows:
        items.append({
            "id": str(r.get("id")) if r.get("id") else None,
            "name": r.get("name"),
            "type": r.get("type"),
            "currency": r.get("currency"),
            # `balance` in the serialized account is the stored column, which
            # for a manual account is only its opening balance.
            "balance": num(r.get("current_balance")),
            "current_balance": num(r.get("current_balance")),
            "opening_balance": num(r.get("balance")),
            "balance_primary": num(r.get("balance_primary")),
            "is_closed": bool(r.get("is_closed", False)),
            "institution": r.get("institution_name"),
        })
    return {"items": items, "total": len(items)}


@tool(
    name="get_account_summary",
    description=(
        "Income, expenses, and net for a single account over a date range. "
        "Defaults to the current month if no range is provided."
    ),
    parameters={
        "type": "object",
        "properties": {
            "account_id": {"type": "string", "format": "uuid"},
            "from_date": {"type": "string", "format": "date"},
            "to_date": {"type": "string", "format": "date"},
        },
        "required": ["account_id"],
        "additionalProperties": False,
    },
    tags=["read", "accounts"],
)
async def get_account_summary(
    *,
    session: AsyncSession,
    ctx: CallContext,
    account_id: str,
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict[str, Any]:
    ws_id = await resolve_workspace_id(session, ctx)
    account_uuid = parse_uuid(account_id)
    if account_uuid is None:
        return dict(error="account not found")

    summary = await account_service.get_account_summary(
        session,
        account_uuid,
        ws_id,
        date_from=parse_date(from_date),
        date_to=parse_date(to_date),
    )
    if summary is None:
        return {"error": "account not found"}
    return {
        "account_id": str(summary["account_id"]),
        "current_balance": num(summary.get("current_balance")),
        "opening_balance": num(summary.get("opening_balance")),
        "monthly_income": num(summary.get("monthly_income")),
        "monthly_expenses": num(summary.get("monthly_expenses")),
        "projected_income": num(summary.get("projected_income")),
        "projected_expenses": num(summary.get("projected_expenses")),
    }


def _normalize_account_name(name: str) -> str:
    normalized = name.strip()
    if not normalized:
        raise ValueError("name is required")
    if len(normalized) > 255:
        raise ValueError("name must be at most 255 characters")
    return normalized


def _normalize_account_type(account_type: str) -> str:
    normalized = account_type.strip().lower()
    if normalized not in _MANUAL_ACCOUNT_TYPES:
        allowed = ", ".join(sorted(_MANUAL_ACCOUNT_TYPES))
        raise ValueError(f"type must be one of: {allowed}")
    return normalized


def _normalize_currency(currency: str) -> str:
    normalized = currency.strip().upper()
    if not _CURRENCY_RE.fullmatch(normalized):
        raise ValueError("currency must be a three-letter code")
    return normalized


def _parse_opening_balance_date(value: str | None):
    if value is None or value == "":
        return None
    try:
        return parse_date(value)
    except ValueError as exc:
        raise ValueError(
            "opening_balance_date must be an ISO date (YYYY-MM-DD)"
        ) from exc


def _finite_decimal(value: float, field: str) -> Decimal:
    amount = Decimal(str(value))
    if not amount.is_finite():
        raise ValueError(f"{field} must be finite")
    return amount


async def _opening_balance_date(
    session: AsyncSession, account_id, workspace_id
):
    return await session.scalar(
        select(Transaction.date)
        .where(
            Transaction.account_id == account_id,
            Transaction.workspace_id == workspace_id,
            Transaction.source == "opening_balance",
        )
        .order_by(Transaction.date.desc())
        .limit(1)
    )


async def _account_snapshot(
    session: AsyncSession, account: Account
) -> dict[str, Any]:
    serialized = await account_service.get_account_with_balances(
        session, account.id, account.workspace_id
    )
    balance_date = serialized.get("balance_date") if serialized else None
    return {
        "id": str(account.id),
        "name": account.name,
        "type": account.type,
        "currency": account.currency,
        "current_balance": num(
            serialized.get("current_balance") if serialized else account.balance
        ),
        "opening_balance": num(account.balance),
        "opening_balance_date": balance_date.isoformat() if balance_date else None,
    }


async def _matching_manual_accounts(
    session: AsyncSession,
    *,
    workspace_id,
    name: str,
    currency: str,
) -> list[Account]:
    rows = await session.execute(
        select(Account)
        .where(
            Account.workspace_id == workspace_id,
            Account.connection_id.is_(None),
            func.lower(Account.name) == name.lower(),
            Account.currency == currency,
        )
        .order_by(Account.name)
    )
    return list(rows.scalars().all())


@tool(
    name="propose_create_manual_account",
    description=_PROPOSAL_PREFACE
    + (
        "Build a preview for creating a manual checking, savings, wallet or "
        "investment account with an opening balance. This is the correct tool "
        "for cash balances that are not connected through a bank provider. "
        "The preview warns about same-name manual accounts in the same "
        "currency but does not block a deliberate duplicate."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "minLength": 1, "maxLength": 255},
            "type": {
                "type": "string",
                "enum": sorted(_MANUAL_ACCOUNT_TYPES),
            },
            "currency": {
                "type": "string",
                "pattern": "^[A-Za-z]{3}$",
            },
            "opening_balance": {"type": "number"},
            "opening_balance_date": {"type": "string", "format": "date"},
            "apply": _APPLY_FIELD,
        },
        "required": ["name", "type", "currency", "opening_balance"],
        "additionalProperties": False,
    },
    is_proposal=True,
    tags=["propose", "accounts"],
)
async def propose_create_manual_account(
    *,
    session: AsyncSession,
    ctx: CallContext,
    name: str,
    type: str,
    currency: str,
    opening_balance: float,
    opening_balance_date: str | None = None,
    apply: bool = False,
) -> dict[str, Any]:
    try:
        normalized_name = _normalize_account_name(name)
        normalized_type = _normalize_account_type(type)
        normalized_currency = _normalize_currency(currency)
        normalized_balance = _finite_decimal(opening_balance, "opening_balance")
        normalized_date = _parse_opening_balance_date(opening_balance_date)
    except (ValueError, ArithmeticError) as exc:
        return {"error": str(exc)}
    if normalized_balance == 0 and normalized_date is not None:
        return {"error": "opening_balance_date requires a non-zero opening_balance"}
    effective_date = (
        (normalized_date or app_today())
        if normalized_balance != 0
        else None
    )

    ws_id = await resolve_workspace_id(session, ctx)
    existing = await _matching_manual_accounts(
        session,
        workspace_id=ws_id,
        name=normalized_name,
        currency=normalized_currency,
    )
    preview = {
        "kind": "create_manual_account",
        "proposed": {
            "name": normalized_name,
            "type": normalized_type,
            "currency": normalized_currency,
            "current_balance": num(normalized_balance),
            "opening_balance": num(normalized_balance),
            "opening_balance_date": (
                effective_date.isoformat() if effective_date else None
            ),
        },
        "existing_accounts": [
            await _account_snapshot(session, account) for account in existing
        ],
        **(
            {
                "name_collision": {
                    "id": str(existing[0].id),
                    "name": existing[0].name,
                }
            }
            if existing
            else {}
        ),
        "apply_endpoint": "POST /api/accounts",
    }

    if _can_apply(ctx, apply):
        created = await account_service.create_account(
            session,
            ws_id,
            ctx.user_id,
            AccountCreate(
                name=normalized_name,
                type=normalized_type,
                currency=normalized_currency,
                balance=normalized_balance,
                balance_date=effective_date,
            ),
        )
        return {**preview, "applied": True, "id": str(created.id)}

    return preview


@tool(
    name="propose_update_manual_account",
    description=_PROPOSAL_PREFACE
    + (
        "Build a preview for updating a manual account's name, type, opening "
        "balance or balance date. Call list_accounts first to find the "
        "account_id. Bank-connected accounts are refused because their "
        "balances are owned by the sync provider."
    ),
    parameters={
        "type": "object",
        "properties": {
            "account_id": {"type": "string", "format": "uuid"},
            "name": {"type": "string", "minLength": 1, "maxLength": 255},
            "type": {
                "type": "string",
                "enum": sorted(_MANUAL_ACCOUNT_TYPES),
            },
            "opening_balance": {"type": "number"},
            "opening_balance_date": {"type": "string", "format": "date"},
            "apply": _APPLY_FIELD,
        },
        "required": ["account_id"],
        "additionalProperties": False,
    },
    is_proposal=True,
    tags=["propose", "accounts"],
)
async def propose_update_manual_account(
    *,
    session: AsyncSession,
    ctx: CallContext,
    account_id: str,
    name: str | None = None,
    type: str | None = None,
    opening_balance: float | None = None,
    opening_balance_date: str | None = None,
    apply: bool = False,
) -> dict[str, Any]:
    ws_id = await resolve_workspace_id(session, ctx)
    account_uuid = parse_uuid(account_id)
    if account_uuid is None:
        return {"error": "account not found"}
    account = await account_service.get_account(session, account_uuid, ws_id)
    if account is None:
        return {"error": "account not found"}
    if account.is_closed:
        return {"error": "closed accounts cannot be edited"}
    if account.type not in _MANUAL_ACCOUNT_TYPES:
        return {
            "error": (
                f"{account.type} is outside this tool's scope; "
                "only checking, savings, wallet and investment accounts can be edited"
            )
        }
    if account.connection_id is not None:
        return {
            "error": (
                "bank-connected accounts cannot be edited through this tool; "
                "their balance is managed by the provider sync"
            )
        }

    current_date = await _opening_balance_date(session, account.id, ws_id)
    changes: dict[str, Any] = {}
    try:
        parsed_date = _parse_opening_balance_date(opening_balance_date)
        effective_balance = (
            _finite_decimal(opening_balance, "opening_balance")
            if opening_balance is not None
            else account.balance
        )
        if name is not None:
            normalized_name = _normalize_account_name(name)
            if normalized_name != account.name:
                changes["name"] = normalized_name
        if type is not None:
            normalized_type = _normalize_account_type(type)
            if normalized_type != account.type:
                changes["type"] = normalized_type
        if opening_balance is not None:
            normalized_balance = _finite_decimal(
                opening_balance, "opening_balance"
            )
            if (
                normalized_balance != account.balance
                or (current_date is None and parsed_date is not None)
            ):
                changes["opening_balance"] = num(normalized_balance)
        if parsed_date is not None and effective_balance == 0:
            return {
                "error": (
                    "opening_balance_date requires a non-zero opening_balance"
                )
            }
        if (
            parsed_date is not None
            and current_date is None
            and opening_balance is None
        ):
            return {
                "error": (
                    "opening_balance_date requires an opening balance; "
                    "provide opening_balance in the same update"
                )
            }
        if parsed_date is not None and parsed_date != current_date:
            changes["opening_balance_date"] = parsed_date.isoformat()
    except (ValueError, ArithmeticError) as exc:
        return {"error": str(exc)}

    if not changes:
        return {"error": "nothing to change"}

    prospective_name = changes.get("name", account.name)
    duplicates = [
        candidate
        for candidate in await _matching_manual_accounts(
            session,
            workspace_id=ws_id,
            name=prospective_name,
            currency=account.currency,
        )
        if candidate.id != account.id
    ]
    preview = {
        "kind": "update_manual_account",
        "target": await _account_snapshot(session, account),
        "changes": changes,
        "existing_accounts": [
            await _account_snapshot(session, candidate)
            for candidate in duplicates
        ],
        **(
            {
                "name_collision": {
                    "id": str(duplicates[0].id),
                    "name": duplicates[0].name,
                }
            }
            if duplicates
            else {}
        ),
        "apply_endpoint": f"PATCH /api/accounts/{account.id}",
    }

    if _can_apply(ctx, apply):
        update_payload: dict[str, Any] = dict(changes)
        if "opening_balance" in update_payload:
            update_payload["balance"] = Decimal(
                str(update_payload.pop("opening_balance"))
            )
        if "opening_balance_date" in update_payload:
            update_payload["balance_date"] = parse_date(
                update_payload.pop("opening_balance_date")
            )
        update = AccountUpdate.model_validate(update_payload)
        try:
            updated = await account_service.update_account(
                session, account.id, ws_id, update
            )
        except ValueError as exc:
            return {**preview, "error": str(exc)}
        if updated is None:
            return {**preview, "error": "account not found"}
        return {**preview, "applied": True, "id": str(updated.id)}

    return preview
