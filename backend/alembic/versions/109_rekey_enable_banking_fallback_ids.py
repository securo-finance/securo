"""re-key Enable Banking rows that still carry the old fallback id (#1085)

The fallback id for an entry without a usable ``entry_reference`` used to
leave out the transaction date and keep only the counterparty IBANs. The
current recipe includes both, so the next sync computes a different id for
every such row it already stored and would insert it again, or let a held
row be claimed by a different purchase.

Each row is re-keyed from its own stored payload, the way 102 did for rows
with a reference. A row is only touched when all of these hold:

- its connection is Enable Banking, it is posted, and it has a stored payload;
- its id is exactly the old id of that payload (so the id still describes
  the payload, not a payload merged in from a later entry);
- the payload still describes the row (amount, direction and date);
- the new id is not already used in the account and no other row of the
  account would get the same new id. Such rows keep their old id.

Only posted rows are re-keyed. A pending row is left as it is: it is not
final, and it settles or leaves the feed on its own.

Idempotent: a re-keyed row no longer carries the old id. Safe to re-run.

Revision ID: 109
Revises: 108
"""
from collections import defaultdict

from alembic import op
from sqlalchemy import text

from app.providers.enable_banking import (
    EnableBankingProvider,
    legacy_fallback_external_id,
)

revision = "109"
down_revision = "108"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        text(
            """
            SELECT t.id, t.account_id, a.external_id AS account_uid,
                   t.external_id, t.raw_data, t.amount, t.type, t.date
            FROM transactions t
            JOIN accounts a ON a.id = t.account_id
            JOIN bank_connections bc ON bc.id = a.connection_id
            WHERE bc.provider = 'enable_banking'
              AND t.source IN ('sync', 'import', 'csv', 'ofx', 'qif', 'camt')
              AND t.status = 'posted'
              AND t.raw_data IS NOT NULL
              AND t.external_id IS NOT NULL
              AND coalesce(
                    btrim(t.raw_data ->> 'entry_reference', E' \\t\\n\\r\\x0b'), ''
                  ) IN ('', '0')
            """
        )
    ).fetchall()

    provider = EnableBankingProvider()
    planned: dict = defaultdict(list)  # (account_id, new_id) -> [row id]
    for row in rows:
        raw = row.raw_data
        if not isinstance(raw, dict):
            continue
        account_uid = row.account_uid or ""
        if legacy_fallback_external_id(account_uid, raw) != row.external_id:
            continue
        parsed = provider._build_transaction(account_uid, raw, "posted", "auto")
        if parsed is None or (parsed.amount, parsed.type, parsed.date) != (
            row.amount,
            row.type,
            row.date,
        ):
            continue
        if parsed.external_id == row.external_id:
            continue
        planned[(row.account_id, parsed.external_id)].append(row.id)

    for (account_id, new_id), row_ids in planned.items():
        if len(row_ids) != 1:
            continue
        taken = bind.execute(
            text(
                "SELECT 1 FROM transactions "
                "WHERE account_id = :account_id AND external_id = :new_id LIMIT 1"
            ),
            {"account_id": account_id, "new_id": new_id},
        ).first()
        if taken is not None:
            continue
        bind.execute(
            text("UPDATE transactions SET external_id = :new_id WHERE id = :id"),
            {"new_id": new_id, "id": row_ids[0]},
        )


def downgrade() -> None:
    pass
