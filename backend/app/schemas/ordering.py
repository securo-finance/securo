import uuid
from typing import Annotated

from pydantic import AfterValidator, Field


def _unique(ids: list[uuid.UUID]) -> list[uuid.UUID]:
    if len(ids) != len(set(ids)):
        raise ValueError("ids must be unique")
    return ids


# The ids of a list in the order someone put them, as a reorder request sends them.
OrderedIds = Annotated[list[uuid.UUID], Field(min_length=1, max_length=1000), AfterValidator(_unique)]
