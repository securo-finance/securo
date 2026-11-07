"""Read-only price-change projections for recurring series.

Deliberately separate from the CRUD schemas in ``recurring_transaction.py``:
these are derived read models, recomputed from linked transaction history on
each request. Nothing here is persisted, and no route built on them writes to
the ledger.
"""
import uuid
from datetime import date as _Date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

RecurringPriceDirection = Literal["increase", "decrease"]
RecurringPriceChangeEligibility = Literal[
    "insufficient_history", "no_qualifying_change", "changes_detected"
]


class RecurringPriceChangeRead(BaseModel):
    """One detected transition between two clean observations."""

    recurring_id: uuid.UUID
    previous_transaction_id: uuid.UUID
    current_transaction_id: uuid.UUID
    previous_date: _Date
    current_date: _Date
    previous_amount: Decimal
    current_amount: Decimal
    currency: str
    direction: RecurringPriceDirection
    absolute_delta: Decimal
    percent_delta: Decimal
    gap_days: int
    baseline_occurrence_count: int


class RecurringPriceChangeHistoryRead(BaseModel):
    """Full history for one series: metadata plus every qualifying event."""

    recurring_id: uuid.UUID
    eligible_occurrence_count: int
    eligibility: RecurringPriceChangeEligibility
    # The history service reverses detector output to newest-first.
    changes: list[RecurringPriceChangeRead]
