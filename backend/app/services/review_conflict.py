"""Unactivated reviewer conflicts and strict direct-cause FK classifier."""

from collections.abc import Awaitable, Callable
from functools import wraps
from inspect import signature
from typing import ParamSpec, TypeVar

from asyncpg.exceptions import ForeignKeyViolationError
from sqlalchemy.dialects.postgresql.asyncpg import AsyncAdapt_asyncpg_dbapi
from sqlalchemy.exc import IntegrityError


class ReviewerDeleteRace(Exception):
    """A reviewer account deletion lost to a concurrent review."""

    def __init__(self) -> None:
        super().__init__("Cannot delete a user who reviewed another user's transactions")


class ReviewerAttributionRace(Exception):
    """A review lost to a concurrent reviewer account deletion."""

    def __init__(self) -> None:
        super().__init__("Review could not be saved because the reviewer account was deleted.")


def is_reviewer_fk_violation(exc: IntegrityError) -> bool:
    """Recognize only the named FK on the adapter's direct driver cause."""
    orig = getattr(exc, "orig", None)
    if not isinstance(orig, AsyncAdapt_asyncpg_dbapi.IntegrityError):
        return False
    if getattr(orig, "sqlstate", None) != "23503":
        return False
    cause = orig.__cause__
    return (
        isinstance(cause, ForeignKeyViolationError)
        and getattr(cause, "constraint_name", None) == "fk_transactions_reviewed_by_id"
    )


P = ParamSpec("P")
T = TypeVar("T")


def review_persistence(conflict: type[ReviewerDeleteRace] | type[ReviewerAttributionRace]):
    """Install rollback ownership across all flush/autoflush/commit surfaces.

    Wrap the committing owner, never the stamp helper or a commit in isolation.
    Unrelated IntegrityErrors retain their original object and traceback.
    """
    def decorate(function: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
        parameters = signature(function)

        @wraps(function)
        async def boundary(*args: P.args, **kwargs: P.kwargs) -> T:
            session = parameters.bind(*args, **kwargs).arguments["session"]
            try:
                return await function(*args, **kwargs)
            except IntegrityError as exc:
                if not is_reviewer_fk_violation(exc):
                    raise
                await session.rollback()
                raise conflict() from exc

        setattr(boundary, "__review_conflict__", conflict)
        return boundary

    return decorate
