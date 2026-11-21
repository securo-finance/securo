import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.category import CategoryRead

CategoryLineShareType = Literal["exact", "percent"]


class TransactionCategoryLineInput(BaseModel):
    """One part of a category split. `amount` is required for
    share_type='exact', `share_pct` for share_type='percent'."""

    category_id: uuid.UUID
    # Cents and hundredths of a percent, what the columns store: anything
    # finer would validate against one total and be saved as another.
    amount: Optional[Decimal] = Field(default=None, decimal_places=2)
    share_pct: Optional[Decimal] = Field(default=None, decimal_places=2)
    notes: Optional[str] = Field(default=None, max_length=500)


class TransactionCategoryLinesInput(BaseModel):
    """A whole category split, replacing whatever the transaction had.

    Same semantics as the group `splits` payload: leave the field out to
    keep the current lines, send two or more lines to split, send an empty
    list to undo the split (the transaction gets back the category of its
    largest line).
    """

    share_type: CategoryLineShareType
    lines: list[TransactionCategoryLineInput]


class TransactionCategoryLineRead(BaseModel):
    id: uuid.UUID
    category_id: uuid.UUID
    amount: Decimal
    share_type: str
    share_pct: Optional[Decimal] = None
    position: int
    notes: Optional[str] = None
    created_at: datetime
    # Embedded so lists can label each part without a second lookup.
    category: Optional[CategoryRead] = None

    model_config = ConfigDict(from_attributes=True)
