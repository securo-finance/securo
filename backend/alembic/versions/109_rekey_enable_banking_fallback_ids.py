"""re-key Enable Banking rows that still carry the pre-v0.16.3 fallback id (#1085)

Before v0.16.3 an entry without a usable ``entry_reference`` got a fallback id
that left out the transaction date and kept only the counterparty IBANs, and
an entry_reference of "0" was stored as the literal id "0". Since v0.16.3 the
id includes the transaction date and the counterparty identity, so the next
sync computes a different id for every such row it already stored and imports
it again.

Each row is re-keyed from its own stored payload, the way 102 did for rows
with a reference. Both id recipes are frozen in this file (the old one from
v0.16.2, the current one from v0.16.3), so later changes to the provider
cannot change what this migration does. A row is only touched when all of
these hold:

- its connection is Enable Banking, it is posted, and it has a stored payload;
- its reference is empty or "0" and its id is exactly the old id of that
  payload (so the id still describes the payload, not one merged in from a
  later entry);
- the payload still describes the row (amount, direction and date);
- the new id is not already used in the account and no other row of the
  account would get the same new id. Such rows keep their old id.

A row whose payload cannot be read is skipped. Pending rows are not touched.
Rows that are skipped keep their old id and may still be imported again by
the next sync; this migration does not repair duplicates that already exist.

Idempotent: a re-keyed row no longer carries the old id. Safe to re-run.

Revision ID: 109
Revises: 108
"""
import hashlib
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any, Optional

from alembic import op
from sqlalchemy import text

revision = "109"
down_revision = "108"
branch_labels = None
depends_on = None

# Errors a malformed stored payload can raise while its id is computed. Only
# the pure computation is guarded; database errors are never swallowed.
_PAYLOAD_ERRORS = (AttributeError, TypeError, ValueError, ArithmeticError)


def _join_remittance(value: Any) -> str:
    if isinstance(value, list):
        return " ".join(str(v) for v in value if v).strip()
    return (value or "").strip() if isinstance(value, str) else ""


def _counterparty_name(raw: dict, party: str) -> str:
    party_obj = raw.get(party)
    name = party_obj.get("name") if isinstance(party_obj, dict) else None
    if not isinstance(name, str) or not name.strip():
        name = raw.get(f"{party}_name")
    return name.strip() if isinstance(name, str) else ""


def _iban(raw: dict, party: str) -> str:
    account = raw.get(f"{party}_account")
    iban = account.get("iban") if isinstance(account, dict) else None
    return iban if isinstance(iban, str) else ""


def _legacy_fingerprint(account_uid: str, raw: dict) -> str:
    """The fallback id up to v0.16.2 (no transaction date, IBANs only)."""
    amount = raw.get("transaction_amount") or {}
    parts = [
        str(account_uid),
        str(raw.get("booking_date") or ""),
        str(raw.get("value_date") or ""),
        str(amount.get("amount") or ""),
        str(amount.get("currency") or ""),
        str(raw.get("credit_debit_indicator") or ""),
        _join_remittance(raw.get("remittance_information"))[:80],
        _iban(raw, "creditor"),
        _iban(raw, "debtor"),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]


def _current_fingerprint(account_uid: str, raw: dict) -> str:
    """The fallback id from v0.16.3 on."""
    amount = raw.get("transaction_amount") or {}
    parts = [
        str(account_uid),
        str(raw.get("booking_date") or ""),
        str(raw.get("value_date") or ""),
        str(raw.get("transaction_date") or ""),
        str(amount.get("amount") or ""),
        str(amount.get("currency") or ""),
        str(raw.get("credit_debit_indicator") or ""),
        _join_remittance(raw.get("remittance_information"))[:80],
        _iban(raw, "creditor").strip() or _counterparty_name(raw, "creditor"),
        _iban(raw, "debtor").strip() or _counterparty_name(raw, "debtor"),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]


def _iso_date(value: Any) -> Optional[date]:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _new_id(account_uid: str, raw: dict, stored_id: str, row: Any) -> Optional[str]:
    """The id to give the row, or None when it must keep its current one."""
    entry_ref = str(raw.get("entry_reference") or "").strip()
    if entry_ref == "0":
        old_id = "0"
    elif not entry_ref:
        old_id = _legacy_fingerprint(account_uid, raw)
    else:
        return None
    if old_id != stored_id:
        return None
    amount_obj = raw.get("transaction_amount")
    if not isinstance(amount_obj, dict):
        return None
    amount = Decimal(str(amount_obj.get("amount", "0"))).copy_abs()
    txn_type = "debit" if str(raw.get("credit_debit_indicator") or "").upper() == "DBIT" else "credit"
    txn_date = (
        _iso_date(raw.get("booking_date"))
        or _iso_date(raw.get("value_date"))
        or _iso_date(raw.get("transaction_date"))
    )
    if (amount, txn_type, txn_date) != (row.amount, row.type, row.date):
        return None
    new_id = _current_fingerprint(account_uid, raw)
    return None if new_id == stored_id else new_id


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

    planned: dict = defaultdict(list)  # (account_id, new id) -> [row id]
    for row in rows:
        raw = row.raw_data
        if not isinstance(raw, dict):
            continue
        try:
            new_id = _new_id(row.account_uid or "", raw, row.external_id, row)
        except _PAYLOAD_ERRORS:
            continue
        if new_id is not None:
            planned[(row.account_id, new_id)].append(row.id)

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
