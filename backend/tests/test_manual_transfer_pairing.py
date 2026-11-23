"""Pairing a transfer whose leg was typed in by hand (issues #1798, #1802).

The detector has always run at the end of a sync, over the rows that just
arrived. A row added with the "+ Add" form never went through that path, so
a transfer recorded by hand sat unpaired forever and the review queue never
saw it. Creating a transaction now runs the same detection with the new row
as the only candidate, which keeps the rule that a pair may only form when
at least one leg is new.

Two more paths run it the same way (#1802): editing a row, so a figure or an
account typed wrong and then corrected still finds its other leg, and a file
import, so two statements carrying the two halves of one transfer land as a
pair. In both the candidates are only the rows that just changed, which is
what keeps two rows already in the books from being linked on the strength
of a third one arriving.
"""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.models.account import Account
from app.models.reconciliation import ReconciliationSuggestion
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


async def _patch(
    client, auth_headers, transaction_id: str, payload: dict
) -> dict:
    response = await client.patch(
        f"/api/transactions/{transaction_id}", headers=auth_headers, json=payload
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _import_rows(
    client, auth_headers, account: Account, rows: list[dict]
) -> dict:
    response = await client.post(
        "/api/transactions/import",
        headers=auth_headers,
        json={"account_id": str(account.id), "transactions": rows},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _rows(session: AsyncSession, workspace_id: uuid.UUID) -> list[Transaction]:
    # The request wrote through its own session; refresh what this one had
    # cached earlier so the rows show what is actually stored.
    result = await session.execute(
        select(Transaction)
        .where(Transaction.workspace_id == workspace_id)
        .execution_options(populate_existing=True)
    )
    return list(result.scalars().all())


@pytest.mark.asyncio
async def test_edit_pairs_with_the_leg_already_in_the_books(
    client, auth_headers, session: AsyncSession, test_user, test_workspace
):
    """Correcting a mistyped leg links it to the other leg that was there."""
    checking = await _account(session, test_user.id, test_workspace.id, "Edit A")
    savings = await _account(session, test_user.id, test_workspace.id, "Edit B")
    day = app_today()

    old_credit = await _insert(session, test_user.id, savings, "100.00", "credit", day)
    # Typed with the wrong figure, so the first attempt found nothing.
    debit = await _post(client, auth_headers, checking, "999.00", "debit", day)
    assert await _pair_id(session, debit["id"]) is None

    await _patch(client, auth_headers, debit["id"], {"amount": "100.00"})

    debit_pair = await _pair_id(session, debit["id"])
    assert debit_pair is not None
    assert debit_pair == await _pair_id(session, str(old_credit.id))


@pytest.mark.asyncio
async def test_edit_never_pairs_two_old_rows(
    client, auth_headers, session: AsyncSession, test_user, test_workspace
):
    """An unrelated edit must not link rows that were both already there."""
    checking = await _account(session, test_user.id, test_workspace.id, "Edit old A")
    savings = await _account(session, test_user.id, test_workspace.id, "Edit old B")
    third = await _account(session, test_user.id, test_workspace.id, "Edit old C")
    day = app_today()

    old_debit = await _insert(session, test_user.id, checking, "300.00", "debit", day)
    old_credit = await _insert(session, test_user.id, savings, "300.00", "credit", day)
    unrelated = await _post(client, auth_headers, third, "500.00", "credit", day)

    await _patch(client, auth_headers, unrelated["id"], {"amount": "777.00"})

    assert await _pair_id(session, str(old_debit.id)) is None
    assert await _pair_id(session, str(old_credit.id)) is None


@pytest.mark.asyncio
async def test_edit_reruns_detection_only_for_fields_the_detector_reads(
    client, auth_headers, session: AsyncSession, test_user, test_workspace, monkeypatch
):
    """A note or a flag does not turn the row into a new candidate."""
    from app.services import transaction_service

    calls: list = []

    async def _spy(*args, **kwargs):
        calls.append(kwargs.get("candidate_ids"))
        return 0

    monkeypatch.setattr(transaction_service, "detect_transfer_pairs", _spy)

    account = await _account(session, test_user.id, test_workspace.id, "Edit gate")
    transaction = await _post(client, auth_headers, account, "10.00", "debit", app_today())
    calls.clear()

    await _patch(client, auth_headers, transaction["id"], {"notes": "just a note"})
    assert calls == []

    await _patch(client, auth_headers, transaction["id"], {"amount": "12.00"})
    assert calls == [[uuid.UUID(transaction["id"])]]


@pytest.mark.asyncio
async def test_import_second_file_pairs_with_the_first(
    client, auth_headers, session: AsyncSession, test_user, test_workspace
):
    """The reported case: a debit file and a credit file carrying one transfer."""
    checking = await _account(session, test_user.id, test_workspace.id, "File A")
    savings = await _account(session, test_user.id, test_workspace.id, "File B")
    day = app_today()

    await _import_rows(
        client,
        auth_headers,
        checking,
        [
            {
                "description": "Transfer to savings",
                "amount": "100.00",
                "date": day.isoformat(),
                "type": "debit",
            }
        ],
    )
    rows = await _rows(session, test_workspace.id)
    assert len(rows) == 1
    assert rows[0].transfer_pair_id is None

    await _import_rows(
        client,
        auth_headers,
        savings,
        [
            {
                "description": "Transfer from checking",
                "amount": "100.00",
                "date": day.isoformat(),
                "type": "credit",
            }
        ],
    )

    rows = await _rows(session, test_workspace.id)
    assert len(rows) == 2
    assert rows[0].transfer_pair_id is not None
    assert rows[0].transfer_pair_id == rows[1].transfer_pair_id


@pytest.mark.asyncio
async def test_import_never_pairs_two_old_rows(
    client, auth_headers, session: AsyncSession, test_user, test_workspace
):
    """An unrelated import must not link rows that were both already there."""
    checking = await _account(session, test_user.id, test_workspace.id, "File old A")
    savings = await _account(session, test_user.id, test_workspace.id, "File old B")
    third = await _account(session, test_user.id, test_workspace.id, "File old C")
    day = app_today()

    old_debit = await _insert(session, test_user.id, checking, "300.00", "debit", day)
    old_credit = await _insert(session, test_user.id, savings, "300.00", "credit", day)

    await _import_rows(
        client,
        auth_headers,
        third,
        [
            {
                "description": "Unrelated",
                "amount": "999.00",
                "date": day.isoformat(),
                "type": "credit",
            }
        ],
    )

    assert await _pair_id(session, str(old_debit.id)) is None
    assert await _pair_id(session, str(old_credit.id)) is None


@pytest.mark.asyncio
async def test_import_pairs_legs_far_apart_in_one_file(
    client, auth_headers, session: AsyncSession, test_user, test_workspace
):
    """Candidates years apart are handled in batches without losing a pair."""
    checking = await _account(session, test_user.id, test_workspace.id, "Span A")
    savings = await _account(session, test_user.id, test_workspace.id, "Span B")
    other = await _account(session, test_user.id, test_workspace.id, "Span C")
    day = app_today()

    old_credit_a = await _insert(session, test_user.id, savings, "100.00", "credit", day)
    old_credit_b = await _insert(
        session, test_user.id, other, "200.00", "credit", day + timedelta(days=1000)
    )

    await _import_rows(
        client,
        auth_headers,
        checking,
        [
            {
                "description": "Old transfer",
                "amount": "100.00",
                "date": day.isoformat(),
                "type": "debit",
            },
            {
                "description": "Recent transfer",
                "amount": "200.00",
                "date": (day + timedelta(days=1000)).isoformat(),
                "type": "debit",
            },
        ],
    )

    pairs = {await _pair_id(session, str(tx.id)) for tx in await _rows(session, test_workspace.id)}
    assert None not in pairs
    assert len(pairs) == 2
    assert await _pair_id(session, str(old_credit_a.id)) in pairs
    assert await _pair_id(session, str(old_credit_b.id)) in pairs


@pytest.mark.asyncio
async def test_import_asks_when_two_legs_claim_the_same_one(
    client, auth_headers, session: AsyncSession, test_user, test_workspace
):
    """An imported leg that fits two rows goes to the queue, not to a pair."""
    checking = await _account(session, test_user.id, test_workspace.id, "Ask A")
    savings = await _account(session, test_user.id, test_workspace.id, "Ask B")
    other = await _account(session, test_user.id, test_workspace.id, "Ask C")
    day = app_today()

    await _insert(session, test_user.id, savings, "100.00", "credit", day)
    await _insert(session, test_user.id, other, "100.00", "credit", day)

    await _import_rows(
        client,
        auth_headers,
        checking,
        [
            {
                "description": "Transfer out",
                "amount": "100.00",
                "date": day.isoformat(),
                "type": "debit",
            }
        ],
    )

    assert all(tx.transfer_pair_id is None for tx in await _rows(session, test_workspace.id))
    suggestions = list(
        (
            await session.execute(
                select(ReconciliationSuggestion).where(
                    ReconciliationSuggestion.workspace_id == test_workspace.id
                )
            )
        ).scalars()
    )
    assert suggestions, "the ambiguous import should land in the review queue"
