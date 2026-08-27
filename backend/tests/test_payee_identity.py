"""A payee's identity is not its display name.

Sync used to recognise a counterparty by `lower(name)` alone. Because the
bank names a Pix counterparty after their CPF whenever it withholds the
name, users renamed those payees — and the next sync, still sending the
CPF, failed to find them and inserted a duplicate. The display name is the
one field a person is invited to change, so these tests pin down the two
things that now carry identity instead: the original name the provider
sent, and the fiscal document.

The documents below are fictitious but check-digit-valid, because
`normalise_and_validate` rejects anything else and a placeholder that
silently failed validation would make these tests pass for the wrong
reason.
"""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.fiscal.registry import TaxIdKind
from app.models.payee import Payee, PayeeTaxId
from app.schemas.payee import PayeeCreate, PayeeUpdate
from app.services.payee_service import (
    create_payee,
    get_or_create_payee,
    tax_id_from_provider,
    update_payee,
)

#: What the bank sends when it withholds an individual's name.
CPF = "529.982.247-25"
CNPJ = "11.222.333/0001-81"


# ---------------------------------------------------------------------------
# The reported regression
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rename_survives_the_next_sync(session: AsyncSession, test_user, test_workspace):
    """Rename a payee the bank named after a CPF; sync must still find it.

    This is the bug in full: before `original_name` existed, the second call
    matched nothing and created a second payee for one real person.
    """
    created = await get_or_create_payee(session, test_user.id, CPF, workspace_id=test_workspace.id)

    renamed = await update_payee(session, created.id, test_workspace.id, PayeeUpdate(name="Jane Doe"))
    assert renamed is not None
    assert renamed.name == "Jane Doe"

    again = await get_or_create_payee(session, test_user.id, CPF, workspace_id=test_workspace.id)
    assert again.id == created.id

    rows = await session.execute(select(Payee).where(Payee.workspace_id == test_workspace.id))
    assert len(rows.scalars().all()) == 1


@pytest.mark.asyncio
async def test_rename_survives_even_when_the_descriptor_changes(
    session: AsyncSession, test_user, test_workspace
):
    """The document outlives both the display name and the original one.

    A counterparty reached through a second channel arrives under a
    different descriptor, so neither name lookup can match. Only the
    document can, which is why it is tried first.
    """
    tax_id = (TaxIdKind.CNPJ, CNPJ)
    created = await get_or_create_payee(
        session, test_user.id, "ACME LTDA", workspace_id=test_workspace.id, tax_id=tax_id
    )
    await update_payee(session, created.id, test_workspace.id, PayeeUpdate(name="Acme"))

    again = await get_or_create_payee(
        session, test_user.id, "ACME *STORE 4471", workspace_id=test_workspace.id, tax_id=tax_id
    )
    assert again.id == created.id


# ---------------------------------------------------------------------------
# original_name
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_stamps_original_name(session: AsyncSession, test_user, test_workspace):
    payee = await get_or_create_payee(session, test_user.id, CPF, workspace_id=test_workspace.id)
    assert payee.original_name == CPF


@pytest.mark.asyncio
async def test_update_never_touches_original_name(session: AsyncSession, test_user, test_workspace):
    """`original_name` records what arrived, not what the row has become.

    If an edit could rewrite it, renaming twice would strand the payee just
    as the original bug did.
    """
    payee = await get_or_create_payee(session, test_user.id, CPF, workspace_id=test_workspace.id)

    await update_payee(session, payee.id, test_workspace.id, PayeeUpdate(name="Jane Doe"))
    await update_payee(session, payee.id, test_workspace.id, PayeeUpdate(name="Jane R. Doe"))

    assert payee.original_name == CPF
    again = await get_or_create_payee(session, test_user.id, CPF, workspace_id=test_workspace.id)
    assert again.id == payee.id


@pytest.mark.asyncio
async def test_manual_creation_leaves_original_name_null(
    session: AsyncSession, test_user, test_workspace
):
    """Nobody sent this name over the wire, so there is no original to keep."""
    payee = await create_payee(
        session, test_workspace.id, test_user.id, PayeeCreate(name="Hand Typed")
    )
    assert payee.original_name is None


@pytest.mark.asyncio
async def test_name_hit_backfills_a_missing_original_name(
    session: AsyncSession, test_user, test_workspace
):
    """A payee created before this column existed heals on the next sync.

    Without this the backfill would only reach rows the migration touched,
    and a hand-created payee adopted by sync would still be renameable into
    a duplicate.
    """
    manual = await create_payee(
        session, test_workspace.id, test_user.id, PayeeCreate(name="Corner Shop")
    )
    assert manual.original_name is None

    adopted = await get_or_create_payee(
        session, test_user.id, "corner shop", workspace_id=test_workspace.id
    )
    assert adopted.id == manual.id
    assert adopted.original_name == "corner shop"
    # Adoption must not relabel a row somebody entered on purpose.
    assert adopted.source == "manual"


@pytest.mark.asyncio
async def test_display_name_outranks_original_name(session: AsyncSession, test_user, test_workspace):
    """The current name is the most recent thing anybody said about a row.

    When one payee's original name collides with another's display name,
    the display name wins, and the result is at least deterministic.
    """
    renamed = await get_or_create_payee(session, test_user.id, CPF, workspace_id=test_workspace.id)
    await update_payee(session, renamed.id, test_workspace.id, PayeeUpdate(name="Jane Doe"))

    literal = await create_payee(session, test_workspace.id, test_user.id, PayeeCreate(name=CPF))

    hit = await get_or_create_payee(session, test_user.id, CPF, workspace_id=test_workspace.id)
    assert hit.id == literal.id


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


async def _tax_ids(session: AsyncSession, payee_id: uuid.UUID) -> dict[str, str]:
    rows = await session.execute(select(PayeeTaxId).where(PayeeTaxId.payee_id == payee_id))
    return {row.kind: row.value for row in rows.scalars().all()}


@pytest.mark.asyncio
async def test_sync_stores_the_document_normalised(
    session: AsyncSession, test_user, test_workspace
):
    """The table and its index existed all along; nothing ever wrote to them."""
    payee = await get_or_create_payee(
        session,
        test_user.id,
        CPF,
        workspace_id=test_workspace.id,
        tax_id=(TaxIdKind.CPF, CPF),
    )
    assert await _tax_ids(session, payee.id) == {"cpf": "52998224725"}


@pytest.mark.asyncio
async def test_document_settles_the_legal_nature(session: AsyncSession, test_user, test_workspace):
    """A CPF means an individual and a CNPJ a company. Nothing else guesses."""
    person = await get_or_create_payee(
        session, test_user.id, CPF, workspace_id=test_workspace.id, tax_id=(TaxIdKind.CPF, CPF)
    )
    company = await get_or_create_payee(
        session, test_user.id, "ACME", workspace_id=test_workspace.id, tax_id=(TaxIdKind.CNPJ, CNPJ)
    )
    unknown = await get_or_create_payee(
        session, test_user.id, "UBER *TRIP", workspace_id=test_workspace.id
    )

    assert person.type == "person"
    assert company.type == "company"
    assert unknown.type is None


@pytest.mark.asyncio
async def test_name_hit_attaches_a_missing_document(
    session: AsyncSession, test_user, test_workspace
):
    payee = await get_or_create_payee(session, test_user.id, "ACME", workspace_id=test_workspace.id)
    assert await _tax_ids(session, payee.id) == {}

    again = await get_or_create_payee(
        session, test_user.id, "ACME", workspace_id=test_workspace.id, tax_id=(TaxIdKind.CNPJ, CNPJ)
    )
    assert again.id == payee.id
    assert await _tax_ids(session, payee.id) == {"cnpj": "11222333000181"}


@pytest.mark.asyncio
async def test_provider_never_overwrites_a_hand_entered_document(
    session: AsyncSession, test_user, test_workspace
):
    """A person editing the form states the whole set; a payment does not.

    One payment reports one document. Letting it replace a value somebody
    considered and typed would lose the better of the two.
    """
    payee = await create_payee(
        session,
        test_workspace.id,
        test_user.id,
        PayeeCreate(name="ACME", tax_ids=[{"kind": "cnpj", "value": CNPJ}]),
    )

    await get_or_create_payee(
        session,
        test_user.id,
        "ACME",
        workspace_id=test_workspace.id,
        tax_id=(TaxIdKind.CNPJ, "11.444.777/0001-61"),
    )
    assert await _tax_ids(session, payee.id) == {"cnpj": "11222333000181"}


@pytest.mark.asyncio
async def test_a_malformed_document_never_breaks_a_sync(
    session: AsyncSession, test_user, test_workspace
):
    """Fall back to the name rather than failing over a bank's bad value."""
    payee = await get_or_create_payee(
        session,
        test_user.id,
        "ACME",
        workspace_id=test_workspace.id,
        tax_id=(TaxIdKind.CNPJ, "00.000.000/0000-00"),
    )
    assert payee.name == "ACME"
    assert await _tax_ids(session, payee.id) == {}
    assert payee.type is None


@pytest.mark.asyncio
async def test_duplicate_document_resolves_to_the_oldest(
    session: AsyncSession, test_user, test_workspace
):
    """Two payees may legitimately share a document; raising would be a sync failure.

    There is no unique constraint on purpose — issue #678 is what happens
    when sync turns pre-existing data into an IntegrityError.
    """
    first = await get_or_create_payee(
        session, test_user.id, "ACME ONE", workspace_id=test_workspace.id, tax_id=(TaxIdKind.CNPJ, CNPJ)
    )
    second = await create_payee(
        session,
        test_workspace.id,
        test_user.id,
        PayeeCreate(name="ACME TWO", tax_ids=[{"kind": "cnpj", "value": CNPJ}]),
    )
    assert first.id != second.id

    hit = await get_or_create_payee(
        session, test_user.id, "ACME THREE", workspace_id=test_workspace.id, tax_id=(TaxIdKind.CNPJ, CNPJ)
    )
    assert hit.id == first.id


# ---------------------------------------------------------------------------
# tax_id_from_provider
# ---------------------------------------------------------------------------


def test_tax_id_from_provider_maps_known_kinds():
    assert tax_id_from_provider("cpf", CPF) == (TaxIdKind.CPF, CPF)


@pytest.mark.parametrize(
    "kind,value",
    [
        ("passport", "X1234567"),  # a kind the registry does not know
        (None, CPF),
        ("cpf", None),
        ("cpf", ""),
    ],
)
def test_tax_id_from_provider_declines_what_it_cannot_use(kind, value):
    """An unusable document means "match by name", not "raise"."""
    assert tax_id_from_provider(kind, value) is None
