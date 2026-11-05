"""Pure detection vectors for recurring price-change detection (PR-9 §6.1).

Database-light by design: the helper builds a series-like ORM object and
ordered ``Transaction`` rows in memory, so every vector here is deterministic
and exercises the exact eligibility/normalization/stability contract without
a session. Named cases are the packet's §6.1 table, as amended by §12
(three clean observations is the minimum that fires; two is insufficient).
"""
import calendar
import random
import time
import uuid
from datetime import date
from decimal import Decimal

from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.models.transaction_split import TransactionSplit
from app.models.workspace import Workspace
from app.services.recurring_price_change_service import (
    classify_eligibility,
    count_clean_observations,
    detect_price_changes,
    get_price_history,
    get_recent_price_changes,
)

_START = date(2026, 1, 15)


def _series(**overrides) -> RecurringTransaction:
    fields = dict(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        description="Netflix",
        amount=Decimal("15.00"),
        currency="BRL",
        type="debit",
        frequency="monthly",
        start_date=_START,
        next_occurrence=date(2026, 5, 15),
    )
    fields.update(overrides)
    return RecurringTransaction(**fields)


def _tx(series: RecurringTransaction, amount: str, when: date, **overrides) -> Transaction:
    fields = dict(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        workspace_id=series.workspace_id,
        account_id=uuid.uuid4(),
        description="NETFLIX",
        amount=Decimal(amount),
        currency=series.currency,
        date=when,
        type=series.type,
        source="csv",
        status="posted",
        recurring_transaction_id=series.id,
    )
    fields.update(overrides)
    return Transaction(**fields)


def _add_months(when: date, months: int) -> date:
    month_index = when.month - 1 + months
    year = when.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, min(when.day, calendar.monthrange(year, month)[1]))


def _monthly_rows(
    series: RecurringTransaction, amounts: list[str], start: date = _START
) -> list[Transaction]:
    return [_tx(series, amount, _add_months(start, i)) for i, amount in enumerate(amounts)]


async def _persist(session, series: RecurringTransaction, rows: list[Transaction]) -> None:
    """Put a series and its linked rows in the session without committing."""
    session.add(series)
    session.add_all(rows)
    await session.flush()


# ---------------------------------------------------------------------------
# §6.1 named vectors
# ---------------------------------------------------------------------------


def test_price_change_stable_monthly_15_to_17():
    series = _series()
    rows = _monthly_rows(series, ["15.00", "15.00", "15.00", "17.00"])

    events = detect_price_changes(series, rows)

    assert len(events) == 1
    event = events[0]
    assert event.direction == "increase"
    assert event.previous_amount == Decimal("15.00")
    assert event.current_amount == Decimal("17.00")
    assert event.absolute_delta == Decimal("2.00")
    assert event.percent_delta == Decimal("13.33")
    assert event.currency == "BRL"
    assert event.baseline_occurrence_count == 2
    # The change is observed at the first 17.00, not the last row.
    assert event.current_transaction_id == rows[3].id
    assert event.previous_transaction_id == rows[2].id


def test_price_change_stable_monthly_17_to_15():
    series = _series()
    rows = _monthly_rows(series, ["17.00", "17.00", "17.00", "15.00"])

    events = detect_price_changes(series, rows)

    assert len(events) == 1
    event = events[0]
    assert event.direction == "decrease"
    assert event.previous_amount == Decimal("17.00")
    assert event.current_amount == Decimal("15.00")
    assert event.absolute_delta == Decimal("2.00")
    assert event.percent_delta == Decimal("-11.76")


def test_price_change_requires_three_clean_observations():
    """Exactly three clean observations is the documented minimum that fires
    (§3.1, §3.4 rule 1, §7 Q1; Juiz gate amendment 2026-09-23)."""
    series = _series()
    rows = _monthly_rows(series, ["10.00", "10.00", "11.00"])

    events = detect_price_changes(series, rows)

    assert len(events) == 1
    event = events[0]
    assert event.direction == "increase"
    assert event.previous_amount == Decimal("10.00")
    assert event.current_amount == Decimal("11.00")
    assert event.absolute_delta == Decimal("1.00")
    assert event.percent_delta == Decimal("10.00")
    assert count_clean_observations(series, rows) == 3


def test_price_change_two_observations_are_insufficient():
    """Two linked occurrences = insufficient history (§3.4; Juiz gate
    amendment 2026-09-23)."""
    series = _series()
    rows = _monthly_rows(series, ["10.00", "11.00"])

    events = detect_price_changes(series, rows)
    count = count_clean_observations(series, rows)

    assert events == []
    assert count == 2
    assert classify_eligibility(count, events) == "insufficient_history"


def test_price_change_ignores_one_cent_rounding():
    series = _series()
    rows = _monthly_rows(series, ["39.90", "39.90", "39.90", "39.91"])

    events = detect_price_changes(series, rows)
    count = count_clean_observations(series, rows)

    assert events == []
    assert classify_eligibility(count, events) == "no_qualifying_change"


def test_price_change_emits_once_for_sustained_new_level():
    series = _series()
    rows = _monthly_rows(series, ["39.90", "39.90", "39.90", "42.90", "42.90", "42.90"])

    events = detect_price_changes(series, rows)

    assert len(events) == 1
    event = events[0]
    assert event.previous_amount == Decimal("39.90")
    assert event.current_amount == Decimal("42.90")
    assert event.current_transaction_id == rows[3].id  # first 42.90 only


def test_price_change_does_not_realert_on_transient_return():
    series = _series()
    amounts = ["10.00", "10.00", "10.00", "12.00", "10.00", "10.00", "10.00", "12.00"]
    rows = _monthly_rows(series, amounts)

    events = detect_price_changes(series, rows)

    # Only the first and final stable transitions: the dip back to 10.00 has
    # an unstable baseline and never manufactures one.
    assert len(events) == 2
    assert [event.direction for event in events] == ["increase", "increase"]
    assert events[0].current_transaction_id == rows[3].id
    assert events[1].current_transaction_id == rows[7].id
    assert events[1].previous_amount == Decimal("10.00")
    assert events[1].current_amount == Decimal("12.00")


def test_variable_utility_history_is_not_flagged():
    series = _series()
    rows = _monthly_rows(series, ["80.00", "100.00", "90.00", "110.00"])

    events = detect_price_changes(series, rows)
    count = count_clean_observations(series, rows)

    assert events == []
    assert count == 4  # baseline never stable, but all rows are clean observations
    assert classify_eligibility(count, events) == "no_qualifying_change"


def test_stable_new_level_resets_future_detection():
    series = _series()
    rows = _monthly_rows(series, ["10.00", "10.00", "10.00", "12.00", "12.00", "12.00", "15.00"])

    events = detect_price_changes(series, rows)

    assert len(events) == 2
    assert (
        events[0].previous_amount,
        events[0].current_amount,
        events[0].direction,
    ) == (Decimal("10.00"), Decimal("12.00"), "increase")
    assert (
        events[1].previous_amount,
        events[1].current_amount,
        events[1].direction,
    ) == (Decimal("12.00"), Decimal("15.00"), "increase")


def test_same_date_identical_rows_collapse():
    series = _series()
    day1, day2, day3 = date(2026, 1, 15), date(2026, 2, 15), date(2026, 3, 15)

    # Two identical rows on one date are one observation: 10 -> 11 with only
    # two clean observations is insufficient. Left uncollapsed, the duplicate
    # would fake a third observation and fire an event.
    collapsed = [
        _tx(series, "10.00", day1),
        _tx(series, "10.00", day1),
        _tx(series, "11.00", day2),
    ]
    assert count_clean_observations(series, collapsed) == 2
    assert detect_price_changes(series, collapsed) == []

    # With genuine history, the collapse still leaves a valid chain.
    with_history = [
        _tx(series, "10.00", day1),
        _tx(series, "10.00", day2),
        _tx(series, "10.00", day2),
        _tx(series, "11.00", day3),
    ]
    events = detect_price_changes(series, with_history)
    assert len(events) == 1
    assert events[0].previous_amount == Decimal("10.00")
    assert events[0].current_amount == Decimal("11.00")


def test_same_date_duplicates_use_lowest_uuid_for_both_evidence_ids():
    series = _series()
    previous_low = uuid.UUID("00000000-0000-0000-0000-000000000001")
    previous_high = uuid.UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")
    current_low = uuid.UUID("00000000-0000-0000-0000-000000000002")
    current_high = uuid.UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee")
    # Reverse UUID order within each date defeats date-only stable sorting.
    rows = [
        _tx(series, "10.00", date(2026, 1, 15)),
        _tx(series, "10.00", date(2026, 2, 15), id=previous_high),
        _tx(series, "10.00", date(2026, 2, 15), id=previous_low),
        _tx(series, "11.00", date(2026, 3, 15), id=current_high),
        _tx(series, "11.00", date(2026, 3, 15), id=current_low),
    ]
    events = detect_price_changes(series, rows)
    assert count_clean_observations(series, rows) == 3
    assert len(events) == 1
    assert events[0].previous_transaction_id == previous_low
    assert events[0].current_transaction_id == current_low
    assert detect_price_changes(series, list(reversed(rows))) == events


def test_same_date_different_amounts_break_chain():
    series = _series()
    rows = [
        _tx(series, "10.00", date(2026, 1, 15)),
        _tx(series, "10.00", date(2026, 2, 15)),
        _tx(series, "10.00", date(2026, 3, 15)),
        _tx(series, "10.00", date(2026, 4, 15)),  # ambiguous date: {10, 12}
        _tx(series, "12.00", date(2026, 4, 15)),
        _tx(series, "12.00", date(2026, 5, 15)),
    ]

    events = detect_price_changes(series, rows)
    count = count_clean_observations(series, rows)

    # The ambiguous date resets the chain: no event spans it, and the single
    # clean observation after it cannot form a baseline.
    assert events == []
    assert count == 4
    assert classify_eligibility(count, events) == "no_qualifying_change"


def test_currency_change_is_excluded():
    series = _series(currency="BRL")
    rows = _monthly_rows(series, ["10.00", "10.00", "10.00"])
    rows.append(_tx(series, "12.00", _add_months(_START, 3), currency="USD"))

    events = detect_price_changes(series, rows)

    assert events == []
    assert count_clean_observations(series, rows) == 3


def test_pending_and_recurring_placeholder_rows_are_excluded():
    series = _series()
    rows = _monthly_rows(series, ["10.00", "10.00", "12.00", "12.00"])
    rows[2] = _tx(series, "12.00", rows[2].date, status="pending")
    rows[3] = _tx(series, "12.00", rows[3].date, source="recurring")

    events = detect_price_changes(series, rows)

    assert events == []
    assert count_clean_observations(series, rows) == 2
    assert classify_eligibility(2, events) == "insufficient_history"


def test_credit_refund_on_debit_series_is_excluded():
    series = _series(type="debit")
    rows = _monthly_rows(series, ["10.00", "10.00", "10.00"])
    rows.append(_tx(series, "10.00", _add_months(_START, 3), type="credit"))

    events = detect_price_changes(series, rows)

    assert events == []
    assert count_clean_observations(series, rows) == 3


def test_split_parent_uses_gross_amount():
    series = _series()
    rows = _monthly_rows(series, ["60.00", "60.00", "60.00", "66.00"])
    # A shared-expense settlement exists on the price-changed parent; the
    # detector must read the merchant gross, never viewer-share math (§5.2).
    TransactionSplit(
        transaction_id=rows[3].id,
        workspace_id=series.workspace_id,
        group_member_id=uuid.uuid4(),
        share_amount=Decimal("33.00"),
    )

    events = detect_price_changes(series, rows)

    assert len(events) == 1
    event = events[0]
    assert event.previous_amount == Decimal("60.00")
    assert event.current_amount == Decimal("66.00")
    assert event.absolute_delta == Decimal("6.00")
    assert event.percent_delta == Decimal("10.00")


def test_ignored_transfer_and_opening_balance_rows_are_excluded():
    series = _series()
    rows = _monthly_rows(series, ["10.00", "10.00", "10.00"])
    rows.append(_tx(series, "12.00", _add_months(_START, 3), is_ignored=True))
    rows.append(
        _tx(series, "12.00", _add_months(_START, 4), transfer_pair_id=uuid.uuid4())
    )
    rows.append(_tx(series, "12.00", _add_months(_START, 5), source="opening_balance"))

    events = detect_price_changes(series, rows)

    # If any excluded row entered the chain, [10, 10, 10, 12, 12, 12] would
    # fire an increase at the first 12.00.
    assert events == []
    assert count_clean_observations(series, rows) == 3


def test_gap_is_reported_without_interpolation():
    series = _series()
    rows = [
        _tx(series, "10.00", date(2026, 1, 1)),
        _tx(series, "10.00", date(2026, 2, 1)),
        _tx(series, "12.00", date(2026, 4, 1)),  # March never happened
    ]

    events = detect_price_changes(series, rows)

    assert len(events) == 1
    event = events[0]
    assert event.previous_date == date(2026, 2, 1)
    assert event.current_date == date(2026, 4, 1)
    assert event.gap_days == (date(2026, 4, 1) - date(2026, 2, 1)).days == 59
    # Nothing was invented for March: the input row set is untouched.
    assert len(rows) == 3


def test_non_positive_old_amount_is_not_eligible():
    series = _series()
    rows = [
        _tx(series, "0.00", date(2026, 1, 15)),
        _tx(series, "12.00", date(2026, 2, 15)),
    ]

    events = detect_price_changes(series, rows)
    count = count_clean_observations(series, rows)

    # The zero row is not evidence, so the old amount never reaches the
    # percent calculation: no event, no percent.
    assert events == []
    assert count == 1
    assert classify_eligibility(count, events) == "insufficient_history"


def test_decimal_serialization_preserves_two_places():
    series = _series()
    rows = _monthly_rows(series, ["39.90", "39.90", "39.90", "42.90"])

    events = detect_price_changes(series, rows)

    assert len(events) == 1
    payload = events[0].model_dump_json()
    assert '"previous_amount":"39.90"' in payload
    assert '"current_amount":"42.90"' in payload
    assert '"absolute_delta":"3.00"' in payload
    assert '"percent_delta":"7.52"' in payload


# ---------------------------------------------------------------------------
# Property-style vectors (§6.1 tail)
# ---------------------------------------------------------------------------

_VECTORS = [
    ["15.00", "15.00", "15.00", "17.00"],
    ["10.00", "11.00"],
    ["10.00", "10.00", "11.00"],
    ["80.00", "100.00", "90.00", "110.00"],
    ["10.00", "10.00", "10.00", "12.00", "12.00", "12.00", "15.00"],
    ["39.90", "39.90", "39.90", "42.90", "42.90", "42.90"],
    ["10.00", "10.00", "10.00", "12.00", "10.00", "10.00", "10.00", "12.00"],
    ["5.00"],
    [],
]


def test_event_count_never_exceeds_adjacent_clean_transitions():
    for amounts in _VECTORS:
        series = _series()
        rows = _monthly_rows(series, amounts)
        events = detect_price_changes(series, rows)
        count = count_clean_observations(series, rows)
        # Each event consumes one adjacent clean transition; a segment of n
        # observations yields at most n-2 events, always <= count-1.
        assert len(events) <= max(count - 1, 0), amounts


def test_input_order_does_not_change_result():
    amounts = ["10.00", "10.00", "10.00", "12.00", "12.00", "15.00"]
    series = _series()
    baseline_rows = _monthly_rows(series, amounts)
    expected = detect_price_changes(series, baseline_rows)

    rng = random.Random(7)
    for _ in range(5):
        shuffled = baseline_rows.copy()
        rng.shuffle(shuffled)
        assert detect_price_changes(series, shuffled) == expected


# ---------------------------------------------------------------------------
# T2: workspace-scoped query tests (service layer, no HTTP)
# ---------------------------------------------------------------------------


async def _other_workspace(session, test_user) -> Workspace:
    workspace = Workspace(
        id=uuid.uuid4(),
        name="Escritório",
        kind="personal",
        created_by_user_id=test_user.id,
        default_currency="USD",
        locale="en",
    )
    session.add(workspace)
    await session.flush()
    return workspace


async def test_price_history_query_scoped_to_workspace(session, test_user, test_workspace):
    other = await _other_workspace(session, test_user)
    other_series = _series(workspace_id=other.id)
    rows = _monthly_rows(other_series, ["10.00", "10.00", "11.00"])
    await _persist(session, other_series, rows)

    # Out-of-workspace series behaves exactly like an unknown one.
    assert await get_price_history(session, other_series.id, test_workspace.id) is None
    own_read = await get_price_history(session, other_series.id, other.id)
    assert own_read is not None
    assert own_read.eligibility == "changes_detected"

    # Recent list is workspace-scoped: the caller's workspace sees nothing.
    assert await get_recent_price_changes(session, test_workspace.id) == []
    recent = await get_recent_price_changes(session, other.id)
    assert [event.recurring_id for event in recent] == [other_series.id]


async def test_price_history_query_excludes_cross_workspace_linked_rows(
    session, test_user, test_workspace
):
    other = await _other_workspace(session, test_user)
    series = _series(workspace_id=test_workspace.id)
    rows = _monthly_rows(series, ["10.00", "10.00", "11.00"])
    # A row physically in another workspace cannot serve as evidence here,
    # even if the FK points at this series.
    rows[2] = _tx(series, "11.00", rows[2].date, workspace_id=other.id)
    await _persist(session, series, rows)

    history = await get_price_history(session, series.id, test_workspace.id)
    assert history is not None
    assert history.eligible_occurrence_count == 2
    assert history.changes == []
    assert await get_recent_price_changes(session, test_workspace.id) == []


async def test_price_history_query_filters_source_status_and_ignore(
    session, test_workspace, test_user
):
    series = _series(workspace_id=test_workspace.id)
    rows = _monthly_rows(series, ["10.00", "10.00", "10.00"])
    rows.append(_tx(series, "12.00", _add_months(_START, 3), source="opening_balance"))
    rows.append(_tx(series, "12.00", _add_months(_START, 4), status="pending"))
    rows.append(_tx(series, "12.00", _add_months(_START, 5), is_ignored=True))
    rows.append(_tx(series, "12.00", _add_months(_START, 6), source="recurring"))
    await _persist(session, series, rows)

    history = await get_price_history(session, series.id, test_workspace.id)
    assert history is not None
    # The four synthetic/ineligible rows never reach the detector.
    assert history.eligible_occurrence_count == 3
    assert history.eligibility == "no_qualifying_change"
    assert history.changes == []


async def test_price_history_query_reflects_unlink(session, test_workspace, test_user):
    series = _series(workspace_id=test_workspace.id)
    rows = _monthly_rows(series, ["10.00", "10.00", "11.00"])
    await _persist(session, series, rows)

    before = await get_price_history(session, series.id, test_workspace.id)
    assert before is not None and len(before.changes) == 1
    assert len(await get_recent_price_changes(session, test_workspace.id)) == 1

    # The unlink escape hatch clears only the FK; the derived signal must
    # recompute without a stale event (§5.10).
    for tx in rows:
        tx.recurring_transaction_id = None
    await session.flush()

    after = await get_price_history(session, series.id, test_workspace.id)
    assert after is not None
    assert after.eligible_occurrence_count == 0
    assert after.eligibility == "insufficient_history"
    assert after.changes == []
    assert await get_recent_price_changes(session, test_workspace.id) == []


async def test_price_history_query_does_not_mutate(session, test_workspace, test_user):
    series = _series(workspace_id=test_workspace.id, amount=Decimal("99.99"))
    rows = _monthly_rows(series, ["10.00", "10.00", "11.00"])
    await _persist(session, series, rows)

    def in_memory_snapshot():
        row_state = [
            (
                tx.id,
                tx.recurring_transaction_id,
                tx.amount,
                tx.status,
                tx.is_ignored,
                tx.source,
            )
            for tx in rows
        ]
        return (row_state, series.amount, series.next_occurrence, series.currency)

    async def db_snapshot():
        # Reload from the database so a flushed write cannot hide behind the
        # identity map (attribute access must be awaited on an async session).
        for tx in rows:
            await session.refresh(tx)
        await session.refresh(series)
        return in_memory_snapshot()

    before_memory = in_memory_snapshot()
    before_db = await db_snapshot()
    assert before_memory == before_db

    history = await get_price_history(session, series.id, test_workspace.id)
    recent = await get_recent_price_changes(session, test_workspace.id)
    assert history is not None and len(history.changes) == 1
    assert len(recent) == 1

    # The reads changed neither the in-memory objects nor the stored rows.
    assert in_memory_snapshot() == before_memory
    assert await db_snapshot() == before_db


async def test_recent_price_changes_query_latest_per_series(session, test_workspace, test_user):
    first = _series(workspace_id=test_workspace.id, description="Streaming")
    first_rows = _monthly_rows(
        first, ["10.00", "10.00", "10.00", "12.00", "12.00", "12.00", "15.00"]
    )
    second = _series(workspace_id=test_workspace.id, description="Gym")
    second_rows = _monthly_rows(second, ["20.00", "20.00", "25.00"], start=date(2026, 2, 1))
    await _persist(session, first, first_rows)
    await _persist(session, second, second_rows)

    history = await get_price_history(session, first.id, test_workspace.id)
    assert history is not None
    assert len(history.changes) == 2  # 10->12 then 12->15, newest first
    assert history.changes[0].current_amount == Decimal("15.00")
    assert history.changes[1].current_amount == Decimal("12.00")

    recent = await get_recent_price_changes(session, test_workspace.id)
    # Two series, each contributing exactly one (latest) event, newest first.
    assert len(recent) == 2
    assert recent[0].recurring_id == first.id
    assert recent[0].current_amount == Decimal("15.00")
    assert recent[1].recurring_id == second.id
    assert recent[1].current_amount == Decimal("25.00")


async def test_recent_price_changes_query_respects_limit(session, test_workspace, test_user):
    starts = [date(2026, 1, 15), date(2026, 2, 15), date(2026, 5, 15)]
    series_list = []
    for index, start in enumerate(starts):
        series = _series(workspace_id=test_workspace.id, description=f"Plan {index}")
        rows = _monthly_rows(series, ["10.00", "10.00", "11.00"], start=start)
        await _persist(session, series, rows)
        series_list.append(series)

    newest_first = [series_list[2], series_list[1], series_list[0]]
    one = await get_recent_price_changes(session, test_workspace.id, limit=1)
    assert [event.recurring_id for event in one] == [newest_first[0].id]

    two = await get_recent_price_changes(session, test_workspace.id, limit=2)
    assert [event.recurring_id for event in two] == [s.id for s in newest_first[:2]]

    all_three = await get_recent_price_changes(session, test_workspace.id, limit=50)
    assert [event.recurring_id for event in all_three] == [s.id for s in newest_first]


async def test_price_history_handles_large_history(session, test_workspace, test_user):
    """R5 performance check: one workspace-scoped batch read over a large fixture."""
    series = _series(workspace_id=test_workspace.id)
    row_count = 1000
    start_ordinal = date(2020, 1, 1).toordinal()
    rows = [
        _tx(series, "10.00", date.fromordinal(start_ordinal + i))
        for i in range(row_count - 3)
    ]
    rows += [
        _tx(series, "11.00", date.fromordinal(start_ordinal + row_count - 3 + i))
        for i in range(3)
    ]
    await _persist(session, series, rows)

    began = time.perf_counter()
    history = await get_price_history(session, series.id, test_workspace.id)
    elapsed = time.perf_counter() - began

    assert history is not None
    assert history.eligible_occurrence_count == row_count
    assert len(history.changes) == 1
    assert history.changes[0].current_amount == Decimal("11.00")
    # Generous bound: the read is one indexed query plus a linear pass; a
    # regression to per-row lazy loads would blow far past this.
    assert elapsed < 5.0, f"large-history read took {elapsed:.2f}s"
