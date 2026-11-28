"""Manual order that a filtered view can change without moving what it hides."""

import uuid
from collections.abc import Sequence
from typing import Protocol, TypeVar


class _HasId(Protocol):
    id: uuid.UUID


T = TypeVar("T", bound=_HasId)


def place_in_order(rows: Sequence[T], ids: Sequence[uuid.UUID]) -> list[T] | None:
    """Return `rows` with the rows named in `ids` put in that order.

    The named rows trade places among the slots they already hold, so a row
    the caller did not name (hidden by a Collection filter, sold, archived)
    keeps its place. None when an id is not in `rows`.
    """
    by_id = {row.id: row for row in rows}
    if any(row_id not in by_id for row_id in ids):
        return None
    named = set(ids)
    in_new_order = iter(by_id[row_id] for row_id in ids)
    return [next(in_new_order) if row.id in named else row for row in rows]
