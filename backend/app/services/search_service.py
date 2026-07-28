"""Global search across the user's financial entities.

Implements lightweight case-insensitive ILIKE matching across transactions,
accounts, payees, categories, goals and assets. Designed to power the
command palette (Cmd/Ctrl+K) on the frontend. Kept intentionally simple —
no full-text indexes required — but fast enough for tens of thousands of
rows thanks to per-entity LIMITs and trigger-word scoping.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal, Optional

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.asset import Asset
from app.models.category import Category
from app.models.goal import Goal
from app.models.payee import Payee
from app.models.transaction import Transaction
from app.services._query_filters import account_history_is_visible


EntityType = Literal[
    "transaction",
    "account",
    "payee",
    "category",
    "goal",
    "asset",
]


@dataclass
class SearchHit:
    type: EntityType
    id: str
    label: str
    subtitle: Optional[str] = None
    amount: Optional[Decimal] = None
    currency: Optional[str] = None
    date: Optional[str] = None
    icon: Optional[str] = None
    color: Optional[str] = None
    meta: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "id": self.id,
            "label": self.label,
            "subtitle": self.subtitle,
            "amount": float(self.amount) if self.amount is not None else None,
            "currency": self.currency,
            "date": self.date,
            "icon": self.icon,
            "color": self.color,
            "meta": self.meta or {},
        }


def _like(term: str) -> str:
    # Escape SQL LIKE wildcards the user typed, then wrap.
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


async def search_all(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    query: str,
    per_type_limit: int = 5,
) -> list[dict[str, Any]]:
    """Search across all entity types in the current workspace.

    Returns a flat list of hits ordered by entity type, newest first within
    each group. Matching is case-insensitive ILIKE on the most useful string
    columns per model.

    Transaction search additionally pulls in cross-workspace rows the
    caller participates in via group splits (the Splitwise projection
    — "concert" finds the parent that someone else paid for but is on
    the caller's settlement ledger).
    """
    term = (query or "").strip()
    if len(term) < 1:
        return []

    pattern = _like(term)
    hits: list[SearchHit] = []

    # -- Transactions -------------------------------------------------------
    from app.models.group import GroupMember
    from app.models.transaction_split import TransactionSplit

    viewer_member_ids = select(GroupMember.id).where(
        GroupMember.linked_user_id == user_id,
        GroupMember.is_self.is_(False),
    )
    shared_tx_ids = (
        select(TransactionSplit.transaction_id)
        .where(TransactionSplit.group_member_id.in_(viewer_member_ids))
        .distinct()
    )
    tx_result = await session.execute(
        select(Transaction)
        .join(Account, Transaction.account_id == Account.id)
        .where(
            or_(
                Transaction.workspace_id == workspace_id,
                Transaction.id.in_(shared_tx_ids),
            ),
            account_history_is_visible(),
            or_(
                Transaction.description.ilike(pattern, escape="\\"),
                Transaction.payee.ilike(pattern, escape="\\"),
                Transaction.notes.ilike(pattern, escape="\\"),
            ),
        )
        .order_by(Transaction.date.desc(), Transaction.created_at.desc())
        .limit(per_type_limit)
    )
    for tx in tx_result.scalars().all():
        hits.append(
            SearchHit(
                type="transaction",
                id=str(tx.id),
                label=tx.description,
                subtitle=tx.payee,
                amount=tx.amount,
                currency=tx.currency,
                date=tx.date.isoformat() if tx.date else None,
                meta={
                    "account_id": str(tx.account_id),
                    "category_id": str(tx.category_id) if tx.category_id else None,
                    "tx_type": tx.type,
                },
            )
        )

    # -- Accounts -----------------------------------------------------------
    acc_result = await session.execute(
        select(Account)
        .where(
            Account.workspace_id == workspace_id,
            Account.name.ilike(pattern, escape="\\"),
        )
        .order_by(Account.is_closed.asc(), Account.name.asc())
        .limit(per_type_limit)
    )
    for acc in acc_result.scalars().all():
        hits.append(
            SearchHit(
                type="account",
                id=str(acc.id),
                label=acc.name,
                subtitle=acc.type,
                amount=acc.balance,
                currency=acc.currency,
                meta={"is_closed": acc.is_closed},
            )
        )

    # -- Payees -------------------------------------------------------------
    payee_result = await session.execute(
        select(Payee)
        .where(
            Payee.workspace_id == workspace_id,
            or_(
                Payee.name.ilike(pattern, escape="\\"),
                Payee.notes.ilike(pattern, escape="\\"),
            ),
        )
        .order_by(Payee.is_favorite.desc(), Payee.name.asc())
        .limit(per_type_limit)
    )
    for payee in payee_result.scalars().all():
        hits.append(
            SearchHit(
                type="payee",
                id=str(payee.id),
                label=payee.name,
                subtitle=payee.type,
                meta={"is_favorite": payee.is_favorite},
            )
        )

    # -- Categories ---------------------------------------------------------
    cat_result = await session.execute(
        select(Category)
        .where(
            Category.workspace_id == workspace_id,
            Category.name.ilike(pattern, escape="\\"),
        )
        .order_by(Category.name.asc())
        .limit(per_type_limit)
    )
    for cat in cat_result.scalars().all():
        hits.append(
            SearchHit(
                type="category",
                id=str(cat.id),
                label=cat.name,
                icon=cat.icon,
                color=cat.color,
            )
        )

    # -- Goals --------------------------------------------------------------
    goal_result = await session.execute(
        select(Goal)
        .where(
            Goal.workspace_id == workspace_id,
            Goal.name.ilike(pattern, escape="\\"),
        )
        .order_by(Goal.position.asc(), Goal.name.asc())
        .limit(per_type_limit)
    )
    for goal in goal_result.scalars().all():
        hits.append(
            SearchHit(
                type="goal",
                id=str(goal.id),
                label=goal.name,
                subtitle=goal.status,
                amount=goal.target_amount,
                currency=goal.currency,
                icon=goal.icon,
                color=goal.color,
            )
        )

    # -- Assets -------------------------------------------------------------
    asset_result = await session.execute(
        select(Asset)
        .where(
            Asset.workspace_id == workspace_id,
            Asset.name.ilike(pattern, escape="\\"),
        )
        .order_by(Asset.is_archived.asc(), Asset.position.asc(), Asset.name.asc())
        .limit(per_type_limit)
    )
    for asset in asset_result.scalars().all():
        hits.append(
            SearchHit(
                type="asset",
                id=str(asset.id),
                label=asset.name,
                subtitle=asset.type,
                currency=asset.currency,
                meta={"is_archived": asset.is_archived},
            )
        )

    return [hit.to_dict() for hit in hits]
