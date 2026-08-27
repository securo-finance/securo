"""recover the document from payees the bank named after one

Revision ID: 077
Revises: 076
Create Date: 2026-08-26

Migration 076 gave every payee an `original_name` and made the lookup fall
back to it, which is what lets a rename survive. This one goes further and
recovers the *document* from those rows.

The rows in question are the ones this whole change exists for: a Pix to an
individual arrives with `receiver.name: null`, so the provider falls back to
the CPF and the payee ends up literally named `529.982.247-25`. Reading it
back out of `original_name` gets those payees onto the document-first
lookup immediately, rather than only once the same counterparty happens to
be paid again.

Two things it is careful about:

- A name is only treated as a document when it is *nothing but* a document.
  "52.288.516 OZEIAS AMORIM VIEIRA" is an MEI trading name that opens with a
  CNPJ root, not a CNPJ, and must not be read as one. Check digits alone
  would not settle that — an eleven-digit string passes them by luck about
  once in a hundred — so anything left over after the digits and their
  punctuation disqualifies the row.
- It never replaces a document a payee already carries. A value somebody
  entered by hand is more considered than one inferred from a name here.

CNPJ is handled alongside CPF. The reporter's data has no CNPJ-named payees,
because institutions withhold an individual's name but not a company's, but
the two cases are the same mechanism and excluding one would be arbitrary.

Validation goes through `app.fiscal.registry.normalise_and_validate`, the
same call `payee_service._apply_tax_ids` makes, so a document recovered here
is stored byte-identically to one entered through the form. The usual reason
to keep a migration free of application imports — that the code may change
underneath it — is weak for this particular import: CPF and CNPJ check
digits are fixed by law, and duplicating the arithmetic would create two
copies free to drift apart.
"""

import uuid
from typing import Optional, Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.fiscal.registry import TaxIdKind, normalise_and_validate

revision: str = "077"
down_revision: Union[str, None] = "076"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: Punctuation a Brazilian document is allowed to wear. Anything else in the
#: name means the name is not a bare document.
_DOCUMENT_PUNCTUATION = set(". -/")

#: The legal nature each document settles, mirroring what the service layer
#: stamps when it creates a payee from a document.
_TYPE_BY_KIND = {TaxIdKind.CPF.value: "person", TaxIdKind.CNPJ.value: "company"}


def _classify(name: Optional[str]) -> Optional[tuple[str, str]]:
    """(kind, normalised value) when this name is a document and nothing else."""
    if not name:
        return None
    stripped = name.strip()
    if not stripped:
        return None
    if any(c not in _DOCUMENT_PUNCTUATION and not c.isdigit() for c in stripped):
        return None
    # The two shapes are unambiguous by length, so trying both settles which
    # one this is without the migration having to know the rule itself.
    for kind in (TaxIdKind.CPF, TaxIdKind.CNPJ):
        value, error = normalise_and_validate(kind, stripped)
        if not error:
            return kind.value, value
    return None


def _candidates(conn) -> list[tuple]:
    """Payees whose original_name is a bare document, with what it resolves to."""
    rows = conn.execute(
        sa.text(
            "SELECT id, workspace_id, original_name, type FROM payees "
            "WHERE original_name IS NOT NULL"
        )
    ).fetchall()
    found = []
    for row in rows:
        classified = _classify(row.original_name)
        if classified is not None:
            found.append((row, *classified))
    return found


def upgrade() -> None:
    conn = op.get_bind()
    candidates = _candidates(conn)
    if not candidates:
        return

    taken = {
        (r.payee_id, r.kind)
        for r in conn.execute(sa.text("SELECT payee_id, kind FROM payee_tax_ids")).fetchall()
    }

    documents = []
    natures = []
    for payee, kind, digits in candidates:
        if (payee.id, kind) in taken:
            continue
        documents.append(
            {
                "id": uuid.uuid4(),
                "payee_id": payee.id,
                "workspace_id": payee.workspace_id,
                "kind": kind,
                "value": digits,
            }
        )
        # Only where nothing has been said yet. A nature somebody chose
        # outranks one inferred from a name, even a wrong one.
        if payee.type is None:
            natures.append({"payee_id": payee.id, "nature": _TYPE_BY_KIND[kind]})

    if documents:
        conn.execute(
            sa.text(
                "INSERT INTO payee_tax_ids (id, payee_id, workspace_id, kind, value) "
                "VALUES (:id, :payee_id, :workspace_id, :kind, :value)"
            ),
            documents,
        )
    if natures:
        conn.execute(
            sa.text("UPDATE payees SET type = :nature WHERE id = :payee_id"),
            natures,
        )


def downgrade() -> None:
    """Remove the documents this migration could have inferred.

    Reversible only up to a point, and deliberately so. A document somebody
    typed by hand that happens to equal the one inferred here is
    indistinguishable from it — but since the stored value is identical,
    dropping it loses no information the upgrade did not already supply.

    `payees.type` is left as it stands. The upgrade only filled it where
    nothing had been said, and there is no record of which rows those were;
    clearing it by shape would discard natures that predate this migration.
    """
    conn = op.get_bind()
    stale = [
        {"payee_id": payee.id, "kind": kind, "value": digits}
        for payee, kind, digits in _candidates(conn)
    ]
    if stale:
        conn.execute(
            sa.text(
                "DELETE FROM payee_tax_ids "
                "WHERE payee_id = :payee_id AND kind = :kind AND value = :value"
            ),
            stale,
        )
