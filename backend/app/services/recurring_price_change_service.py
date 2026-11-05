"""Derived, read-only price-change detection for linked recurring occurrences (PR-9).

Given one existing ``RecurringTransaction`` and its already-linked
``Transaction`` rows, detect a transition from a stable prior amount to a
materially different amount (packet §3). Pure and deterministic: the database
query (added separately) supplies candidates; nothing here writes, and the
signal is recomputed on every read (§3.6).

Contract summary, all from the packet:

- Eligibility (§3.2): workspace + series link, real-source allowlist
  (``sync``/``import``/``ofx``/``csv``/``manual``), ``posted``, not ignored, not a
  transfer leg, same direction and currency as the series, strictly positive
  amount. A generated ``source="recurring"`` placeholder is a schedule
  projection, not proof a merchant charged that amount.
- Normalization (§3.3): sort by ``(date, id)``; same-date rows with one
  amount collapse to one observation, same-date rows with different amounts
  mark the date ambiguous (omitted, chain resets at the next clean
  observation). Missing occurrences are never interpolated.
- Stability (§3.4): for a candidate ``current``, the baseline is
  ``[previous, prior_previous]``; it must be stable
  (``max - min <= 0.01``) and the candidate must differ from ``previous`` by
  more than ``0.01``. Exactly three clean observations is the minimum that
  fires (two alone = insufficient history; minimum-sample contract).
- Decimal discipline (§3.2): amounts are quantized to the model's two-decimal
  scale with ``ROUND_HALF_UP`` and never round-tripped through binary floats.
  The v1 noise unit is one persisted minor unit, ``Decimal("0.01")``.

The detector never uses the series' current ``amount`` as ``previous``: the
signal describes observed transaction history, not the current schedule
(§3.4, §5.9).
"""
import asyncio
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from sqlalchemy import select
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.schemas.recurring_price_change import (
    RecurringPriceChangeEligibility,
    RecurringPriceChangeHistoryRead,
    RecurringPriceChangeRead,
)

# Real-transaction sources a recurring charge can arrive under — the same
# allowlist the recurring matcher uses (recurring_match_service._REAL_SOURCES).
# Excludes "recurring" itself: a generated placeholder is not an observed
# charge (§3.2 rule 3, §5.5).
_REAL_SOURCES = ("sync", "import", "ofx", "csv", "manual")

#: One persisted minor unit under Numeric(15, 2): the v1 noise band and the
#: quantization scale (§3.2). A currency-minor-unit resolver must replace this
#: constant before any database scale other than two decimals is supported.
_NOISE_UNIT = Decimal("0.01")

#: Baseline window size kept in the event contract for auditability (§3.1).
BASELINE_OCCURRENCE_COUNT = 2

#: Two stable baseline observations plus the candidate (§3.4 rule 1, §7 Q1).
MINIMUM_CLEAN_OBSERVATIONS = BASELINE_OCCURRENCE_COUNT + 1


@dataclass(frozen=True)
class _SeriesEvidence:
    """Scalar closure of the detector's series reads; safe outside the session."""

    id: uuid.UUID
    workspace_id: uuid.UUID
    type: str
    currency: str


@dataclass(frozen=True)
class _OccurrenceEvidence:
    """No ORM state, relationships, schedule values or viewer-share amounts."""

    id: uuid.UUID
    workspace_id: uuid.UUID
    recurring_transaction_id: Optional[uuid.UUID]
    source: str
    status: str
    is_ignored: bool
    transfer_pair_id: Optional[uuid.UUID]
    type: str
    currency: str
    amount: Decimal
    date: date


_Series = RecurringTransaction | _SeriesEvidence
_Occurrence = Transaction | _OccurrenceEvidence


def _as_decimal(value: Decimal | float | str | int | None) -> Optional[Decimal]:
    """Quantize a persisted amount to two places, never via binary float."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        decimal_value = value
    elif isinstance(value, float):
        # Defensive: the ORM hands back Decimal, but if a float ever arrives
        # go through its decimal string, not its binary expansion.
        decimal_value = Decimal(str(value))
    else:
        decimal_value = Decimal(value)
    return decimal_value.quantize(_NOISE_UNIT, rounding=ROUND_HALF_UP)


def is_eligible_occurrence(series: _Series, tx: _Occurrence) -> bool:
    """Return True when ``tx`` is price-change evidence for ``series`` (§3.2)."""
    if tx.workspace_id != series.workspace_id:
        return False
    if tx.recurring_transaction_id != series.id:
        return False
    if tx.source not in _REAL_SOURCES:
        return False
    if tx.status != "posted":
        return False
    if tx.is_ignored:
        return False
    if tx.transfer_pair_id is not None:
        return False
    if tx.type != series.type:
        return False
    if tx.currency != series.currency:
        return False
    amount = _as_decimal(tx.amount)
    # Zero or non-positive amounts are excluded: no percentage can be
    # computed from zero (§3.2 rule 9, §5.6).
    return amount is not None and amount > 0


@dataclass(frozen=True)
class _Observation:
    """One clean observation: a normalized amount on a single date."""

    when: date
    amount: Decimal
    transaction_id: uuid.UUID


def _normalize(
    series: _Series, rows: Sequence[_Occurrence]
) -> list[Optional[_Observation]]:
    """Apply eligibility, ordering, and same-date rules (§3.3).

    Returns clean observations in chronological order; ``None`` marks an
    ambiguous date (same-date rows with different amounts), which splits the
    signal chain so no event can span it.
    """
    eligible = [tx for tx in rows if is_eligible_occurrence(series, tx)]
    # (date ASC, id ASC): deterministic even when the caller's order is not.
    eligible.sort(key=lambda tx: (tx.date, tx.id))

    observations: list[Optional[_Observation]] = []
    index = 0
    while index < len(eligible):
        group_end = index
        while group_end < len(eligible) and eligible[group_end].date == eligible[index].date:
            group_end += 1
        group = eligible[index:group_end]
        amounts = {amount for amount in (_as_decimal(tx.amount) for tx in group) if amount is not None}
        if len(amounts) == 1:
            # Identical same-date evidence collapses to one observation;
            # the group is id-sorted, so the representative is its lowest id.
            first = group[0]
            observations.append(
                _Observation(when=first.date, amount=amounts.pop(), transaction_id=first.id)
            )
        else:
            # Different amounts on one date: ambiguous, never guessed (§3.3).
            observations.append(None)
        index = group_end
    return observations


def _segments(
    observations: list[Optional[_Observation]],
) -> list[list[_Observation]]:
    """Split the chain at ambiguous dates so no event spans a reset."""
    segments: list[list[_Observation]] = []
    current: list[_Observation] = []
    for observation in observations:
        if observation is None:
            if current:
                segments.append(current)
            current = []
        else:
            current.append(observation)
    if current:
        segments.append(current)
    return segments


def _build_event(
    series: _Series,
    previous: _Observation,
    current: _Observation,
    delta: Decimal,
) -> RecurringPriceChangeRead:
    absolute_delta = abs(delta).quantize(_NOISE_UNIT, rounding=ROUND_HALF_UP)
    percent_delta = (
        (current.amount - previous.amount) / previous.amount * Decimal("100")
    ).quantize(_NOISE_UNIT, rounding=ROUND_HALF_UP)
    return RecurringPriceChangeRead(
        recurring_id=series.id,
        previous_transaction_id=previous.transaction_id,
        current_transaction_id=current.transaction_id,
        previous_date=previous.when,
        current_date=current.when,
        previous_amount=previous.amount,
        current_amount=current.amount,
        currency=series.currency,
        direction="increase" if delta > 0 else "decrease",
        absolute_delta=absolute_delta,
        percent_delta=percent_delta,
        gap_days=(current.when - previous.when).days,
        baseline_occurrence_count=BASELINE_OCCURRENCE_COUNT,
    )


def _detect_events(
    series: _Series, observations: list[Optional[_Observation]]
) -> list[RecurringPriceChangeRead]:
    events: list[RecurringPriceChangeRead] = []
    for segment in _segments(observations):
        for i in range(MINIMUM_CLEAN_OBSERVATIONS - 1, len(segment)):
            prior_previous = segment[i - 2]
            previous = segment[i - 1]
            current = segment[i]
            baseline = (prior_previous.amount, previous.amount)
            if max(baseline) - min(baseline) > _NOISE_UNIT:
                continue
            delta = current.amount - previous.amount
            if abs(delta) <= _NOISE_UNIT:
                continue
            events.append(_build_event(series, previous, current, delta))
    return events


def detect_price_changes(
    series: _Series, rows: Sequence[_Occurrence]
) -> list[RecurringPriceChangeRead]:
    """Return every qualifying price change, chronological (oldest first).

    For each candidate ``current`` at clean index ``i >= 2``: the baseline
    ``[i-2, i-1]`` must be stable and ``current`` must differ from ``previous``
    by more than the noise unit; otherwise no event is emitted and no baseline
    is manufactured from the candidate (§3.4). The series' current ``amount``
    is never consulted.
    """
    return _detect_events(series, _normalize(series, rows))


def count_clean_observations(series: _Series, rows: Sequence[_Occurrence]) -> int:
    """Number of clean observations available to the signal chain (§3.3).

    Collapsed same-date duplicates count once; ambiguous dates count zero —
    they are eligible rows but never chain candidates.
    """
    return sum(1 for observation in _normalize(series, rows) if observation is not None)


def classify_eligibility(
    eligible_occurrence_count: int, events: list[RecurringPriceChangeRead]
) -> RecurringPriceChangeEligibility:
    """Map observation count + events to the §3.7 eligibility union."""
    if eligible_occurrence_count < MINIMUM_CLEAN_OBSERVATIONS:
        return "insufficient_history"
    if events:
        return "changes_detected"
    return "no_qualifying_change"


@dataclass
class PriceChangeAnalysis:
    """Everything one read of a series' history needs, normalized once."""

    events: list[RecurringPriceChangeRead]  # chronological, oldest first
    eligible_occurrence_count: int
    eligibility: RecurringPriceChangeEligibility


def analyze_price_changes(
    series: _Series, rows: Sequence[_Occurrence]
) -> PriceChangeAnalysis:
    """Single-pass analysis: normalize once, then derive events and count."""
    observations = _normalize(series, rows)
    events = _detect_events(series, observations)
    count = sum(1 for observation in observations if observation is not None)
    return PriceChangeAnalysis(
        events=events,
        eligible_occurrence_count=count,
        eligibility=classify_eligibility(count, events),
    )


# ---------------------------------------------------------------------------
# Workspace-scoped queries (T2). The SQL predicates mirror §3.2 to cut rows
# early; the detector re-checks every condition in Python, so the pure
# predicate stays the single authority. Read-only: no writes, no lazy loads.
# ---------------------------------------------------------------------------


async def get_price_history(
    session: AsyncSession, recurring_id: uuid.UUID, workspace_id: uuid.UUID
) -> Optional[RecurringPriceChangeHistoryRead]:
    """Full price history for one series, or None when unknown/out-of-workspace.

    Returns the response with ``changes`` newest-first per §3.7.
    """
    series_result = await session.execute(
        select(RecurringTransaction).where(
            RecurringTransaction.id == recurring_id,
            RecurringTransaction.workspace_id == workspace_id,
        )
    )
    series = series_result.scalar_one_or_none()
    if series is None:
        return None

    rows_result = await session.execute(
        select(Transaction)
        .where(
            Transaction.recurring_transaction_id == series.id,
            Transaction.workspace_id == series.workspace_id,
            Transaction.source.in_(_REAL_SOURCES),
            Transaction.status == "posted",
            Transaction.is_ignored.is_(False),
            Transaction.transfer_pair_id.is_(None),
            Transaction.type == series.type,
            Transaction.currency == series.currency,
            Transaction.amount > 0,
        )
        .order_by(Transaction.date, Transaction.id)
    )
    rows = list(rows_result.scalars().all())
    analysis = analyze_price_changes(series, rows)
    return RecurringPriceChangeHistoryRead(
        recurring_id=series.id,
        eligible_occurrence_count=analysis.eligible_occurrence_count,
        eligibility=analysis.eligibility,
        changes=list(reversed(analysis.events)),
    )


async def get_recent_price_changes(
    session: AsyncSession, workspace_id: uuid.UUID, limit: int = 20
) -> list[RecurringPriceChangeRead]:
    """Latest qualifying event per series in the workspace, newest first (§3.7).

    One join query scoped by the series workspace: a linked row from another
    workspace cannot leak. ``limit`` is applied after latest-per-series
    selection (callers bound it to 1–50 at the route).
    """
    result = await session.execute(
        # Keep this scalar projection in the evidence dataclasses' field order.
        # In particular, do not materialize raw_data or any ORM relationships.
        select(
            RecurringTransaction.id,
            RecurringTransaction.workspace_id,
            RecurringTransaction.type,
            RecurringTransaction.currency,
            Transaction.id,
            Transaction.workspace_id,
            Transaction.recurring_transaction_id,
            Transaction.source,
            Transaction.status,
            Transaction.is_ignored,
            Transaction.transfer_pair_id,
            Transaction.type,
            Transaction.currency,
            Transaction.amount,
            Transaction.date,
        )
        .join(
            Transaction,
            Transaction.recurring_transaction_id == RecurringTransaction.id,
        )
        .where(
            RecurringTransaction.workspace_id == workspace_id,
            Transaction.workspace_id == RecurringTransaction.workspace_id,
            Transaction.source.in_(_REAL_SOURCES),
            Transaction.status == "posted",
            Transaction.is_ignored.is_(False),
            Transaction.transfer_pair_id.is_(None),
            Transaction.type == RecurringTransaction.type,
            Transaction.currency == RecurringTransaction.currency,
            Transaction.amount > 0,
        )
        .order_by(RecurringTransaction.id, Transaction.date, Transaction.id)
    )

    # Only detached scalar rows cross the thread boundary, never the session
    # or an ORM entity. Grouping, normalization and event selection are pure.
    return await asyncio.to_thread(_recent_from_evidence, result.all(), limit)


def _recent_from_evidence(rows: Sequence[Row], limit: int) -> list[RecurringPriceChangeRead]:
    grouped: dict[uuid.UUID, tuple[_SeriesEvidence, list[_OccurrenceEvidence]]] = {}
    for row in rows:
        series_id = row[0]
        if series_id not in grouped:
            grouped[series_id] = (_SeriesEvidence(*row[:4]), [])
        grouped[series_id][1].append(_OccurrenceEvidence(*row[4:]))

    latest: list[RecurringPriceChangeRead] = []
    for series, occurrences in grouped.values():
        analysis = analyze_price_changes(series, occurrences)
        if not analysis.events:
            continue
        newest = max(
            analysis.events,
            key=lambda event: (event.current_date, event.current_transaction_id),
        )
        latest.append(newest)
    # Newest current_date first; recurring_id keeps ties deterministic.
    latest.sort(key=lambda event: (-event.current_date.toordinal(), event.recurring_id))
    return latest[:limit]
