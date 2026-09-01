"""What the matching rules and the doubtful queue look like over HTTP."""
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class RuleWindow(BaseModel):
    """How far from the expected day a movement may land.

    Asymmetric because money arrives after a due date far more often than
    before it, and the two sides are measured from different anchors: late
    from the due date, early from the day the promise was written.
    """

    before_days: int = Field(ge=0, le=365)
    after_days: int = Field(ge=0, le=365)


class ReconciliationRuleRead(BaseModel):
    """One rule as the page shows it — shipped, changed, or the workspace's own.

    `customised` is what lets the screen say "you changed this" and offer
    to put it back, without the client having to know what we ship.
    """

    id: str
    node: str
    name: Optional[str] = None
    origin: str
    customised: bool
    enabled: bool
    outcome: str
    when: dict[str, Any]
    position: int


class ReconciliationNodeRead(BaseModel):
    """A whole set of rules, in the order they are tried."""

    node: str
    #: Whether this set is reachable at all for this workspace. The invoice
    #: rules mean nothing where the module is off, and showing them as if
    #: they were live would be a lie the page tells every time it loads.
    active: bool
    rules: list[ReconciliationRuleRead]


class ReconciliationRuleUpdate(BaseModel):
    """A change to a rule we ship. Every field optional: what is not sent
    stays as shipped, and keeps improving when we improve it."""

    enabled: Optional[bool] = None
    outcome: Optional[str] = None
    when: Optional[dict[str, Any]] = None
    position: Optional[int] = None


class ReconciliationRuleCreate(BaseModel):
    """A rule the workspace writes itself."""

    node: str
    name: str = Field(min_length=1, max_length=120)
    outcome: str
    when: dict[str, Any]
    enabled: bool = True
    position: Optional[int] = None


class SuggestionTransactionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    description: Optional[str] = None
    amount: Decimal
    currency: Optional[str] = None
    date: date
    type: str


class SuggestionRead(BaseModel):
    """One open question, with the evidence behind it.

    `scores` is the per-signal breakdown rather than one number, because
    "we are 78% sure" is not something anyone can check, while "the amount
    is exact and the date is four days out" is.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    node: str
    strategy_id: str
    expectation_kind: str
    expectation_id: uuid.UUID
    #: What the promise is called, resolved for display: an invoice number
    #: and client, or the recurring bill's description.
    expectation_label: Optional[str] = None
    amount: Decimal
    scores: dict[str, Any]
    status: str
    created_at: datetime
    transaction: Optional[SuggestionTransactionRead] = None
