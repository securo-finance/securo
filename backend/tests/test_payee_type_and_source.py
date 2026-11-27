"""`type` holds a legal nature; `source` holds where the row came from.

These were one field until this migration. `type` also carried `merchant`,
which is a role rather than a nature, and it was the only thing telling the
hundreds of rows sync creates apart from the handful somebody typed in. The
two jobs are now separate, and these tests pin the part that is easy to
undo by accident: `source` is set by the code path, never by the caller,
and never rewritten afterwards.
"""
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.payee import PayeeCreate, PayeeUpdate
from app.services.payee_service import (
    create_payee,
    get_or_create_payee,
    update_payee,
)


# ---------------------------------------------------------------------------
# type: a legal nature, and null is a real answer
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_legal_nature_can_be_cleared(
    session: AsyncSession, test_user, test_workspace
):
    """Setting it wrong has to be undoable, so null must be reachable
    through an update and not only at creation."""
    payee = await create_payee(
        session,
        test_workspace.id,
        test_user.id,
        PayeeCreate(name="Reclassify me", type="company"),
    )
    updated = await update_payee(
        session, payee.id, test_workspace.id, PayeeUpdate(type=None, name="Kept")
    )
    assert updated is not None
    assert updated.type is None


# ---------------------------------------------------------------------------
# source: stamped by the path that inserted the row
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_form_path_is_manual(
    session: AsyncSession, test_user, test_workspace
):
    payee = await create_payee(
        session, test_workspace.id, test_user.id, PayeeCreate(name="Typed in by hand")
    )
    assert payee.source == "manual"


@pytest.mark.asyncio
async def test_the_bulk_path_is_sync(
    session: AsyncSession, test_user, test_workspace
):
    """What `get_or_create_payee` exists for: turning bank descriptors into
    rows, hundreds at a time."""
    payee = await get_or_create_payee(
        session, test_user.id, "UBER *TRIP", workspace_id=test_workspace.id
    )
    assert payee.source == "sync"


@pytest.mark.asyncio
async def test_the_importer_says_so_explicitly(
    session: AsyncSession, test_user, test_workspace
):
    payee = await get_or_create_payee(
        session,
        test_user.id,
        "FROM A CSV",
        workspace_id=test_workspace.id,
        source="import",
    )
    assert payee.source == "import"


@pytest.mark.asyncio
async def test_sync_does_not_relabel_something_entered_by_hand(
    session: AsyncSession, test_user, test_workspace
):
    """The protection that makes `source` worth having.

    A counterparty somebody registered deliberately keeps saying so even
    after sync meets the same name on a statement. Without this, one sync
    run would reclassify every hand-entered client as noise and the client
    picker would hide exactly the rows it exists to show.
    """
    typed = await create_payee(
        session,
        test_workspace.id,
        test_user.id,
        PayeeCreate(name="Acme Consulting", type="company"),
    )
    seen_by_sync = await get_or_create_payee(
        session, test_user.id, "acme consulting", workspace_id=test_workspace.id
    )
    assert seen_by_sync.id == typed.id
    assert seen_by_sync.source == "manual"
    # And the nature the user stated survives too.
    assert seen_by_sync.type == "company"


@pytest.mark.asyncio
async def test_source_is_not_writable_through_the_api(
    client: AsyncClient, auth_headers
):
    """Provenance is a fact about how the row got here. A client that could
    assert it could hide its own rows among the hand-entered ones."""
    resp = await client.post(
        "/api/payees",
        headers=auth_headers,
        json={"name": "Claims to be synced", "source": "sync"},
    )
    assert resp.status_code == 201
    assert resp.json()["source"] == "manual"


@pytest.mark.asyncio
async def test_updating_a_payee_never_moves_its_source(
    session: AsyncSession, test_user, test_workspace
):
    synced = await get_or_create_payee(
        session, test_user.id, "NETFLIX.COM", workspace_id=test_workspace.id
    )
    updated = await update_payee(
        session,
        synced.id,
        test_workspace.id,
        PayeeUpdate(name="Netflix", type="company"),
    )
    assert updated is not None
    assert updated.source == "sync"


