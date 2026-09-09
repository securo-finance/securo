"""The doubtful space: matches that are plausible without being certain.

A suggestion is what the engine produces when the signals point somewhere
but not hard enough to act. Storing them rather than recomputing them is
not an optimisation: it is the only way to remember that somebody
already said no.

## Why `declined` is the important column

Without it the loop is: sync finds a plausible pair, the person rejects
it, the next sync finds the same pair and asks again. A queue that
re-asks yesterday's questions is worse than no queue, because people stop
reading it, and then they stop reading the good ones too. The accountant
interview is unambiguous about where that ends: an incumbent whose
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
is in the rules, not in asking people to work harder.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.reconciliation import ReconciliationSuggestion
from app.services import reconciliation_history_service
from app.services.reconciliation_engine import Decision, Movement, Settlement

#: How long an unanswered suggestion stays in the queue. Long enough that
#: somebody who reconciles monthly still sees it; short enough that the
#: queue does not become an archive of everything we were ever unsure
#: about.
STALE_AFTER_DAYS = 60


def _signal_scores(
    decision: Decision, movement: Movement, settlement: "Settlement"
) -> dict[str, object]:
    """The per-signal breakdown a person actually needs.

    One overall number would be worse than none. "We are 78% sure" is not
    something anyone can check; "the amount is exact, the date is four
    days out, the name does not match" tells them exactly where to look,
    and lets them disagree with a specific thing rather than with a
    verdict.
    """
    expectation = settlement.expectation
    return {
        "strategy": decision.strategy,
        # How many promises this one payment is being offered against, so
        # the queue can say "one of three" rather than showing a number
        # that looks wrong on its own.
        "of_set": len(decision.settlements),
        "description": round(decision.score, 3),
        "amount_expected": str(expectation.amount),
        "amount_moved": str(abs(movement.amount)),
        "amount_exact": settlement.amount == expectation.amount,
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
) -> list[ReconciliationSuggestion]:
    """Keep a doubtful match, unless this pair has been settled already.

    Returns the rows written: several when one payment may be covering
    several promises, and none at all when every pair is already in the
    queue or was already answered. That last case is the common one on a
    re-sync, and the whole reason the table exists.

    A payment covering several invoices is stored as a **group**, because
    the question is the whole question. Asking about each invoice
    separately would let somebody accept two thirds of a payout and leave
    the payment short on the rest, with nothing on screen having warned
    them.
    """
    if decision.port != "suggested" or not decision.settlements:
        return []

    group_id = uuid.uuid4() if len(decision.settlements) > 1 else None
    written: list[ReconciliationSuggestion] = []

    for settlement in decision.settlements:
        expectation = settlement.expectation
        existing = await session.execute(
            select(ReconciliationSuggestion).where(
                ReconciliationSuggestion.workspace_id == workspace_id,
                ReconciliationSuggestion.transaction_id == transaction_id,
                ReconciliationSuggestion.expectation_kind == expectation.kind,
                ReconciliationSuggestion.expectation_id == expectation.id,
            )
        )
        if existing.scalar_one_or_none() is not None:
            # Pending, accepted or declined: all three mean "do not ask
            # again". Declined is the one that matters: re-offering it is
            # the failure this table was built to prevent.
            #
            # One member of a group already answered is enough to drop the
            # whole group: the question was about the combination, and a
            # combination missing a piece is a different question.
            return []

        written.append(
            ReconciliationSuggestion(
                id=uuid.uuid4(),
                workspace_id=workspace_id,
                transaction_id=transaction_id,
                group_id=group_id,
                expectation_kind=expectation.kind,
                expectation_id=expectation.id,
                strategy_id=decision.strategy or "unknown",
                node=node,
                amount=settlement.amount,
                scores=_signal_scores(decision, movement, settlement),
                status="pending",
            )
        )

    for suggestion in written:
        session.add(suggestion)

    if written:
        # **One event per question, not per invoice inside it.** A payment
        # offered against three invoices is one thing that happened and one
        # thing a person will answer; three rows in the stream would be the
        # same noise the queue collapses, in the place meant for scanning.
        head = written[0]
        await reconciliation_history_service.record(
            session,
            workspace_id,
            "suggested",
            expectation_kind=head.expectation_kind,
            expectation_id=head.expectation_id,
            amount=sum((s.amount for s in written), Decimal("0")),
            transaction_id=transaction_id,
            strategy_id=head.strategy_id,
            detail={"of_set": len(written)},
        )
        await session.flush()
    return written


async def members_of(
    session: AsyncSession, suggestion: ReconciliationSuggestion
) -> list[ReconciliationSuggestion]:
    """Every row this question is made of.

    A suggestion covering one invoice is its own group of one. A payment
    offered against three is answered whole or not at all: accepting two
    thirds of it would leave the payment short on the rest, with nothing
    on screen having said so.
    """
    if suggestion.group_id is None:
        return [suggestion]
    result = await session.execute(
        select(ReconciliationSuggestion).where(
            ReconciliationSuggestion.workspace_id == suggestion.workspace_id,
            ReconciliationSuggestion.group_id == suggestion.group_id,
        )
    )
    return list(result.unique().scalars().all())


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
    """The row, locked for the length of the transaction.

    Both callers read `status` and then write, and a plain read lets two
    requests pass that check before either commits: a double click, or a
    phone and a laptop a second apart. The pair that costs something is an
    accept and a decline, which would settle the invoice and then record
    the suggestion as dismissed, leaving the queue disagreeing with the
    ledger. The status guard closes the window; the lock closes what is
    left of it.

    `with_for_update` renders nothing on SQLite, which the test suite
    uses, the same trade the invoice numbering lock already makes: the
    guarantee is real in production, and the status check remains the
    backstop in both.

    **`of=` is not decoration.** This row eager-loads its transaction, so
    the statement is a LEFT OUTER JOIN, and Postgres refuses to lock the
    nullable side of one. A bare `FOR UPDATE` here raises
    `FeatureNotSupportedError` against Postgres and renders nothing at all
    against the SQLite the tests run on, which is the worst pair of
    behaviours available: green suite, broken endpoint. Naming the table
    locks the row we came for and leaves the join alone.
    """
    result = await session.execute(
        select(ReconciliationSuggestion)
        .where(
            ReconciliationSuggestion.id == suggestion_id,
            ReconciliationSuggestion.workspace_id == workspace_id,
        )
        .with_for_update(of=ReconciliationSuggestion)
    )
    return result.unique().scalar_one_or_none()


def _resolve(
    suggestion: ReconciliationSuggestion, status: str, user_id: Optional[uuid.UUID]
) -> None:
    """Mark one row. **Does not write history**: a grouped question is
    resolved row by row but *happened* once, so the event belongs to
    whoever knows the whole act. `answered` is that place."""
    suggestion.status = status
    suggestion.resolved_at = datetime.now(timezone.utc)
    suggestion.resolved_by = user_id


async def answered(
    session: AsyncSession,
    members: list[ReconciliationSuggestion],
    status: str,
    user_id: Optional[uuid.UUID],
) -> None:
    """Record that a question (all of it) was answered.

    One event, whether the question named one invoice or four. Splitting
    it would put several rows in the stream against a single decision, in
    the one place built for scanning.
    """
    if not members:
        return
    head = members[0]
    await reconciliation_history_service.record(
        session,
        head.workspace_id,
        status,
        expectation_kind=head.expectation_kind,
        expectation_id=head.expectation_id,
        amount=sum((m.amount for m in members), Decimal("0")),
        transaction_id=head.transaction_id,
        strategy_id=head.strategy_id,
        user_id=user_id,
        detail={"of_set": len(members)},
    )


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


def _by_question(
    rows: list[ReconciliationSuggestion],
) -> list[list[ReconciliationSuggestion]]:
    """Gather rows back into the questions they came from."""
    groups: dict[Any, list[ReconciliationSuggestion]] = {}
    for row in rows:
        groups.setdefault(row.group_id or row.id, []).append(row)
    return list(groups.values())


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
        # Nobody answered, so nobody is recorded as having.
        _resolve(suggestion, "expired", None)
    # Grouped questions expire as one event, like every other answer.
    for group in _by_question(stale):
        await answered(session, group, "expired", None)
    if stale:
        await session.flush()
    return len(stale)


async def drop_for_expectation(
    session: AsyncSession, workspace_id: uuid.UUID, expectation_id: uuid.UUID
) -> None:
    """Clear the queue of a promise that no longer exists.

    There is no foreign key to follow (an invoice and a recurring bill
    live in different tables), so a deleted promise is cleaned up here.
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
