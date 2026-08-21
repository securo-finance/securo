"""Pure rule evaluation engine — no DB access."""
import re
import unicodedata
import uuid
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.transaction import Transaction


def _strip_accents(text: str) -> str:
    """Remove diacritics (accents), preserving case."""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _normalize(text: str) -> str:
    """Normalize text: uppercase and remove diacritics (accents)."""
    return _strip_accents(text.upper())


def _to_decimal(val) -> Decimal:
    try:
        return Decimal(str(val))
    except InvalidOperation:
        return Decimal("0")


def _to_date(val) -> date | None:
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    if isinstance(val, str):
        try:
            return date.fromisoformat(val)
        except ValueError:
            return None
    return None


def _match_condition(condition: dict, tx: "Transaction") -> bool:
    field = condition.get("field", "")
    op = condition.get("op", "")
    value = condition.get("value")

    tx_val = getattr(tx, field, None)

    # A blank value matches everything: "" is a substring of any string, every
    # string starts/ends with it, an empty regex always matches, and numeric
    # comparisons fall back to 0. Creation now rejects these, but rules saved
    # before that validation existed are still in users' databases — refuse to
    # match rather than recategorizing their whole ledger. Explicit 0/False
    # are real values and pass through.
    if value is None or (isinstance(value, str) and not value.strip()):
        return False

    # String operators
    if op in ("contains", "not_contains", "starts_with", "ends_with", "equals", "not_equals", "regex"):
        tx_str = _normalize(str(tx_val or ""))
        val_str = _normalize(str(value or ""))

        if op == "contains":
            return val_str in tx_str
        if op == "not_contains":
            return val_str not in tx_str
        if op == "starts_with":
            return tx_str.startswith(val_str)
        if op == "ends_with":
            return tx_str.endswith(val_str)
        if op == "equals":
            return tx_str == val_str
        if op == "not_equals":
            return tx_str != val_str
        if op == "regex":
            try:
                # Strip accents from the pattern so it lines up with the
                # normalized text, but keep its case: uppercasing a regex
                # inverts escape classes (\s -> \S, \b -> \B, \d -> \D) and
                # breaks inline flags. Case is already handled by IGNORECASE.
                pattern = _strip_accents(str(value or ""))
                return bool(re.search(pattern, tx_str, re.IGNORECASE))
            except re.error:
                return False

    # Numeric operators
    if op in ("gt", "gte", "lt", "lte"):
        if field == "date":
            tx_date = _to_date(tx_val)
            val_date = _to_date(value)
            if tx_date is None or val_date is None:
                return False
            if op == "gt":
                return tx_date > val_date
            if op == "gte":
                return tx_date >= val_date
            if op == "lt":
                return tx_date < val_date
            if op == "lte":
                return tx_date <= val_date

        tx_num = _to_decimal(tx_val)
        val_num = _to_decimal(value)
        if op == "gt":
            return tx_num > val_num
        if op == "gte":
            return tx_num >= val_num
        if op == "lt":
            return tx_num < val_num
        if op == "lte":
            return tx_num <= val_num

    return False


def _is_group(node: dict) -> bool:
    """A condition list entry is a group when it carries its own condition list."""
    return isinstance(node, dict) and isinstance(node.get("conditions"), list)


def _match_group(group: dict, tx: "Transaction") -> bool:
    """Evaluate one nested group: its leaves joined by the group's own operator.

    Groups hold leaves only, which caps a rule at two levels. A nested group
    would reach `_match_condition` with no `field`/`op` and evaluate to False;
    creation rejects them, so this only guards hand-edited data.
    """
    conditions = group.get("conditions") or []
    if not conditions:
        return False
    results = [_match_condition(c, tx) for c in conditions]
    if group.get("op") == "or":
        return any(results)
    return all(results)  # "and" is default


def evaluate_conditions(conditions_op: str, conditions: list[dict], tx: "Transaction") -> bool:
    """Return True if the transaction matches the rule's conditions.

    Each entry is either a leaf condition (`field`/`op`/`value`) or a group that
    joins its own leaves with its own operator, letting a rule mix AND and OR —
    e.g. `type is debit AND (description contains UBER OR contains 99POP)`.
    """
    if not conditions:
        return False
    results = [
        _match_group(node, tx) if _is_group(node) else _match_condition(node, tx)
        for node in conditions
    ]
    if conditions_op == "or":
        return any(results)
    return all(results)  # "and" is default


def merge_notes(existing: str | None, incoming: str | None) -> str | None:
    """Combine two note strings the way `append_notes` combines tags.

    Used when an incoming charge is folded into a row that already has notes:
    the existing text is never dropped, the incoming one is only appended when
    it is not already in there.
    """
    incoming = (incoming or "").strip()
    if not incoming:
        return existing
    existing = (existing or "").strip()
    if not existing:
        return incoming
    if incoming in existing:
        return existing
    return f"{existing} {incoming}"


def apply_rule_actions(
    actions: list[dict],
    tx: "Transaction",
    category_already_set: bool,
    *,
    skip_description: bool = False,
) -> bool:
    """Apply actions in-place and return the updated category-set flag."""
    for action in actions:
        op = action.get("op")
        value = action.get("value")

        if op == "set_category" and not category_already_set:
            try:
                tx.category_id = uuid.UUID(str(value))
                category_already_set = True
            except (ValueError, AttributeError):
                pass

        elif op == "set_description":
            if skip_description:
                continue
            description = str(value or "").strip()
            if not description or description == tx.description:
                continue
            if getattr(tx, "original_description", None) is None:
                tx.original_description = tx.description
            tx.description = description
            tx.description_is_rule_managed = True

        elif op == "set_payee":
            try:
                tx.payee_id = uuid.UUID(str(value))
            except (ValueError, AttributeError):
                pass

        elif op == "append_notes":
            new_tags = str(value or "").strip()
            if not new_tags:
                continue
            existing = tx.notes or ""
            if new_tags not in existing:
                tx.notes = (existing + " " + new_tags).strip() if existing else new_tags

        elif op == "ignore":
            tx.is_ignored = True

    return category_already_set
