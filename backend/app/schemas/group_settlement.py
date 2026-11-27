import uuid
from datetime import date as _Date, datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class GroupSettlementBase(BaseModel):
    from_member_id: uuid.UUID
    to_member_id: uuid.UUID
    amount: Decimal = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    date: _Date
    transaction_id: Optional[uuid.UUID] = None
    # The receiver's own credit that already arrived, instead of a synthetic one.
    receiver_transaction_id: Optional[uuid.UUID] = None
    notes: Optional[str] = None

    @model_validator(mode="after")
    def _distinct_members(self):
        if self.from_member_id == self.to_member_id:
            raise ValueError("from_member_id and to_member_id must differ")
        return self


class GroupSettlementCreate(GroupSettlementBase):
    # When provided, also creates a debit transaction on this account
    # for the settlement amount and links it via `transaction_id`. The
    # account must belong to the requesting user, and the user must be
    # the `from_member` (the payer). Mutually exclusive with passing
    # `transaction_id` directly.
    account_id: Optional[uuid.UUID] = None
    description: Optional[str] = None


class GroupSettlementUpdate(BaseModel):
    from_member_id: Optional[uuid.UUID] = None
    to_member_id: Optional[uuid.UUID] = None
    amount: Optional[Decimal] = Field(default=None, gt=0)
    currency: Optional[str] = Field(default=None, min_length=3, max_length=3)
    date: Optional[_Date] = None
    transaction_id: Optional[uuid.UUID] = None
    notes: Optional[str] = None


class GroupSettlementRead(GroupSettlementBase):
    id: uuid.UUID
    group_id: uuid.UUID
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
