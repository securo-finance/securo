"""The doubtful space: matches that are plausible without being certain.

A suggestion is what the engine produces when the signals point somewhere
but not hard enough to act. Storing them rather than recomputing them is
not an optimisation — it is the only way to remember that somebody
already said no.

## Why `declined` is the important column

Without it the loop is: sync finds a plausible pair, the person rejects
it, the next sync finds the same pair and asks again. A queue that
re-asks yesterday's questions is worse than no queue, because people stop
reading it, and then they stop reading the good ones too. The accountant
interview is unambiguous about where that ends — an incumbent whose
import-then-confirm flow was abandoned because *"eles acharam muito
trabalhoso"*.

So the rule this module exists to keep: **the queue only ever grows by
questions nobody has answered.** A declined pair is never offered again.
An accepted one is settled and gone. And a pending one that has gone
stale expires on its own, because a question nobody answered in two
months is not a question any more.

## It is the residue, never the main road

Every suggestion here is a payment the automatic tier could not claim.
If this queue is where the volume goes, the rules are wrong and the fix
is in the rules — not in asking people to work harder.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.reconciliation import ReconciliationSuggestion
from app.services.reconciliation_engine import Decision, Movement

#: How long an unanswered suggestion stays in the queue. Long enough that
#: somebody who reconciles monthly still sees it; short enough that the
#: queue does not become an archive of everything we were ever unsure
#: about.
STALE_AFTER_DAYS = 60


def _signal_scores(decision: Decision, movement: Movement) -> dict[str, object]:
    """The per-signal breakdown a person actually needs.

    One overall number would be worse than none. "We are 78% sure" is not
    something anyone can check; "the amount is exact, the date is four
    days out, the name does not match" tells them exactly where to look,
    and lets them disagree with a specific thing rather than with a
    verdict.
    """
    expectation = decision.expectation
    if expectation is None:
        return {}
    return {
        "strategy": decision.strategy,
        "description": round(decision.score, 3),
        "amount_expected": str(expectation.amount),
        "amount_moved": str(abs(movement.amount)),
        "amount_exact": abs(movement.amount) == expectation.amount,
        "days_apart": (movement.when - expectation.when).days,
        "same_counterparty": bool(
            movement.payee_id and movement.payee_id == expectation.payee_id
        ),
        "currency": expectation.currency,
    }


async def record(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    transaction_id: uuid.UUID,
    decision: Decision,
    movement: Movement,
    node: str,
) -> Optional[ReconciliationSuggestion]:
    """Keep a doubtful match, unless this pair has been settled already.

    Returns nothing when the pair is already in the queue or was already
    answered — which is the common case on a re-sync, and the whole reason
    the table exists.
    """
    if decision.port != "suggested" or decision.expectation is None:
        return None

    expectation = decision.expectation
    existing = await session.execute(
        select(ReconciliationSuggestion).where(
            ReconciliationSuggestion.workspace_id == workspace_id,
            ReconciliationSuggestion.transaction_id == transaction_id,
            ReconciliationSuggestion.expectation_kind == expectation.kind,
            ReconciliationSuggestion.expectation_id == expectation.id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        # Pending, accepted or declined — all three mean "do not ask
        # again". Declined is the one that matters: re-offering it is the
        # failure this table was built to prevent.
        return None

    suggestion = ReconciliationSuggestion(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        transaction_id=transaction_id,
        expectation_kind=expectation.kind,
        expectation_id=expectation.id,
        strategy_id=decision.strategy or "unknown",
        node=node,
        amount=decision.amount or Decimal("0"),
        scores=_signal_scores(decision, movement),
        status="pending",
    )
    session.add(suggestion)
    await session.flush()
    return suggestion


async def open_for(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    limit: int = 100,
) -> list[ReconciliationSuggestion]:
    """The questions still waiting for an answer, oldest first.

    Oldest first because a queue read newest-first grows a tail nobody
    ever reaches, and the oldest question is the one closest to expiring
    unanswered.
    """
    result = await session.execute(
        select(ReconciliationSuggestion)
        .where(
            ReconciliationSuggestion.workspace_id == workspace_id,
            ReconciliationSuggestion.status == "pending",
        )
        .order_by(ReconciliationSuggestion.created_at.asc())
        .limit(limit)
    )
    return list(result.unique().scalars().all())


async def count_open(session: AsyncSession, workspace_id: uuid.UUID) -> int:
    from sqlalchemy import func

    result = await session.execute(
        select(func.count(ReconciliationSuggestion.id)).where(
            ReconciliationSuggestion.workspace_id == workspace_id,
            ReconciliationSuggestion.status == "pending",
        )
    )
    return int(result.scalar_one() or 0)


async def get(
    session: AsyncSession, workspace_id: uuid.UUID, suggestion_id: uuid.UUID
) -> Optional[ReconciliationSuggestion]:
    result = await session.execute(
        select(ReconciliationSuggestion).where(
            ReconciliationSuggestion.id == suggestion_id,
            ReconciliationSuggestion.workspace_id == workspace_id,
        )
    )
    return result.unique().scalar_one_or_none()


def _resolve(
    suggestion: ReconciliationSuggestion, status: str, user_id: Optional[uuid.UUID]
) -> None:
    suggestion.status = status
    suggestion.resolved_at = datetime.now(timezone.utc)
    suggestion.resolved_by = user_id


async def decline(
    session: AsyncSession,
    suggestion: ReconciliationSuggestion,
    user_id: Optional[uuid.UUID],
) -> ReconciliationSuggestion:
    """Say no, once and for all.

    The row stays rather than being deleted, because the *record* of the
    refusal is the useful part: it is what stops the same pair being
    offered on every sync from here on.
    """
    _resolve(suggestion, "declined", user_id)
    await session.flush()
    return suggestion


async def mark_accepted(
    session: AsyncSession,
    suggestion: ReconciliationSuggestion,
    user_id: Optional[uuid.UUID],
) -> ReconciliationSuggestion:
    """Note that this one was taken. The link itself is the caller's job.

    Marking and linking are separate for the same reason deciding and
    writing are: an invoice allocation and a recurring upgrade are
    genuinely different writes, and this module should not know which.
    """
    _resolve(suggestion, "accepted", user_id)
    await session.flush()
    return suggestion


async def expire_stale(
    session: AsyncSession, workspace_id: uuid.UUID, *, now: Optional[datetime] = None
) -> int:
    """Retire questions nobody answered.

    Expired rather than deleted: the pair stays remembered, so a stale
    question is not re-asked the moment it is forgotten. That would be the
    original bug wearing a hat.
    """
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=STALE_AFTER_DAYS)
    result = await session.execute(
        select(ReconciliationSuggestion).where(
            ReconciliationSuggestion.workspace_id == workspace_id,
            ReconciliationSuggestion.status == "pending",
            ReconciliationSuggestion.created_at < cutoff,
        )
    )
    stale = list(result.unique().scalars().all())
    for suggestion in stale:
        suggestion.status = "expired"
        suggestion.resolved_at = datetime.now(timezone.utc)
    if stale:
        await session.flush()
    return len(stale)


async def drop_for_expectation(
    session: AsyncSession, workspace_id: uuid.UUID, expectation_id: uuid.UUID
) -> None:
    """Clear the queue of a promise that no longer exists.

    There is no foreign key to follow — an invoice and a recurring bill
    live in different tables — so a deleted promise is cleaned up here.
    Only pending rows go: an answered question stays answered, which is
    what keeps `declined` meaningful.
    """
    result = await session.execute(
        select(ReconciliationSuggestion).where(
            ReconciliationSuggestion.workspace_id == workspace_id,
            ReconciliationSuggestion.expectation_id == expectation_id,
            ReconciliationSuggestion.status == "pending",
        )
    )
    for suggestion in result.unique().scalars().all():
        await session.delete(suggestion)
