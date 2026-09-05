"""A payee name is unique inside a workspace, not inside a user account.

Regression cover for #616. Payees moved onto workspaces in migration 052
and the service layer moved with them, but the database kept the unique
constraint written back when a payee belonged to a user
(`uq_payees_user_id_name`). Sync then looked for a counterparty in *this*
workspace, correctly found nothing, inserted, and hit a row the same
person owned in a *different* workspace. The bank connection failed with a
duplicate key error naming a counterparty the user could not see from
where they were standing.

The rule these tests pin: the same name may exist once per workspace, and
twice in one workspace it may not.
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import UniqueConstraint, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bank_connection import BankConnection
from app.models.payee import Payee
from app.models.workspace import Workspace, WorkspaceMember
from app.providers.base import AccountData, ConnectionData, TransactionData
from app.schemas.payee import PayeeCreate
from app.services.connection_service import handle_oauth_callback, sync_connection
from app.services.payee_service import create_payee, get_or_create_payee


# A CPF-shaped name, because that is what the bank sends as the
# counterparty on a Pix transfer and what the reported failures collided on.
COUNTERPARTY = "12345678901"


async def _second_workspace(session: AsyncSession, user_id: uuid.UUID) -> Workspace:
    """A second workspace the same user belongs to — a shared household
    workspace, or one they were added to. The single-workspace assumption
    is exactly what the old constraint encoded."""
    ws = Workspace(
        id=uuid.uuid4(), name="Shared", kind="personal",
        created_by_user_id=user_id, default_currency="BRL",
    )
    session.add(ws)
    await session.flush()
    session.add(
        WorkspaceMember(
            id=uuid.uuid4(), workspace_id=ws.id, user_id=user_id, role="owner",
        )
    )
    await session.commit()
    await session.refresh(ws)
    return ws


# ---------------------------------------------------------------------------
# the constraint itself
# ---------------------------------------------------------------------------
def test_uniqueness_is_scoped_to_the_workspace():
    """Asserted on the model because the test database is built from it:
    a constraint that lives only in a migration is invisible here, which
    is how the user-scoped one survived the move to workspaces unnoticed."""
    scopes = {
        tuple(col.name for col in constraint.columns)
        for constraint in Payee.__table_args__
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("workspace_id", "name") in scopes
    assert ("user_id", "name") not in scopes


@pytest.mark.asyncio
async def test_the_same_name_twice_in_one_workspace_is_rejected(
    session: AsyncSession, test_user, test_workspace
):
    """The half of the old rule that was right, and has to stay enforced by
    the database rather than only by the service's pre-check."""
    session.add(
        Payee(id=uuid.uuid4(), user_id=test_user.id,
              workspace_id=test_workspace.id, name=COUNTERPARTY)
    )
    await session.commit()

    session.add(
        Payee(id=uuid.uuid4(), user_id=test_user.id,
              workspace_id=test_workspace.id, name=COUNTERPARTY)
    )
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


# ---------------------------------------------------------------------------
# the reported failure
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_same_counterparty_can_exist_in_two_workspaces(
    session: AsyncSession, test_user, test_workspace
):
    """One person, two workspaces, one counterparty they transact with from
    both. Two rows, because a payee belongs to a workspace."""
    other = await _second_workspace(session, test_user.id)

    first = await create_payee(
        session, test_workspace.id, test_user.id, PayeeCreate(name=COUNTERPARTY)
    )
    second = await create_payee(
        session, other.id, test_user.id, PayeeCreate(name=COUNTERPARTY)
    )

    assert first.id != second.id
    assert {first.workspace_id, second.workspace_id} == {test_workspace.id, other.id}


@pytest.mark.asyncio
async def test_sync_creates_the_counterparty_in_its_own_workspace(
    session: AsyncSession, test_user, test_workspace
):
    """`get_or_create_payee` is the bulk path sync uses, and the one that
    raised the duplicate key: it looks the name up in the workspace being
    synced, so a hit in another workspace must not stop the insert."""
    other = await _second_workspace(session, test_user.id)
    await create_payee(
        session, test_workspace.id, test_user.id, PayeeCreate(name=COUNTERPARTY)
    )

    synced = await get_or_create_payee(
        session, test_user.id, COUNTERPARTY, workspace_id=other.id
    )
    await session.commit()

    assert synced.workspace_id == other.id
    rows = await session.execute(select(Payee).where(Payee.name == COUNTERPARTY))
    assert len(rows.scalars().all()) == 2


@pytest.mark.asyncio
async def test_get_or_create_reuses_the_row_inside_one_workspace(
    session: AsyncSession, test_user, test_workspace
):
    """The other side of the same call: within a workspace it still returns
    the existing row instead of racing the constraint."""
    existing = await create_payee(
        session, test_workspace.id, test_user.id, PayeeCreate(name=COUNTERPARTY)
    )

    found = await get_or_create_payee(
        session, test_user.id, COUNTERPARTY.lower(), workspace_id=test_workspace.id
    )

    assert found.id == existing.id


@pytest.mark.asyncio
async def test_connecting_a_bank_succeeds_with_the_name_already_used_elsewhere(
    session: AsyncSession, test_user, test_workspace
):
    """The user's story end to end: a counterparty already recorded in the
    personal workspace, and a bank connected in the shared one whose
    transactions name the same counterparty. The sync used to abort with
    `duplicate key value violates unique constraint "uq_payees_user_id_name"`
    and the connection reported "fail to connect"."""
    other = await _second_workspace(session, test_user.id)
    await create_payee(
        session, test_workspace.id, test_user.id, PayeeCreate(name=COUNTERPARTY)
    )

    conn = BankConnection(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=other.id,
        provider="test", external_id=f"ext-{uuid.uuid4().hex[:8]}",
        institution_name="Banco do Brasil", credentials={"token": "fake"},
        status="active", last_sync_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )
    session.add(conn)
    await session.commit()

    mock_provider = AsyncMock()
    mock_provider.refresh_credentials = AsyncMock(return_value={"token": "refreshed"})
    mock_provider.get_accounts = AsyncMock(return_value=[
        AccountData(
            external_id="bb-acc-1", name="Conta Corrente",
            type="checking", balance=Decimal("100"), currency="BRL",
        ),
    ])
    mock_provider.get_transactions = AsyncMock(return_value=[
        TransactionData(
            external_id="bb-tx-1", description="PIX ENVIADO",
            amount=Decimal("50"), date=date.today(), type="debit",
            currency="BRL", payee=COUNTERPARTY,
        ),
    ])

    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock), \
         patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock), \
         patch("app.services.connection_service.apply_rules_to_transaction", new_callable=AsyncMock):
        result_conn, _ = await sync_connection(
            session, conn.id, other.id, test_user.id
        )

    assert result_conn.status == "active"
    synced = await session.scalar(
        select(Payee).where(Payee.workspace_id == other.id, Payee.name == COUNTERPARTY)
    )
    assert synced is not None


@pytest.mark.asyncio
async def test_connecting_the_bank_succeeds_on_the_callback_itself(
    session: AsyncSession, test_user, test_workspace
):
    """The endpoint the report died on: `POST /api/connections/oauth/callback`
    imports the first batch of transactions inline, so the connection is
    what fails, not a later sync. Same counterparty, already recorded in
    another workspace."""
    other = await _second_workspace(session, test_user.id)
    await create_payee(
        session, test_workspace.id, test_user.id, PayeeCreate(name=COUNTERPARTY)
    )

    mock_provider = AsyncMock()
    mock_provider.handle_oauth_callback = AsyncMock(return_value=ConnectionData(
        external_id="ext-bb",
        institution_name="Banco do Brasil",
        credentials={"token": "x"},
        accounts=[
            AccountData(
                external_id="bb-acc-1", name="Conta Corrente",
                type="checking", balance=Decimal("100"), currency="BRL",
            ),
        ],
    ))
    mock_provider.get_transactions = AsyncMock(return_value=[
        TransactionData(
            external_id="bb-tx-1", description="PIX ENVIADO",
            amount=Decimal("50"), date=date.today(), type="debit",
            currency="BRL", payee=COUNTERPARTY,
        ),
    ])

    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock), \
         patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock), \
         patch("app.services.connection_service.apply_rules_to_transaction", new_callable=AsyncMock):
        connection = await handle_oauth_callback(
            session, other.id, test_user.id, "code", "pluggy", sync_assets=False,
        )

    assert connection is not None
    synced = await session.scalar(
        select(Payee).where(Payee.workspace_id == other.id, Payee.name == COUNTERPARTY)
    )
    assert synced is not None
