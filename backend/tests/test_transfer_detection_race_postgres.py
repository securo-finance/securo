"""A scan that loses the link race must not ask about the pair it lost.

Two transfer scans of the same workspace load the same pool while every
leg is still free, and both decide the same pairs are certain. The pair is
written with a conditional update, so only one scan links it and the other
falls through to the question pass. That pass used to trust the pool it
had read before the race, which made it ask about a pair that was already
linked: the queue filled with questions whose accept answers 400
`already_paired`, and nothing would take them out again.

Real PostgreSQL, two real sessions. Both are held right before writing
the pair, so each reaches the decision with a pool that predates the
other one's commit, which is the interleaving two simultaneous scans
produce.
"""
import asyncio
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.reconciliation import ReconciliationEvent, ReconciliationSuggestion
from app.models.transaction import Transaction
from app.services import transfer_detection_service as detection

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def session(postgres_sessions):
    async with postgres_sessions() as session:
        yield session
        await session.rollback()


@pytest.fixture
def clean_db(postgres_sessions):
    """Each test starts in a fresh per-test PostgreSQL schema."""


async def _account(
    session: AsyncSession, user_id: uuid.UUID, workspace_id: uuid.UUID, name: str
) -> Account:
    account = Account(
        id=uuid.uuid4(),
        user_id=user_id,
        workspace_id=workspace_id,
        name=name,
        type="checking",
        balance=Decimal("0.00"),
        currency="BRL",
    )
    session.add(account)
    await session.commit()
    await session.refresh(account)
    return account


async def _insert(
    session: AsyncSession,
    user_id: uuid.UUID,
    account: Account,
    amount: Decimal,
    kind: str,
    day: date,
) -> Transaction:
    transaction = Transaction(
        id=uuid.uuid4(),
        user_id=user_id,
        workspace_id=account.workspace_id,
        account_id=account.id,
        description=f"Racing {kind} {amount}",
        amount=amount,
        currency=account.currency,
        date=day,
        type=kind,
        source="manual",
        created_at=datetime.now(timezone.utc),
    )
    session.add(transaction)
    await session.commit()
    await session.refresh(transaction)
    return transaction


async def test_a_lost_link_race_is_not_turned_into_a_question(
    session: AsyncSession, postgres_sessions, test_user, test_workspace, monkeypatch
):
    checking = await _account(session, test_user.id, test_workspace.id, "Race A")
    savings = await _account(session, test_user.id, test_workspace.id, "Race B")
    day = date(2026, 11, 10)
    amount = Decimal("6100.11")
    debit = await _insert(session, test_user.id, checking, amount, "debit", day)
    credit = await _insert(session, test_user.id, savings, amount, "credit", day)

    # Both scans decide before either writes the pair, then meet here. The
    # conditional update inside `_link_pair` is what settles it: one gets
    # both rows, the other gets none and leaves the pair unlinked in its
    # own `paired` set.
    #
    # Each scan tries both directions, and the one that lost the first
    # attempt finds both legs taken on the second, so only the first
    # attempt per request waits here. That keeps this to the one
    # interleaving under test.
    real_link_pair = detection._link_pair
    barrier = asyncio.Barrier(2)
    waited: set[int] = set()

    async def gated_link_pair(request_session, *args, **kwargs):
        if id(request_session) not in waited:
            waited.add(id(request_session))
            await barrier.wait()
        return await real_link_pair(request_session, *args, **kwargs)

    monkeypatch.setattr(detection, "_link_pair", gated_link_pair)

    async def scan():
        async with postgres_sessions() as request_session:
            created = await detection.detect_transfer_pairs(
                request_session, test_workspace.id
            )
            await request_session.commit()
            return created

    first, second = await asyncio.wait_for(asyncio.gather(scan(), scan()), timeout=30)
    assert sorted((first, second)) == [0, 1]

    linked_rows = (
        await session.execute(
            select(Transaction.id, Transaction.transfer_pair_id).where(
                Transaction.id.in_((debit.id, credit.id))
            )
        )
    ).all()
    pairs = {row.id: row.transfer_pair_id for row in linked_rows}
    assert pairs[debit.id] is not None
    assert pairs[debit.id] == pairs[credit.id]

    # The scan that lost the race has nothing to ask: the pair is linked,
    # so both the queue and the stream stay empty, exactly as they are
    # after one scan.
    suggestions = list(
        (
            await session.execute(
                select(ReconciliationSuggestion).where(
                    ReconciliationSuggestion.workspace_id == test_workspace.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert suggestions == []

    events = list(
        (
            await session.execute(
                select(ReconciliationEvent).where(
                    ReconciliationEvent.workspace_id == test_workspace.id,
                    ReconciliationEvent.action == "suggested",
                )
            )
        )
        .scalars()
        .all()
    )
    assert events == []
