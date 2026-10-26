"""Transfer-pair maintenance for the agent.

Closes the transfer-matching dead end from the MCP: an agent can find
likely counterparts for a transaction, preview and link a pair, and
preview and unlink a pair, all inside the protocol. Reads reuse the
existing service layer directly; writes follow the propose pattern
(preview first, apply only on the external transport), so Securo's own
runtime never writes through MCP.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.account import Account
from app.models.transaction import Transaction
from app.services import transaction_service
from app.services.transfer_detection_service import unlink_transfer_pair
from mcp_server.auth import CallContext
from mcp_server.registry import tool
from mcp_server.tools._helpers import num, parse_uuid, parse_uuid_list, resolve_workspace_id
from mcp_server.tools.proposals import _APPLY_FIELD, _PROPOSAL_PREFACE, _can_apply


def _tx_summary(tx: Transaction, account_name: str | None) -> dict[str, Any]:
    """Small, stable dict for one transfer leg (either side of a pair)."""
    return {
        "id": str(tx.id),
        "description": tx.description,
        "date": tx.date.isoformat() if tx.date else None,
        "amount": num(tx.amount),
        "currency": tx.currency,
        "amount_primary": num(getattr(tx, "amount_primary", None)),
        "type": tx.type,
        "account_id": str(tx.account_id) if tx.account_id else None,
        "account_name": account_name,
        "category_id": str(tx.category_id) if tx.category_id else None,
        "category_name": tx.category.name if tx.category else None,
        "status": getattr(tx, "status", None),
        "is_ignored": bool(getattr(tx, "is_ignored", False)),
    }


async def _account_names(session: AsyncSession, txs: list[Transaction]) -> dict:
    """Batch-load account names for a set of transactions.

    Keeps the payloads free of lazy loads: the service fetches do not all
    selectinload the account relationship.
    """
    ids = {t.account_id for t in txs if t.account_id}
    if not ids:
        return {}
    rows = (
        await session.execute(select(Account).where(Account.id.in_(ids)))
    ).scalars().all()
    return {a.id: a.name for a in rows}


@tool(
    name="list_transfer_candidates",
    description=(
        "Read-only. Find likely transfer counterparts for one "
        "transaction: other-account rows of the opposing type, not "
        "already linked, within plus or minus window_days of the anchor "
        "date, ranked by date proximity then primary-currency amount "
        "closeness (so cross-currency pairs match well). Use it before "
        "propose_link_transfer. If the anchor is already part of a pair, "
        "returns that pair with linked=true instead of candidates."
    ),
    parameters={
        "type": "object",
        "properties": {
            "transaction_id": {
                "type": "string",
                "format": "uuid",
                "description": "The anchor transaction to find a counterpart for",
            },
            "window_days": {
                "type": "integer",
                "minimum": 1,
                "maximum": 365,
                "default": 30,
                "description": "Date window on each side of the anchor date",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 50,
                "default": 10,
                "description": "Max candidates to return",
            },
        },
        "required": ["transaction_id"],
        "additionalProperties": False,
    },
    tags=["read", "transactions"],
)
async def list_transfer_candidates(
    *,
    session: AsyncSession,
    ctx: CallContext,
    transaction_id: str,
    window_days: int = 30,
    limit: int = 10,
) -> dict[str, Any]:
    anchor_id = parse_uuid(transaction_id)
    if anchor_id is None:
        return {"error": "transaction_id must be a uuid"}
    # Hard cap regardless of what the LLM asks.
    limit = max(1, min(int(limit), 50))
    window = max(1, min(int(window_days), 365))
    ws_id = await resolve_workspace_id(session, ctx)

    anchor = await transaction_service.get_transaction(session, anchor_id, ws_id)
    if anchor is None:
        return {"error": "transaction not found in this workspace"}

    names = await _account_names(session, [anchor])
    anchor_summary = _tx_summary(anchor, names.get(anchor.account_id))

    if anchor.transfer_pair_id is not None:
        pair = await transaction_service.get_transfer_pair(session, ws_id, anchor_id)
        pair_names = await _account_names(session, [pair]) if pair else {}
        return {
            "anchor": anchor_summary,
            "linked": True,
            "transfer_pair_id": str(anchor.transfer_pair_id),
            "pair": _tx_summary(pair, pair_names.get(pair.account_id)) if pair else None,
            "candidates": [],
        }

    candidates = await transaction_service.get_transfer_candidates(
        session, ws_id, anchor_id, limit=limit, window_days=window
    )
    names = await _account_names(session, candidates)
    return {
        "anchor": anchor_summary,
        "linked": False,
        "window_days": window,
        "limit": limit,
        "candidates": [_tx_summary(c, names.get(c.account_id)) for c in candidates],
    }


@tool(
    name="propose_link_transfer",
    description=_PROPOSAL_PREFACE
    + (
        "Build a preview for linking two existing transactions as a "
        "transfer pair (one debit, one credit, in different accounts; "
        "amounts do not have to match). The preview shows both legs in "
        "full, so the user can compare dates, amounts, and accounts "
        "before confirming. Call list_transfer_candidates first unless "
        "the user names both transactions explicitly. After applying, "
        "the pair is treated as a transfer in reports and excluded "
        "from income and expense totals."
    ),
    parameters={
        "type": "object",
        "properties": {
            "transaction_ids": {
                "type": "array",
                "items": {"type": "string", "format": "uuid"},
                "minItems": 2,
                "maxItems": 2,
                "description": "Exactly two transactions: one debit and one credit in different accounts",
            },
            "apply": _APPLY_FIELD,
        },
        "required": ["transaction_ids"],
        "additionalProperties": False,
    },
    is_proposal=True,
    tags=["propose", "transactions"],
)
async def propose_link_transfer(
    *,
    session: AsyncSession,
    ctx: CallContext,
    transaction_ids: list[str],
    apply: bool = False,
) -> dict[str, Any]:
    ws_id = await resolve_workspace_id(session, ctx)
    ids = parse_uuid_list(transaction_ids) or []
    if len(ids) != 2:
        return {"error": "pass exactly two transaction ids"}
    if ids[0] == ids[1]:
        return {"error": "cannot link a transaction to itself"}

    rows = (
        (
            await session.execute(
                select(Transaction)
                .where(
                    Transaction.id.in_(ids),
                    Transaction.workspace_id == ws_id,
                )
                .options(
                    selectinload(Transaction.account),
                    selectinload(Transaction.category),
                )
            )
        )
        .scalars()
        .all()
    )
    by_id = {t.id: t for t in rows}
    txs = [by_id[i] for i in ids if i in by_id]
    if len(txs) != 2:
        return {"error": "one or both transactions not found in this workspace"}

    # Re-run the service's validation so the preview reports conflicts
    # instead of failing at apply time.
    for tx in txs:
        if tx.transfer_pair_id is not None:
            return {
                "error": "Transaction is already part of a transfer",
                "transaction_id": str(tx.id),
                "transfer_pair_id": str(tx.transfer_pair_id),
            }
    if txs[0].account_id == txs[1].account_id:
        return {"error": "transactions must be in different accounts"}
    if {tx.type for tx in txs} != {"debit", "credit"}:
        return {"error": "transactions must be one debit and one credit"}

    preview = {
        "kind": "link_transfer",
        "transaction_ids": [str(t.id) for t in txs],
        "legs": [_tx_summary(t, t.account.name if t.account else None) for t in txs],
        "apply_endpoint": "POST /api/transactions/link-transfer",
    }

    if _can_apply(ctx, apply):
        try:
            debit_tx, _ = await transaction_service.link_existing_as_transfer(
                session, ws_id, ids
            )
        except ValueError as exc:
            return {**preview, "error": str(exc)}
        return {
            **preview,
            "applied": True,
            "transfer_pair_id": str(debit_tx.transfer_pair_id),
        }

    return preview


@tool(
    name="propose_unlink_transfer",
    description=_PROPOSAL_PREFACE
    + (
        "Build a preview for unlinking an existing transfer pair (undo "
        "a previous link). Pass the transfer_pair_id returned by "
        "propose_link_transfer or list_transfer_candidates. Both legs "
        "become ordinary transactions again; amounts, dates, and every "
        "other field are left untouched."
    ),
    parameters={
        "type": "object",
        "properties": {
            "transfer_pair_id": {
                "type": "string",
                "format": "uuid",
                "description": "The shared pair id carried by both legs of a linked transfer",
            },
            "apply": _APPLY_FIELD,
        },
        "required": ["transfer_pair_id"],
        "additionalProperties": False,
    },
    is_proposal=True,
    tags=["propose", "transactions"],
)
async def propose_unlink_transfer(
    *,
    session: AsyncSession,
    ctx: CallContext,
    transfer_pair_id: str,
    apply: bool = False,
) -> dict[str, Any]:
    pair_id = parse_uuid(transfer_pair_id)
    if pair_id is None:
        return {"error": "transfer_pair_id must be a uuid"}
    ws_id = await resolve_workspace_id(session, ctx)

    legs = (
        (
            await session.execute(
                select(Transaction)
                .where(
                    Transaction.workspace_id == ws_id,
                    Transaction.transfer_pair_id == pair_id,
                )
                .options(
                    selectinload(Transaction.account),
                    selectinload(Transaction.category),
                )
            )
        )
        .scalars()
        .all()
    )
    if not legs:
        return {"error": "transfer pair not found in this workspace"}

    preview = {
        "kind": "unlink_transfer",
        "transfer_pair_id": str(pair_id),
        "legs": [_tx_summary(t, t.account.name if t.account else None) for t in legs],
        "apply_endpoint": f"DELETE /api/connections/transfers/{pair_id}",
    }

    if _can_apply(ctx, apply):
        unlinked = await unlink_transfer_pair(session, ws_id, pair_id)
        if not unlinked:
            return {**preview, "error": "transfer pair not found in this workspace"}
        await session.commit()
        return {**preview, "applied": True, "unlinked_count": unlinked}

    return preview
