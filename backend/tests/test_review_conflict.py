"""Pure synthetic chains using installed exceptions; no driver connections."""

from typing import cast

import pytest
from asyncpg.exceptions import ForeignKeyViolationError, UniqueViolationError
from sqlalchemy.dialects.postgresql.asyncpg import AsyncAdapt_asyncpg_dbapi
from sqlalchemy.exc import IntegrityError

from app.services.review_conflict import (
    ReviewerAttributionRace,
    ReviewerDeleteRace,
    is_reviewer_fk_violation,
)

CONSTRAINT = "fk_transactions_reviewed_by_id"
CASES = [
    ("direct-target", True),
    ("wrong-sqlstate", False),
    ("missing-sqlstate", False),
    ("mistyped-sqlstate", False),
    ("wrong-constraint", False),
    ("constraintless-cause", False),
    ("mistyped-constraint", False),
    ("none-orig", False),
    ("missing-orig", False),
    ("mistyped-orig", False),
    ("driver-orig", False),
    ("non-integrity-dialect-orig", False),
    ("missing-cause", False),
    ("non-fk-cause", False),
    ("context-only-trap", False),
    ("deeper-target-trap", False),
    ("orig-constraint-trap", False),
    ("pgcode-only-trap", False),
]


def make_chain(case: str) -> tuple[IntegrityError, list[BaseException]]:
    target = ForeignKeyViolationError("synthetic target")
    setattr(target, "constraint_name", CONSTRAINT)
    other = UniqueViolationError("synthetic non-FK")
    setattr(other, "constraint_name", CONSTRAINT)
    adapter = AsyncAdapt_asyncpg_dbapi.IntegrityError("synthetic adapter")
    setattr(adapter, "sqlstate", "23503")
    adapter.__cause__ = target
    orig: Exception | None = adapter
    if case == "wrong-sqlstate":
        setattr(adapter, "sqlstate", "23505")
    elif case == "missing-sqlstate":
        delattr(adapter, "sqlstate")
    elif case == "mistyped-sqlstate":
        setattr(adapter, "sqlstate", 23503)
    elif case == "wrong-constraint":
        setattr(target, "constraint_name", "fk_transactions_user_id")
    elif case == "constraintless-cause":
        delattr(target, "constraint_name")
    elif case == "mistyped-constraint":
        setattr(target, "constraint_name", 23503)
    elif case == "none-orig":
        orig = None
    elif case == "mistyped-orig":
        orig = RuntimeError("synthetic wrong orig")
        setattr(orig, "sqlstate", "23503")
        setattr(orig, "constraint_name", CONSTRAINT)
        orig.__cause__ = target
    elif case == "driver-orig":
        orig = target
    elif case == "non-integrity-dialect-orig":
        orig = AsyncAdapt_asyncpg_dbapi.Error("synthetic wrong dialect type")
        setattr(orig, "sqlstate", "23503")
        setattr(orig, "constraint_name", CONSTRAINT)
        orig.__cause__ = target
    elif case == "missing-cause":
        adapter.__cause__ = None
    elif case == "non-fk-cause":
        adapter.__cause__ = other
    elif case == "context-only-trap":
        adapter.__cause__ = None
        adapter.__context__ = target
    elif case == "deeper-target-trap":
        adapter.__cause__ = other
        other.__cause__ = target
    elif case == "orig-constraint-trap":
        setattr(adapter, "constraint_name", CONSTRAINT)
        setattr(target, "constraint_name", "unrelated_constraint")
    elif case == "pgcode-only-trap":
        delattr(adapter, "sqlstate")
        setattr(adapter, "pgcode", "23503")
    outer = IntegrityError("synthetic statement", {}, cast(Exception, orig))
    if case == "missing-orig":
        delattr(outer, "orig")
    objects: list[BaseException] = [outer, adapter, target, other]
    if orig is not None and orig is not adapter and orig is not target:
        objects.append(orig)
    return outer, objects


def snapshot(exc: BaseException) -> tuple[object, ...]:
    return (
        id(exc), dict(vars(exc)), exc.args, exc.__cause__, exc.__context__,
        exc.__suppress_context__, exc.__traceback__,
    )


@pytest.mark.parametrize("case, expected", CASES, ids=[case for case, _ in CASES])
def test_classifier(case: str, expected: bool) -> None:
    outer, objects = make_chain(case)
    before = [snapshot(exc) for exc in objects]
    first = is_reviewer_fk_violation(outer)
    second = is_reviewer_fk_violation(outer)
    assert [snapshot(exc) for exc in objects] == before
    assert first is expected
    assert second is expected


@pytest.mark.parametrize(
    "kind, message",
    [
        (ReviewerDeleteRace, "Cannot delete a user who reviewed another user's transactions"),
        (ReviewerAttributionRace, "Review could not be saved because the reviewer account was deleted."),
    ],
    ids=["delete-message", "attribution-message"],
)
def test_typed_message(kind: type[Exception], message: str) -> None:
    conflict = kind()
    assert type(conflict) is kind
    assert str(conflict) == message
    assert conflict.args == (message,)
    assert ReviewerDeleteRace is not ReviewerAttributionRace
