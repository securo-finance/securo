import re
import uuid
from collections.abc import Collection
from datetime import timedelta
from typing import Optional

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.transaction import Transaction


def _normalize_description(value: str | None) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", (value or "").casefold()).split())


def descriptions_match(a: str | None, b: str | None) -> bool:
    """Match only normalized merchant text; never infer from shared fragments."""
    normalized = [_normalize_description(value) for value in (a, b)]
    return bool(normalized[0]) and normalized[0] == normalized[1]


async def find_unique_transaction_match(
    session: AsyncSession,
    account_id: uuid.UUID,
    txn_data,
    sources: Collection[str],
    *,
    date_tolerance_days: int = 3,
    unclaimed_only: bool = False,
    exclude_ids: Collection[uuid.UUID] = (),
    exclude_external_ids: Collection[str] = (),
) -> Optional[Transaction]:
    """Return one unambiguous imported/provider counterpart, never a guess."""
    date_lo = txn_data.date - timedelta(days=date_tolerance_days)
    date_hi = txn_data.date + timedelta(days=date_tolerance_days)
    statement = select(Transaction).where(
        Transaction.account_id == account_id,
        Transaction.source.in_(sources),
        Transaction.amount == txn_data.amount,
        Transaction.type == txn_data.type,
        Transaction.date >= date_lo,
        Transaction.date <= date_hi,
    )
    if unclaimed_only:
        statement = statement.where(Transaction.raw_data.is_(None))
    if exclude_ids:
        statement = statement.where(Transaction.id.not_in(exclude_ids))
    if exclude_external_ids:
        statement = statement.where(
            or_(
                Transaction.external_id.is_(None),
                Transaction.external_id.not_in(exclude_external_ids),
            )
        )
    result = await session.execute(statement)

    incoming_descriptions = (
        txn_data.description,
        getattr(txn_data, "payee", None),
        getattr(txn_data, "payee_raw", None),
    )
    matches = [
        candidate
        for candidate in result.scalars().all()
        if any(
            descriptions_match(candidate_description, incoming_description)
            for candidate_description in (
                candidate.description,
                candidate.original_description,
                candidate.payee,
            )
            for incoming_description in incoming_descriptions
        )
    ]
    if len(matches) <= 1:
        return matches[0] if matches else None

    incoming_exact = {
        normalized
        for value in incoming_descriptions
        if (normalized := _normalize_description(value))
    }
    exact_matches = [
        candidate
        for candidate in matches
        if incoming_exact
        & {
            normalized
            for value in (
                candidate.description,
                candidate.original_description,
                candidate.payee,
            )
            if (normalized := _normalize_description(value))
        }
    ]
    if exact_matches:
        # Indistinguishable exact candidates form a multiset. Consume one
        # deterministically; the caller's exclusions prevent reusing it.
        return min(exact_matches, key=lambda candidate: str(candidate.id))
    return None
