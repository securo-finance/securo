"""Scopes an access token can carry, and which API routes each one opens.

A scope is `<resource>:read` or `<resource>:write`; write includes read.
Read covers GET and HEAD, write covers every other method.

The route map is deny by default: an access token can only reach a path
listed in `_ROUTE_RESOURCES` (or `_OPEN_TO_ANY_TOKEN`). Everything else
(account settings, workspace membership, admin, minting more tokens) stays
reachable only from a signed-in session, so a leaked token can never widen
its own access.

Scopes only narrow what the member who created the token can do: every
request still runs with that member's current role in the workspace.
"""
from __future__ import annotations

from typing import Optional

READ = "read"
WRITE = "write"

# Resource -> API path prefixes it covers. Longer prefixes win, so a nested
# path can belong to a different resource than its parent.
_ROUTE_RESOURCES: dict[str, tuple[str, ...]] = {
    "accounts": ("/api/accounts",),
    "connections": ("/api/connections",),
    "transactions": (
        "/api/transactions",
        "/api/mortgage-payments",
        "/api/import-logs",
        "/api/reconciliation",
    ),
    "categories": ("/api/categories", "/api/category-groups"),
    "payees": ("/api/payees",),
    "rules": ("/api/rules",),
    "budgets": ("/api/budgets",),
    "goals": ("/api/goals",),
    "recurring": ("/api/recurring-transactions",),
    "assets": ("/api/assets", "/api/asset-groups"),
    "reports": ("/api/reports", "/api/dashboard"),
}

# Resources whose routes only ever read: a `:write` scope is meaningless.
_READ_ONLY_RESOURCES = frozenset({"reports"})

# Reference data any token may read, whatever its scopes.
_OPEN_TO_ANY_TOKEN: tuple[str, ...] = ("/api/currencies", "/api/timezones")

RESOURCES: tuple[str, ...] = tuple(_ROUTE_RESOURCES)

ALL_SCOPES: tuple[str, ...] = tuple(
    f"{resource}:{level}"
    for resource in RESOURCES
    for level in ((READ,) if resource in _READ_ONLY_RESOURCES else (READ, WRITE))
)

_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def is_write_scope(scope: str) -> bool:
    return scope.endswith(f":{WRITE}")


def _matches(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


def _resource_for(path: str) -> Optional[str]:
    best: Optional[tuple[int, str]] = None
    for resource, prefixes in _ROUTE_RESOURCES.items():
        for prefix in prefixes:
            if _matches(path, prefix) and (best is None or len(prefix) > best[0]):
                best = (len(prefix), resource)
    return best[1] if best else None


def required_scope(method: str, path: str) -> Optional[str]:
    """The scope a token needs for this request, or None if no token may.

    Returns "" for reference data any token may read.
    """
    is_read = method.upper() in _READ_METHODS
    if is_read and any(_matches(path, p) for p in _OPEN_TO_ANY_TOKEN):
        return ""
    resource = _resource_for(path)
    if resource is None:
        return None
    if resource in _READ_ONLY_RESOURCES and not is_read:
        return None
    return f"{resource}:{READ if is_read else WRITE}"


def grants(scopes: list[str], needed: str) -> bool:
    """Whether `scopes` covers `needed` (write includes read)."""
    if needed == "":
        return True
    if needed in scopes:
        return True
    resource, _, level = needed.partition(":")
    return level == READ and f"{resource}:{WRITE}" in scopes
