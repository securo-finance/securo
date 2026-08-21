"""Default category/group taxonomy seeded into new workspaces.

The data lives in app/data/default_categories.json so that supporting a
new language, or adding a new default category or group, is a data-only
change: add the entries there and every seeding and name-matching code
path picks them up.

Each entry has a stable internal key and the shape
``{"names": {lang: display_name}, "icon": ..., "color": ...}``; groups
also carry ``position``, categories carry ``group`` (the group's
internal key) and optionally ``treat_as_transfer``.
"""

import json
from pathlib import Path

_DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "default_categories.json"

with _DATA_FILE.open(encoding="utf-8") as _f:
    _taxonomy = json.load(_f)

DEFAULT_GROUPS: dict[str, dict] = _taxonomy["groups"]
DEFAULT_CATEGORIES: dict[str, dict] = _taxonomy["categories"]

# Maps category internal key -> group internal key
CATEGORY_TO_GROUP: dict[str, str] = {
    key: data["group"] for key, data in DEFAULT_CATEGORIES.items()
}


def localized_name(data: dict, lang: str) -> str:
    """Display name for `lang`, falling back to English."""
    names = data["names"]
    return str(names.get(lang, names["en"]))


def name_variants(data: dict) -> set[str]:
    """Every language variant of an entry's display name."""
    return set(data["names"].values())
