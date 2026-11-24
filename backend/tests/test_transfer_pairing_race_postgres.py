"""Two creations racing for one counterpart leave no orphan (issue #448).

Creating a transaction runs the pairing detector inside the request's own
transaction. Two requests that each load the shared leg while it is still
free can both decide to link, and the later commit used to overwrite the
pair the earlier one made: the losing new row kept a pair id no other row
carried, dropped out of income, and showed the transfer badge with no
counterpart.

Real PostgreSQL, two real sessions, ten rounds. The sessions are released
from a barrier right before the pool is read, so both always decide on the
same free leg, which is the interleaving a sync, a double click or two API
clients produce.
"""
import asyncio
import uuid
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.app_clock import app_today
from app.models.account import Account
from app.models.reconciliation import ReconciliationSuggestion
from app.models.transaction import Transaction
from app.schemas.transaction import TransactionCreate
from app.services import transfer_detection_service
from app.services.transaction_service import create_transaction

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


async def _credit(
    factory: async_sessionmaker,
    user_id: uuid.UUID,
    workspace_id: uuid.UUID,
    account: Account,
    amount: Decimal,
    day: date,
) -> Transaction:
    async with factory() as request_session:
        return await create_transaction(
            request_session,
            workspace_id,
            user_id,
            TransactionCreate(
                account_id=account.id,
                description="Racing credit",
                amount=amount,
                date=day,
                type="credit",
            ),
        )


async def test_two_creations_racing_for_one_counterpart_never_leave_an_orphan(
    session: AsyncSession, postgres_sessions, test_user, test_workspace, monkeypatch
):
    checking = await _account(session, test_user.id, test_workspace.id, "Race A")
    savings = await _account(session, test_user.id, test_workspace.id, "Race B")
    other = await _account(session, test_user.id, test_workspace.id, "Race C")
    day = app_today()

    real_pool = transfer_detection_service._pool

    for round_number in range(10):
        # One amount per round, so a credit left over from an earlier round
        # cannot become a competing claim in this one.
        amount = Decimal("6100.11") + Decimal(round_number)
        debit = await _insert(
            session, test_user.id, checking, amount, "debit", day
        )

        # Both requests leave their row inserted and uncommitted, read the
        # pool, and only then meet here: each SELECT has taken its snapshot
        # before either request can link or commit, so both decide on the
        # same free debit without leaning on how the event loop schedules
        # them. The conditional UPDATE is where the two then contend.
        barrier = asyncio.Barrier(2)

        async def gated_pool(*args, **kwargs):
            pool = await real_pool(*args, **kwargs)
            await barrier.wait()
            return pool

        monkeypatch.setattr(transfer_detection_service, "_pool", gated_pool)

        first, second = await asyncio.wait_for(
            asyncio.gather(
                _credit(
                    postgres_sessions,
                    test_user.id,
                    test_workspace.id,
                    savings,
                    amount,
                    day,
                ),
                _credit(
                    postgres_sessions,
                    test_user.id,
                    test_workspace.id,
                    other,
                    amount,
                    day,
                ),
            ),
            timeout=30,
        )

        rows = list(
            (
                await session.execute(
                    select(Transaction)
                    .where(
                        Transaction.id.in_([debit.id, first.id, second.id])
                    )
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 3

        by_pair: dict[uuid.UUID, list[str]] = defaultdict(list)
        for row in rows:
            if row.transfer_pair_id is not None:
                by_pair[row.transfer_pair_id].append(str(row.id))

        # Exactly one pair, and it holds exactly the two legs that share it.
        assert len(by_pair) == 1, [str(row.transfer_pair_id) for row in rows]
        assert sorted(len(ids) for ids in by_pair.values()) == [2]

        loser = next(row for row in rows if row.transfer_pair_id is None)
        assert loser.id != debit.id

        # The losing credit is not silently gone: it sits in the review queue
        # as a doubtful match against the debit that was taken from it.
        suggestions = list(
            (
                await session.execute(
                    select(ReconciliationSuggestion).where(
                        ReconciliationSuggestion.transaction_id == loser.id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert [suggestion.expectation_id for suggestion in suggestions] == [debit.id]
        assert suggestions[0].status == "pending"
