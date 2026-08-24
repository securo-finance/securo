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
from importlib.resources import files

# importlib.resources (rather than Path(__file__)) so the file is found in
# every install mode — editable, wheel, or zipped — as long as it's declared
# as package data (see [tool.setuptools.package-data] in pyproject.toml).
_taxonomy = json.loads(
    files("app").joinpath("data", "default_categories.json").read_text(encoding="utf-8")
)

DEFAULT_GROUPS: dict[str, dict] = _taxonomy["groups"]
DEFAULT_CATEGORIES: dict[str, dict] = _taxonomy["categories"]

# Maps category internal key -> group internal key
CATEGORY_TO_GROUP: dict[str, str] = {
    key: data["group"] for key, data in DEFAULT_CATEGORIES.items()
}


def localized_name(data: dict, lang: str) -> str:
    """Display name for `lang`: requested language, then English, then any
    available translation. Never raises on a partially-translated entry —
    a missing language must degrade to a readable name, not break seeding."""
    names = data["names"]
    fallback = names.get("en") or next(iter(names.values()))
    return str(names.get(lang, fallback))


def name_variants(data: dict) -> set[str]:
    """Every language variant of an entry's display name."""
    return set(data["names"].values())
