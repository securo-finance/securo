"""Writing a transfer link is all-or-nothing (issue #448).

Creating a transaction runs the pairing detector inside the request's own
transaction. Two requests that reach the same decision at the same time can
both have loaded the shared leg while it was still free, so writing the pair
row by row lets the later commit overwrite the pair the earlier one made and
leaves the losing new row pointing at an id no other row carries. That lone
pair id takes the row out of income with no counterpart to show for it.

The guard tested here is the conditional write: the link only lands when
both rows still say "free", and a savepoint keeps the half that matched from
surviving on its own. The genuinely concurrent path over two sessions is
covered in ``test_transfer_pairing_race_postgres.py``.
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.models.account import Account
from app.models.reconciliation import ReconciliationSuggestion
from app.models.transaction import Transaction
from app.services import transfer_detection_service
from app.services.transfer_detection_service import _link_pair


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
    amount: str,
    kind: str,
    day: date,
) -> Transaction:
    transaction = Transaction(
        id=uuid.uuid4(),
        user_id=user_id,
        workspace_id=account.workspace_id,
        account_id=account.id,
        description=f"Old {kind} {amount}",
        amount=Decimal(amount),
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


async def _pair_ids(session: AsyncSession) -> dict[str, uuid.UUID | None]:
    result = await session.execute(
        select(Transaction.id, Transaction.transfer_pair_id)
    )
    return {str(row[0]): row[1] for row in result}


@pytest.mark.asyncio
async def test_a_claim_on_a_leg_already_taken_writes_nothing(
    session: AsyncSession, test_user, test_workspace
):
    """The second claim loses whole: neither row it tried to write keeps the id.

    This is the state the losing request holds: it decided while the debit
    still looked free, and the winner committed before its own write landed.
    The pair must not be written to the row that was still free.
    """
    checking = await _account(session, test_user.id, test_workspace.id, "Race A")
    savings = await _account(session, test_user.id, test_workspace.id, "Race B")
    other = await _account(session, test_user.id, test_workspace.id, "Race C")
    day = app_today()

    debit = await _insert(session, test_user.id, checking, "100.00", "debit", day)
    winner = await _insert(session, test_user.id, savings, "100.00", "credit", day)
    loser = await _insert(session, test_user.id, other, "100.00", "credit", day)

    pair_id = await _link_pair(session, test_workspace.id, debit, winner)
    assert pair_id is not None
    await session.commit()

    assert await _link_pair(session, test_workspace.id, debit, loser) is None

    pairs = await _pair_ids(session)
    assert pairs[str(debit.id)] == pair_id
    assert pairs[str(winner.id)] == pair_id
    assert pairs[str(loser.id)] is None


@pytest.mark.asyncio
async def test_a_leg_taken_from_the_other_side_also_writes_nothing(
    session: AsyncSession, test_user, test_workspace
):
    """The guard does not depend on which leg the losing request loaded first."""
    checking = await _account(session, test_user.id, test_workspace.id, "Race D")
    savings = await _account(session, test_user.id, test_workspace.id, "Race E")
    other = await _account(session, test_user.id, test_workspace.id, "Race F")
    day = app_today()

    debit = await _insert(session, test_user.id, checking, "200.00", "debit", day)
    winner = await _insert(session, test_user.id, savings, "200.00", "credit", day)
    loser = await _insert(session, test_user.id, other, "200.00", "debit", day)

    assert await _link_pair(session, test_workspace.id, debit, winner) is not None
    await session.commit()

    assert await _link_pair(session, test_workspace.id, loser, winner) is None

    pairs = await _pair_ids(session)
    assert pairs[str(loser.id)] is None
    assert pairs[str(winner.id)] is not None
    assert pairs[str(winner.id)] == pairs[str(debit.id)]


@pytest.mark.asyncio
async def test_a_pair_that_cannot_be_written_becomes_a_suggestion(
    client, auth_headers, session: AsyncSession, test_user, test_workspace, monkeypatch
):
    """A lost claim is not thrown away: the row goes to the review queue.

    The debit is free when detection decides, but the write cannot land
    (another request took it). Pass two must record the doubtful match
    instead of leaving the new credit looking like plain income with no
    trail.
    """
    checking = await _account(session, test_user.id, test_workspace.id, "Queue A")
    savings = await _account(session, test_user.id, test_workspace.id, "Queue B")
    day = app_today()

    debit = await _insert(session, test_user.id, checking, "300.00", "debit", day)

    async def _taken(*_args, **_kwargs):
        return None

    monkeypatch.setattr(transfer_detection_service, "_link_pair", _taken)

    response = await client.post(
        "/api/transactions",
        headers=auth_headers,
        json={
            "account_id": str(savings.id),
            "description": "Losing credit",
            "amount": "300.00",
            "date": day.isoformat(),
            "type": "credit",
        },
    )
    assert response.status_code == 201, response.text
    credit_id = uuid.UUID(response.json()["id"])

    suggestions = (
        await session.execute(
            select(ReconciliationSuggestion).where(
                ReconciliationSuggestion.transaction_id == credit_id
            )
        )
    ).scalars().all()
    assert [suggestion.expectation_id for suggestion in suggestions] == [debit.id]
    assert suggestions[0].status == "pending"
