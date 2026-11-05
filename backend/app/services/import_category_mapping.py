"""Monarch category mapping service (design packet §4).

Implements the §4.2 suggestion chain as a preloaded, reusable mapper:

1. built-in alias table (import_presets.MONARCH_*_ALIASES) → Securo
   internal key → the workspace's own category row;
2. normalized-name equality against the workspace's actual categories
   (and groups) — casefold, strip, collapse whitespace, ``&`` → ``and``;
3. normalized-name equality against the EN names of the default taxonomy
   → internal key → the workspace's own row (this is how a localized or
   renamed-seed workspace still lands on the right row — matching goes
   through internal keys, never display names, R5);
4. else **unmapped** — no category is contributed (§4.4).

Hidden categories are never suggested (same term the preview uses:
``get_categories`` excludes hidden rows and hidden-group rows).
"""

import uuid
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.category import Category
from app.models.category_group import CategoryGroup
from app.services.category_defaults import (
    CATEGORY_TO_GROUP,
    DEFAULT_CATEGORIES,
    DEFAULT_GROUPS,
)
from app.services.category_group_service import get_groups
from app.services.category_service import get_categories
from app.services.import_presets import match_monarch_alias, split_monarch_category


def normalize_category_name(value: str) -> str:
    """Display-name normalization of §4.2 steps 2-3.

    casefold → strip → collapse whitespace → ``&`` becomes ``and``, the
    same normalization spirit as ``descriptions_match``.
    """
    return " ".join(value.casefold().replace("&", " and ").split())


def _lang_names(data: dict) -> list[tuple[str, str]]:
    """[(language, display_name)] for a taxonomy entry."""
    names = data.get("names", {})
    return list(names.items())


class MonarchCategoryMapper:
    """Preloaded workspace taxonomy that resolves many raw Monarch values.

    Load once per request (``await MonarchCategoryMapper.load(...)``),
    then call ``suggest`` per distinct category value — the mapping card
    and the dry-run unmapped count share one instance.
    """

    def __init__(
        self,
        categories: list[Category],
        groups: list[CategoryGroup],
    ) -> None:
        # Both lists arrive already excluding hidden rows (get_categories /
        # get_groups default), so hidden categories are never suggested.
        self._categories = list(categories)
        self._groups = list(groups)

        # Normalized display name → category row (§4.2 step 2).
        self._by_norm: dict[str, Category] = {}
        for category in self._categories:
            self._by_norm.setdefault(normalize_category_name(category.name), category)
        # Normalized group names (§4.2 step 2 "and groups"): consulted after
        # every candidate failed against categories — a group-only match is a
        # hit with no category target, and we never guess a child (§4.2 rule 1).
        self._group_norms = {
            normalize_category_name(group.name) for group in self._groups
        }

        # Internal key → the workspace's own row, via any-language display
        # name (§4.2 step 1/3 resolver). Language-independent keys are what
        # keep pt-BR/de/fr workspaces correct (R5).
        self._by_key: dict[str, Category] = {}
        rows_by_norm = {
            normalize_category_name(category.name): category
            for category in self._categories
        }
        for key, data in DEFAULT_CATEGORIES.items():
            for _lang, name in _lang_names(data):
                row = rows_by_norm.get(normalize_category_name(name))
                if row is not None:
                    self._by_key.setdefault(key, row)
                    break

        # EN names of the default taxonomy (§4.2 step 3): candidates are
        # matched against these, then resolved through internal keys above.
        self._en_names: list[tuple[str, str]] = []
        for key, data in DEFAULT_CATEGORIES.items():
            en_name = data.get("names", {}).get("en")
            if en_name:
                self._en_names.append(
                    (normalize_category_name(str(en_name)), key)
                )
        # EN group names only contribute where the group key is also a
        # category key (housing/food/transport/other): a group like
        # "Lifestyle" has no single target category to suggest.
        category_keys = set(DEFAULT_CATEGORIES)
        for key, data in DEFAULT_GROUPS.items():
            en_name = data.get("names", {}).get("en")
            if key in category_keys and en_name:
                self._en_names.append(
                    (normalize_category_name(str(en_name)), key)
                )

    @classmethod
    async def load(cls, session: AsyncSession, workspace_id: uuid.UUID) -> "MonarchCategoryMapper":
        categories = await get_categories(session, workspace_id)
        groups = await get_groups(session, workspace_id)
        return cls(categories, groups)

    def suggest(self, raw: str | None) -> Optional[Category]:
        """Resolve one raw Monarch category cell to a workspace category.

        Returns None when the value is empty or unmapped — the caller then
        contributes no category to those rows (§4.4): they land
        uncategorized unless the user's own rules assign one.
        """
        if not raw or not raw.strip():
            return None

        # Step 1: built-in alias table → internal key → own row. The table
        # identified the ONE acceptable target, so a hit stops the chain
        # ("first hit wins"): a hidden or deleted row resolves to None
        # instead of falling through to unrelated name matches — that is
        # how hidden categories are never suggested.
        key = match_monarch_alias(raw)
        if key is not None:
            return self._by_key.get(key)

        # Candidate strings (§4.2 step 2 order): composite, bare category,
        # group alone — all normalized the same way before comparison.
        group, category = split_monarch_category(raw)
        candidates: list[str] = []
        if group and category:
            candidates.append(f"{group}: {category}")
        if category:
            candidates.append(category)
        if group:
            candidates.append(group)
        candidates = list(dict.fromkeys(candidates))

        # Step 2: the workspace's own category names ...
        for candidate in candidates:
            hit = self._by_norm.get(normalize_category_name(candidate))
            if hit is not None:
                return hit
        # ... then its group names: a group-only match stops the chain with
        # no target rather than guessing across taxonomies.
        for candidate in candidates:
            if normalize_category_name(candidate) in self._group_norms:
                return None

        # Step 3: EN names of the default taxonomy → internal key → own row.
        for candidate in candidates:
            norm = normalize_category_name(candidate)
            for en_norm, en_key in self._en_names:
                if norm == en_norm:
                    row = self._by_key.get(en_key)
                    if row is not None:
                        return row

        # Step 4: unmapped.
        return None


__all__ = [
    "MonarchCategoryMapper",
    "normalize_category_name",
    # Re-exported so callers (API, tests) exercise §4.1 through one module.
    "split_monarch_category",
    "match_monarch_alias",
    "CATEGORY_TO_GROUP",
]
