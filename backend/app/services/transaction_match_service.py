import re
import uuid
from collections.abc import Collection, Iterable
from datetime import timedelta
from typing import Optional

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import as_stored_amount
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
    candidates: Optional[Iterable[Transaction]] = None,
) -> Optional[Transaction]:
    """Return one unambiguous imported/provider counterpart, never a guess.

    `candidates`, when given, stands in for the query: every row of the
    account under `sources` within reach of `txn_data`, loaded once by a
    caller that asks about many rows of one account (an import). They are
    filtered here on the same terms the query uses, amounts compared as the
    column stores them: the query rounded the bound amount to the column's
    scale before matching (issue #616).
    """
    date_lo = txn_data.date - timedelta(days=date_tolerance_days)
    date_hi = txn_data.date + timedelta(days=date_tolerance_days)
    if candidates is not None:
        amount = as_stored_amount(txn_data.amount)
        found = [
            candidate
            for candidate in candidates
            if candidate.account_id == account_id
            and candidate.source in sources
            and as_stored_amount(candidate.amount) == amount
            and candidate.type == txn_data.type
            and date_lo <= candidate.date <= date_hi
            and not (unclaimed_only and candidate.raw_data is not None)
            and candidate.id not in exclude_ids
            and (
                not exclude_external_ids
                or candidate.external_id is None
                or candidate.external_id not in exclude_external_ids
            )
        ]
    else:
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
        found = list((await session.execute(statement)).scalars().all())

    incoming_descriptions = (
        txn_data.description,
        getattr(txn_data, "payee", None),
        getattr(txn_data, "payee_raw", None),
    )
    matches = [
        candidate
        for candidate in found
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
