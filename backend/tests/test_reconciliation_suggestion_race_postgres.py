"""Two scans asking the same question at once must both answer (issue #511).

`record` reads the queue and then inserts. Between those two statements a
second request for the same workspace can insert the same pair, and the
unique constraint on the pair used to reject the loser inside the request's
own transaction: two transfer scans overlapping gave a 500 for the scan
that arrived last, with the data still correct.

Real PostgreSQL, two real sessions. Both are held right after the read, so
they always reach the insert with the same empty answer, which is the
interleaving two clicks or a sync and a manual scan produce.
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
from app.services import reconciliation_suggestion_service as suggestions
from app.services.reconciliation_engine import (
    Decision,
    Expectation,
    Movement,
    Settlement,
)

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


async def test_two_scans_asking_the_same_question_one_answer_and_no_failure(
    session: AsyncSession, postgres_sessions, test_user, test_workspace, monkeypatch
):
    checking = await _account(session, test_user.id, test_workspace.id, "Race A")
    savings = await _account(session, test_user.id, test_workspace.id, "Race B")
    day = date(2026, 11, 10)
    amount = Decimal("6100.11")
    debit = await _insert(session, test_user.id, checking, amount, "debit", day)
    credit = await _insert(session, test_user.id, savings, amount, "credit", day)

    expectation = Expectation(
        kind="transaction",
        id=debit.id,
        amount=amount,
        currency="BRL",
        direction="debit",
        when=day,
    )
    movement = Movement(
        amount=-amount,
        currency="BRL",
        direction="credit",
        when=day,
        account_id=savings.id,
    )
    decision = Decision(
        port="suggested",
        settlements=[Settlement(expectation=expectation, amount=amount)],
        strategy="transfer_pair",
    )

    # Both sessions read the queue, then meet here before either inserts.
    # Each SELECT has taken its snapshot first, so the pair looks unasked
    # to both and the unique constraint is the only thing left to decide.
    real_execute = AsyncSession.execute
    barrier = asyncio.Barrier(2)

    async def gated_execute(self, statement, *args, **kwargs):
        result = await real_execute(self, statement, *args, **kwargs)
        if "reconciliation_suggestions" in str(statement):
            gate = self.info.pop("read_barrier", None)
            if gate is not None:
                await gate.wait()
        return result

    monkeypatch.setattr(AsyncSession, "execute", gated_execute)

    async def scan():
        async with postgres_sessions() as request_session:
            request_session.info["read_barrier"] = barrier
            written = await suggestions.record(
                request_session,
                test_workspace.id,
                credit.id,
                decision,
                movement,
                "transfer_pair",
            )
            await request_session.commit()
            return written

    first, second = await asyncio.wait_for(
        asyncio.gather(scan(), scan()), timeout=30
    )

    # Neither scan fails: one writes the question, the other finds it.
    assert sorted(len(result) for result in (first, second)) == [0, 1]

    rows = list(
        (
            await session.execute(
                select(ReconciliationSuggestion).where(
                    ReconciliationSuggestion.transaction_id == credit.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert [row.expectation_id for row in rows] == [debit.id]
    assert rows[0].status == "pending"

    events = list(
        (
            await session.execute(
                select(ReconciliationEvent).where(
                    ReconciliationEvent.expectation_id == debit.id,
                    ReconciliationEvent.action == "suggested",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(events) == 1
