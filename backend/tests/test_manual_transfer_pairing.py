"""Pairing a transfer whose leg was typed in by hand (issue #1798).

The detector has always run at the end of a sync, over the rows that just
arrived. A row added with the "+ Add" form never went through that path, so
a transfer recorded by hand sat unpaired forever and the review queue never
saw it. Creating a transaction now runs the same detection with the new row
as the only candidate, which keeps the rule that a pair may only form when
at least one leg is new.
"""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.models.account import Account
from app.models.transaction import Transaction


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


async def _post(
    client, auth_headers, account: Account, amount: str, kind: str, day: date
) -> dict:
    response = await client.post(
        "/api/transactions",
        headers=auth_headers,
        json={
            "account_id": str(account.id),
            "description": f"Manual {kind} {amount}",
            "amount": amount,
            "date": day.isoformat(),
            "type": kind,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _insert(
    session: AsyncSession,
    user_id: uuid.UUID,
    account: Account,
    amount: str,
    kind: str,
    day: date,
) -> Transaction:
    """A row already sitting in the books before this creation."""
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


async def _pair_id(session: AsyncSession, transaction_id: str) -> uuid.UUID | None:
    result = await session.execute(
        select(Transaction.transfer_pair_id).where(
            Transaction.id == uuid.UUID(transaction_id)
        )
    )
    return result.scalar_one()


@pytest.mark.asyncio
async def test_second_manual_leg_pairs_with_the_first(
    client, auth_headers, session: AsyncSession, test_user, test_workspace
):
    """Two legs typed in by hand end up in the same transfer pair."""
    checking = await _account(session, test_user.id, test_workspace.id, "Checking")
    savings = await _account(session, test_user.id, test_workspace.id, "Savings")
    day = app_today()

    credit = await _post(client, auth_headers, savings, "100.00", "credit", day)
    debit = await _post(client, auth_headers, checking, "100.00", "debit", day)

    debit_pair = await _pair_id(session, debit["id"])
    credit_pair = await _pair_id(session, credit["id"])

    assert debit_pair is not None
    assert debit_pair == credit_pair


@pytest.mark.asyncio
async def test_creation_never_pairs_two_old_rows(
    client, auth_headers, session: AsyncSession, test_user, test_workspace
):
    """An unrelated creation must not link rows that were both already there."""
    checking = await _account(session, test_user.id, test_workspace.id, "Old A")
    savings = await _account(session, test_user.id, test_workspace.id, "Old B")
    third = await _account(session, test_user.id, test_workspace.id, "Old C")
    day = app_today()

    old_debit = await _insert(session, test_user.id, checking, "300.00", "debit", day)
    old_credit = await _insert(session, test_user.id, savings, "300.00", "credit", day)

    await _post(client, auth_headers, third, "999.00", "credit", day)

    assert await _pair_id(session, str(old_debit.id)) is None
    assert await _pair_id(session, str(old_credit.id)) is None


@pytest.mark.asyncio
async def test_creation_still_sees_a_competing_leg_one_window_further(
    client, auth_headers, session: AsyncSession, test_user, test_workspace
):
    """A claim the other leg could see is weighed, even outside the new row's window.

    The new debit on day 0 reaches the credit on day 2, and that credit also
    has a claim from another debit on day 4. The credit cannot choose, so
    nothing is linked. Loading only the new row's own window would drop the
    day-4 debit, make the day-2 credit look certain and pair it with the new
    debit.
    """
    checking = await _account(session, test_user.id, test_workspace.id, "Window A")
    savings = await _account(session, test_user.id, test_workspace.id, "Window B")
    other = await _account(session, test_user.id, test_workspace.id, "Window C")
    day = app_today()

    near_credit = await _insert(
        session, test_user.id, savings, "100.00", "credit", day + timedelta(days=2)
    )
    far_debit = await _insert(
        session, test_user.id, other, "100.00", "debit", day + timedelta(days=4)
    )

    debit = await _post(client, auth_headers, checking, "100.00", "debit", day)

    debit_pair = await _pair_id(session, debit["id"])
    near_pair = await _pair_id(session, str(near_credit.id))
    far_pair = await _pair_id(session, str(far_debit.id))
    assert debit_pair is None, (debit_pair, near_pair, far_pair)
    assert near_pair is None
    assert far_pair is None
