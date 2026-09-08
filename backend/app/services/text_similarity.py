"""How alike two descriptions are, in one place.

Token overlap over the longer side. It lived in `connection_service`,
where the bank-sync fuzzy merge tuned it, and was reproduced inside the
matching engine because that module is pure: it holds no session and
imports no service, so it could not reach across to a module that pulls in
models and SQLAlchemy.

Two copies of one formula drift, and this one is a threshold people tune
against. So it moves here instead: a function with no dependencies, which
the pure engine may import without giving up what makes it pure.

It is deliberately unforgiving. A bank string and a hand-typed one rarely
share tokens exactly, which is why the exact-amount signal carries the
weight and this only guards against two promises of the same value.
"""
from __future__ import annotations

from typing import Optional


def token_overlap(a: Optional[str], b: Optional[str]) -> float:
    """0 when nothing is shared, 1 when the words are the same set."""
    if not a or not b:
        return 0.0
    tokens_a = {token for token in a.lower().split() if token}
    tokens_b = {token for token in b.lower().split() if token}
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / max(len(tokens_a), len(tokens_b))
