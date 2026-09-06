"""What matching did, written down and read back.

Two functions, and that is the whole module. The restraint is the design:
almost everything a person calls "the history" is already stored
elsewhere, so the temptation is to build a second copy of the ledger and
call it an audit trail. What was actually missing is narrower: a single
stream, in one order, including the one event that used to vanish.

## What gets written, and what deliberately does not

Written: a link made, a question asked, a question answered, a link
undone. Six verbs, each of which changed something a person can see.

`linked` and `accepted` are **not** both written for one act. A link the
rules made on their own is `linked`; a link a person made by accepting a
question is `accepted`, and the allocation is its consequence rather than
a second event. That is the same line the whole stream is organised
around (*was this me, or was this the rules?*), and writing both would
put two rows against one decision and blur it.

Not written: everything the engine looked at and passed over. A sync of
three hundred transactions where two hundred and ninety match nothing
would produce two hundred and ninety rows saying so, and a history nobody
can scan is the same as no history. The engine's full trace (which rule
fired, why each candidate lost) stays in memory and reaches a person
through the queue's evidence, where it is attached to a decision they
have to make rather than filed away for nobody.
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.reconciliation import ReconciliationEvent

#: The six things worth remembering.
Action = str


async def record(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    action: Action,
    *,
    expectation_kind: str,
    expectation_id: uuid.UUID,
    amount: Decimal,
    transaction_id: Optional[uuid.UUID] = None,
    strategy_id: Optional[str] = None,
    user_id: Optional[uuid.UUID] = None,
    detail: Optional[dict[str, Any]] = None,
) -> ReconciliationEvent:
    """Note that something happened.

    `user_id` left null says the system did it on its own, which is the
    distinction a reader reaches for first: was this me, or was this the
    rules?

    Never raises on its own account. A history that can fail a sync is a
    history that will one day be removed from the sync.
    """
    event = ReconciliationEvent(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        action=action,
        transaction_id=transaction_id,
        expectation_kind=expectation_kind,
        expectation_id=expectation_id,
        amount=amount,
        strategy_id=strategy_id,
        user_id=user_id,
        detail=detail or {},
    )
    session.add(event)
    return event


async def recent(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    expectation_id: Optional[uuid.UUID] = None,
    limit: int = 50,
) -> list[ReconciliationEvent]:
    """The stream, newest first.

    Newest first here and oldest first in the queue, on purpose: a queue
    is work to get through, so the oldest question is the most urgent,
    while a history is read to find out what just happened.

    `expectation_id` narrows it to one promise (everything that ever
    happened to this invoice), which is the second and only other way
    anybody reads this.
    """
    query = select(ReconciliationEvent).where(
        ReconciliationEvent.workspace_id == workspace_id
    )
    if expectation_id is not None:
        query = query.where(ReconciliationEvent.expectation_id == expectation_id)

    result = await session.execute(
        query.order_by(ReconciliationEvent.at.desc()).limit(min(limit, 200))
    )
    return list(result.scalars().all())
