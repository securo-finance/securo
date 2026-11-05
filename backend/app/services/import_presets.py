"""Named CSV import presets: pure spec data + confirmed-export shapes.

A preset is a data bundle — column aliases, sign convention, default date
format, category alias seed — applied on top of the existing generic CSV
pipeline (design packet §1, §3.4). Nothing here parses rows or touches the
DB; `parse_csv` consumes the spec when given it.

Late-binding rule (§3.4): every header name, sign flag and date-format
preference is DATA in this module, never a code branch, so a real-export
delta is a one-line data change.
"""

from dataclasses import dataclass
from enum import Enum

__all__ = [
    "ImportPreset",
    "MONARCH",
    "PRESETS",
    "PresetShape",
    "detect_import_preset",
    "detect_monarch_shape",
    "normalize_header",
    "resolve_preset_alias",
    "resolve_preset_columns",
    "split_monarch_category",
    "MONARCH_GROUP_ALIASES",
    "MONARCH_CATEGORY_ALIASES",
    "match_monarch_alias",
]


class PresetShape(str, Enum):
    """Which documented Monarch export shape a header signature resolves to."""

    COMBINED = "combined"       # all-accounts export: carries an Account column
    PER_ACCOUNT = "per_account"  # single-register export: no Account column


@dataclass(frozen=True)
class ImportPreset:
    """Spec for one named import preset (pure data).

    Attributes:
        name: Stable identifier carried on the API `preset` form field.
        column_aliases: Securo target field -> ordered header aliases, all
            pre-normalized (lowercase, whitespace-collapsed). Resolution
            order overall is: explicit user column_mapping > these aliases
            > the generic auto-detect candidate lists.
        flip_amount: Sign convention: negate each amount before type
            inference (Monarch income-negative/expense-positive, §5.2).
        default_date_format: Strict DATE_FORMAT_MAP key pinned for the
            file when the user does not override (§5.4). None = no pin.
    """

    name: str
    column_aliases: dict[str, tuple[str, ...]]
    flip_amount: bool
    default_date_format: str | None


#: Monarch Money CSV preset — the alias table of §3.4 verbatim, as data.
MONARCH = ImportPreset(
    name="monarch",
    column_aliases={
        "date": ("date", "transaction date"),
        "account": ("account", "account name"),
        "description": ("merchant/description", "merchant", "description", "payee"),
        "category": ("category",),
        "amount": ("amount",),
        "notes": ("notes", "memo"),
        "tags": ("tags", "tag"),
    },
    flip_amount=True,
    default_date_format="MM/DD/YYYY",
)

PRESETS: dict[str, ImportPreset] = {MONARCH.name: MONARCH}


def normalize_header(header: str) -> str:
    """Casefold + collapse whitespace, the alias-matching normalization (§3.4)."""
    return " ".join(header.lower().split())


def resolve_preset_alias(aliases: tuple[str, ...], columns: list[str]) -> str | None:
    """Return the actual CSV header matching any alias, else None.

    Match is case/whitespace-insensitive; the returned value keeps the
    file's original casing so it can be fed back to the CSV reader.
    """
    normalized = {normalize_header(col): col for col in columns}
    for alias in aliases:
        if alias in normalized:
            return normalized[alias]
    return None


def resolve_preset_columns(preset: ImportPreset, columns: list[str]) -> dict[str, str]:
    """Resolve every preset alias that matched: Securo field -> CSV header.

    Only fields with a real match are returned; unmatched fields fall
    through to the generic auto-detect path in `parse_csv`.
    """
    resolved: dict[str, str] = {}
    for field, aliases in preset.column_aliases.items():
        header = resolve_preset_alias(aliases, columns)
        if header is not None:
            resolved[field] = header
    return resolved


def detect_monarch_shape(columns: list[str]) -> PresetShape | None:
    """Classify the shape of an explicitly confirmed Monarch export.

    These aliases are ordinary CSV headers, not evidence of provenance.
    Callers must not activate Monarch sign/date defaults from this shape.
    """
    if not columns:
        return None
    resolved = resolve_preset_columns(MONARCH, columns)
    if not {"date", "amount", "description", "category"} <= resolved.keys():
        return None
    return PresetShape.COMBINED if "account" in resolved else PresetShape.PER_ACCOUNT


def detect_import_preset(columns: list[str]) -> str | None:
    """Decline header-only inference: no trustworthy provenance discriminator."""
    return None


# --- Category mapping seed (§4.2) -----------------------------------------
# Both maps are keyed on the *normalized* Monarch cell (casefolded,
# whitespace-collapsed) — NOT on display-name normalization: "&" -> "and"
# belongs to matching steps 2-3 of §4.2, not to this built-in table.
# Targets are Securo internal category keys (never display names), so a
# pt-BR/de/fr workspace maps through the same data (R5).

#: Monarch group -> Securo internal key (group-level default, §4.2 table).
MONARCH_GROUP_ALIASES: dict[str, str] = {
    "income": "salary",
    "housing": "housing",
    "food & dining": "food",
    "groceries": "groceries",
    "transportation": "transport",
    "shopping": "shopping",
    "entertainment": "leisure",
    "health & fitness": "health",
    "medical": "health",
    "personal care": "personal_care",
    "education": "education",
    "bills & utilities": "subscriptions",
    "subscriptions": "subscriptions",
    "taxes": "other",
    "fees & charges": "other",
    "gifts & donations": "donations",
    "transfer": "transfers",
    "transfers": "transfers",
    "investments": "investments",
}

#: Monarch category (refinement) -> Securo internal key. Checked BEFORE the
#: group default: "Food & Dining: Groceries" lands on ``groceries``, not
#: ``food`` (§4.2 "category-level refinement beats group-level default").
MONARCH_CATEGORY_ALIASES: dict[str, str] = {
    "paycheck": "salary",
    "groceries": "groceries",
    "subscriptions": "subscriptions",
    "taxes": "taxes",
    "credit card payment": "transfers",
    "transfer": "transfers",
}


def _normalize_category_cell(value: str) -> str:
    return " ".join(value.lower().split())


def split_monarch_category(raw: str | None) -> tuple[str | None, str | None]:
    """Split a Monarch category cell into (group, category) (§4.1).

    ``"Food & Dining: Restaurants"`` -> ("Food & Dining", "Restaurants")
    ``"Groceries"`` (bare)           -> (None, "Groceries")
    ``""`` / None                    -> (None, None)
    """
    if not raw or not raw.strip():
        return None, None
    text = raw.strip()
    for sep in (": ", ":"):
        if sep in text:
            group, _, category = text.partition(sep)
            group, category = group.strip(), category.strip()
            if group and category:
                return group, category
            if category:  # ":Restaurants" — no group part
                return None, category
            return (group or None), None
    return None, text


def match_monarch_alias(raw: str | None) -> str | None:
    """Built-in alias table lookup (§4.2 step 1): raw cell -> Securo key.

    Category-level refinement wins over the group-level default. Returns
    None when neither part is known — "unmapped", never forced into a
    bucket (§4.2 rule 1).
    """
    group, category = split_monarch_category(raw)
    if category:
        key = MONARCH_CATEGORY_ALIASES.get(_normalize_category_cell(category))
        if key:
            return key
    if group:
        return MONARCH_GROUP_ALIASES.get(_normalize_category_cell(group))
    if category:
        # Bare category with no group: try the group table too, since a
        # bare "Groceries"/"Shopping" cell names a Monarch group directly.
        return MONARCH_GROUP_ALIASES.get(_normalize_category_cell(category))
    return None
