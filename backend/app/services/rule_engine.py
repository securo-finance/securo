"""Pure rule evaluation engine — no DB access."""
import re
import unicodedata
import uuid
from collections.abc import Collection
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.transaction import Transaction


def _strip_accents(text: str) -> str:
    """Remove diacritics (accents), preserving case."""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def compile_rule_regex(pattern: str) -> re.Pattern[str]:
    """Compile a regex using Securo's runtime normalization and safety policy."""
    effective_pattern = _strip_accents(pattern)
    try:
        compiled = re.compile(effective_pattern, re.IGNORECASE)
    except re.error as exc:
        raise ValueError("Invalid regular expression") from exc
    if compiled.search("") is not None:
        raise ValueError("Regular expression must not match an empty string")
    return compiled


def _normalize(text: str) -> str:
    """Normalize text: uppercase and remove diacritics (accents)."""
    return _strip_accents(text.upper())


def _to_decimal(val) -> Decimal:
    try:
        return Decimal(str(val))
    except InvalidOperation:
        return Decimal("0")


def _signed_amount(tx: "Transaction", raw) -> Decimal:
    """The amount as a condition compares it: credits positive, debits negative.

    Amounts are stored positive with the direction in `type`, so an unsigned
    comparison sees every transaction as positive. The sign is what lets
    `amount > 0` select income and `amount < 0` expenses. The magnitude is
    taken first because a row saved with a negative amount (the manual API
    does not reject one) must follow the same contract: the sign comes from
    `type` alone.
    """
    amount = abs(_to_decimal(raw))
    if getattr(tx, "type", None) == "debit":
        amount = -amount
    return amount


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
            # Amounts are stored with a fixed scale (100.00) while the rule
            # form submits the number as typed (100): comparing their string
            # forms would never agree. Compare numerically when both sides
            # parse, and fall back to text when either does not. The
            # comparison is signed: a debit of 100.00 is -100.
            if field == "amount":
                try:
                    return _signed_amount(tx, tx_val) == Decimal(str(value))
                except InvalidOperation:
                    pass
            return tx_str == val_str
        if op == "not_equals":
            # Signed for the same reason as `equals`: a debit of 100.00 is
            # -100, so it is not equal to the typed 100.
            if field == "amount":
                try:
                    return _signed_amount(tx, tx_val) != Decimal(str(value))
                except InvalidOperation:
                    pass
            return tx_str != val_str
        if op == "regex":
            try:
                # Strip accents from the pattern so it lines up with the
                # normalized text, but keep its case: uppercasing a regex
                # inverts escape classes (\s -> \S, \b -> \B, \d -> \D) and
                # breaks inline flags. Case is already handled by IGNORECASE.
                compiled = compile_rule_regex(str(value or ""))
                return compiled.search(tx_str) is not None
            except ValueError:
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

        # Amounts are stored positive with the direction in `type`, so the
        # comparison needs the signed value: `amount > 0` is income and
        # `amount < 0` expenses.
        tx_num = _signed_amount(tx, tx_val) if field == "amount" else _to_decimal(tx_val)
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
    assignable_category_ids: Collection[uuid.UUID] | None = None,
) -> bool:
    """Apply actions in-place and return the updated category-set flag.

    Only a category in `assignable_category_ids` is ever written. A rule that
    names any other one keeps its remaining actions and drops the
    categorization, leaving the transaction uncategorized.

    That covers two cases. A hidden category means the user stopped using it,
    so the pickers no longer offer it and neither should a rule. A category
    that is simply gone means the rule went stale: its id lives inside a JSON
    action with no foreign key behind it, and writing it would fail the insert
    and take the whole import or sync batch with it.

    Passing nothing skips the check, for callers with no workspace at hand.
    """
    for action in actions:
        op = action.get("op")
        value = action.get("value")

        if op == "set_category" and not category_already_set:
            try:
                category_id = uuid.UUID(str(value))
            except (ValueError, AttributeError):
                continue
            if assignable_category_ids is not None and category_id not in assignable_category_ids:
                continue
            tx.category_id = category_id
            category_already_set = True

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
