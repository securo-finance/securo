"""backfill external_id for Enable Banking transactions (#753 follow-up)

Rows that were keyed under the old (raw-reference) scheme no longer match
what the next sync computes, so without this backfill every affected
Enable Banking transaction would be re-inserted as a duplicate on its next
sync. Scoped strictly to Enable Banking connections via bank_connections.provider
— every other provider's transactions are untouched.

Covers synced rows and locally imported rows that a sync later linked to an
Enable Banking transaction: the link moves the EB id onto the imported row but
keeps its import source, and nothing else would re-key it.

Only touches transactions whose entry_reference-based branch actually
changed: rows whose fallback fingerprint (empty/"0" entry_reference) was
used are unaffected by the fix and are skipped here too.

Rows whose raw_data no longer describes them (amount, direction or date
differ) are skipped as well. Under the old scheme a later transaction sharing
the reference matched the existing row and merged its payload into it, so
hashing that payload would hand the row the later transaction's id and hide
that transaction for good. Those rows keep their legacy id; the sync's
entry_reference re-key claims them when their own transaction comes back.

Idempotent: recomputes the id from each row's own raw_data and only
updates rows whose external_id differs (raw-reference rows as well as rows
keyed by an earlier revision of the disambiguation hash). Safe to re-run.

Revision ID: 102
Revises: 101
Create Date: 2026-10-01
"""
from alembic import op
from sqlalchemy import text

from app.providers.enable_banking import EnableBankingProvider

revision = "102"
down_revision = "101"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        text(
            """
            SELECT t.id, t.external_id, t.raw_data, t.amount, t.type, t.date
            FROM transactions t
            JOIN accounts a ON a.id = t.account_id
            JOIN bank_connections bc ON bc.id = a.connection_id
            WHERE bc.provider = 'enable_banking'
              AND t.source IN ('sync', 'import', 'csv', 'ofx', 'qif', 'camt')
              AND t.raw_data IS NOT NULL
              AND btrim(t.raw_data ->> 'entry_reference', E' \\t\\n\\r\\x0b') NOT IN ('', '0')
            """
        )
    ).fetchall()

    provider = EnableBankingProvider()
    for row in rows:
        raw_data = row.raw_data
        if not isinstance(raw_data, dict):
            continue
        entry_ref = (raw_data.get("entry_reference") or "").strip()
        if not entry_ref or entry_ref == "0":
            continue
        # The account uid only feeds the fingerprint fallback, which a
        # non-empty entry_reference never reaches.
        parsed = provider._build_transaction("", raw_data, "posted", "auto")
        if parsed is None or (parsed.amount, parsed.type, parsed.date) != (
            row.amount,
            row.type,
            row.date,
        ):
            continue
        if parsed.external_id == row.external_id:
            continue
        bind.execute(
            text("UPDATE transactions SET external_id = :new_id WHERE id = :id"),
            {"new_id": parsed.external_id, "id": row.id},
        )


def downgrade() -> None:
    pass
