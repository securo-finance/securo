"""backfill external_id for Enable Banking transactions (#753 follow-up)

Rows that were keyed under the old (raw-reference) scheme no longer match
what the next sync computes, so without this backfill every affected
Enable Banking transaction would be re-inserted as a duplicate on its next
sync. Scoped strictly to Enable Banking connections via bank_connections.provider
— every other provider's transactions are untouched.

Only touches transactions whose entry_reference-based branch actually
changed: rows whose fallback fingerprint (empty/"0" entry_reference) was
used are unaffected by the fix and are skipped here too.

Idempotent: only matches rows whose external_id still equals the raw,
stripped entry_reference from their own raw_data (i.e. provably still on
the old scheme). Safe to re-run.

Revision ID: 097
Revises: 096
Create Date: 2026-10-01
"""
from alembic import op
from sqlalchemy import text

from app.providers.enable_banking import _entry_reference_external_id

revision = "097"
down_revision = "096"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        text(
            """
            SELECT t.id, t.raw_data
            FROM transactions t
            JOIN accounts a ON a.id = t.account_id
            JOIN bank_connections bc ON bc.id = a.connection_id
            WHERE bc.provider = 'enable_banking'
              AND t.source = 'sync'
              AND t.raw_data IS NOT NULL
              AND t.external_id = btrim(t.raw_data ->> 'entry_reference')
            """
        )
    ).fetchall()

    for row in rows:
        raw_data = row.raw_data
        if not isinstance(raw_data, dict):
            continue
        entry_ref = (raw_data.get("entry_reference") or "").strip()
        if not entry_ref or entry_ref == "0":
            continue
        amount_obj = raw_data.get("transaction_amount") or {}
        new_external_id = _entry_reference_external_id(entry_ref, amount_obj, raw_data)
        bind.execute(
            text("UPDATE transactions SET external_id = :new_id WHERE id = :id"),
            {"new_id": new_external_id, "id": row.id},
        )


def downgrade() -> None:
   pass
