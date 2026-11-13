"""Calendar-window regressions and an API-verified rendered-gap witness."""

import asyncio
import json
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core import app_clock
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.services.recurring_price_change_service import detect_price_changes, get_recent_price_changes

TODAY = date(2026, 11, 7)
RECENT = "/api/recurring-transactions/price-changes/recent"
GAP_FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "frontend/src/test/fixtures/recurring-price-change-31-days.json"
)


@pytest.fixture(autouse=True)
def frozen_app_clock(monkeypatch):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 7, 1, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(app_clock, "datetime", FixedDatetime)


async def _seed(session, user, workspace, account, label, observations, *, series_id=None,
                frequency="monthly", transaction_ids=None):
    series = RecurringTransaction(
        id=series_id or uuid.uuid4(), user_id=user.id, workspace_id=workspace.id,
        account_id=account.id, description=label, amount=Decimal("99.00"),
        currency="BRL", type="debit", frequency=frequency,
        start_date=observations[0][0], next_occurrence=date(2027, 1, 1),
    )
    rows = [
        Transaction(
            id=transaction_ids[index] if transaction_ids else uuid.uuid4(),
            user_id=user.id, workspace_id=workspace.id, account_id=account.id,
            recurring_transaction_id=series.id, description=label,
            amount=Decimal(amount), date=when, currency="BRL", type="debit",
            source="csv", status="posted", is_ignored=False,
        )
        for index, (when, amount) in enumerate(observations)
    ]
    session.add(series)
    session.add_all(rows)
    await session.commit()
    return series, rows


def _observations(event_date):
    # Both stable baseline charges precede the Recent window, even for tomorrow.
    return [(event_date - timedelta(days=90), "10.00"),
            (event_date - timedelta(days=60), "10.00"), (event_date, "12.00")]


@pytest.mark.parametrize("offset,in_recent", [(-31, False), (-30, True), (0, True), (1, False)],
                         ids=["one-day-too-old", "inclusive-lower", "inclusive-today", "tomorrow"])
async def test_recent_calendar_boundary_keeps_older_baseline(
    client, auth_headers, session, test_user, test_workspace, test_account, offset, in_recent,
):
    test_workspace.timezone = "UTC"
    event_date = TODAY + timedelta(days=offset)
    series, rows = await _seed(session, test_user, test_workspace, test_account,
                               "Boundary", _observations(event_date))
    history = await client.get(f"/api/recurring-transactions/{series.id}/price-changes",
                               headers=auth_headers)
    assert history.status_code == 200
    body = history.json()
    assert body["eligible_occurrence_count"] == 3
    assert body["eligibility"] == "changes_detected"
    assert len(body["changes"]) == 1
    event = body["changes"][0]
    assert event["current_date"] == event_date.isoformat()
    assert event["previous_transaction_id"] == str(rows[1].id)
    assert (event["previous_amount"], event["current_amount"], event["absolute_delta"],
            event["percent_delta"], event["baseline_occurrence_count"]) == (
                "10.00", "12.00", "2.00", "20.00", 2)
    response = await client.get(RECENT, headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == ([event] if in_recent else [])


async def test_recent_selects_eligible_event_before_future_latest(
    client, auth_headers, session, test_user, test_workspace, test_account,
):
    test_workspace.timezone = "UTC"
    series, _ = await _seed(session, test_user, test_workspace, test_account, "Future latest", [
        (date(2026, 9, 1), "10.00"), (date(2026, 10, 1), "10.00"),
        (date(2026, 11, 1), "12.00"), (date(2026, 11, 2), "12.00"),
        (date(2026, 11, 8), "15.00"),
    ])
    history = await client.get(f"/api/recurring-transactions/{series.id}/price-changes",
                               headers=auth_headers)
    assert history.status_code == 200
    events = history.json()["changes"]
    assert [event["current_amount"] for event in events] == ["15.00", "12.00"]
    response = await client.get(RECENT, headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == [events[1]]


async def test_recent_filters_before_sort_tie_break_and_limit(
    client, auth_headers, session, test_user, test_workspace, test_account,
):
    test_workspace.timezone = "UTC"
    # Future-only must not consume the limit; equal dates sort by recurring ID.
    for ident, event_date in [(4, TODAY + timedelta(days=1)), (3, TODAY),
                              (2, TODAY), (1, TODAY - timedelta(days=30))]:
        await _seed(session, test_user, test_workspace, test_account, f"Limit {ident}",
                    _observations(event_date), series_id=uuid.UUID(int=ident))
    for limit, expected in [(1, [2]), (2, [2, 3]), (50, [2, 3, 1])]:
        response = await client.get(RECENT, params={"limit": limit}, headers=auth_headers)
        assert response.status_code == 200
        assert [event["recurring_id"] for event in response.json()] == [
            str(uuid.UUID(int=ident)) for ident in expected
        ]
        assert all(event["current_amount"] == "12.00" for event in response.json())


@pytest.mark.parametrize("zone,expected", [("America/Sao_Paulo", False), ("Asia/Tokyo", True)])
async def test_recent_uses_workspace_calendar_at_same_instant(
    client, auth_headers, session, test_user, test_workspace, test_account, zone, expected,
):
    test_workspace.timezone = zone
    series, _ = await _seed(session, test_user, test_workspace, test_account,
                            "Workspace calendar", _observations(TODAY))
    response = await client.get(RECENT, headers={**auth_headers,
                                "X-Workspace-Id": str(test_workspace.id)})
    assert response.status_code == 200
    assert [event["recurring_id"] for event in response.json()] == (
        [str(series.id)] if expected else [])


async def test_recent_analysis_does_not_dispatch_to_thread(
    session, test_user, test_workspace, test_account, monkeypatch,
):
    series, _ = await _seed(session, test_user, test_workspace, test_account,
                            "Direct analysis", _observations(TODAY))
    original = asyncio.to_thread
    dispatches = []

    async def observe_dispatch(func, *args, **kwargs):
        dispatches.append(func.__name__)
        return await original(func, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", observe_dispatch)
    recent = await get_recent_price_changes(session, test_workspace.id)
    assert len(recent) == 1
    assert recent[0].recurring_id == series.id
    assert recent[0].current_amount == Decimal("12.00")
    assert dispatches == []


@pytest.mark.parametrize("previous,current,gap,frequency", [
    (date(2026, 10, 1), date(2026, 11, 1), 31, "monthly"),
    (date(2026, 10, 1), date(2026, 11, 1), 31, "yearly"),
    (date(2024, 2, 1), date(2024, 3, 1), 29, "monthly"),
    (date(2026, 3, 1), date(2026, 4, 1), 31, "monthly"),
], ids=["shared-31-day-witness", "not-schedule-frequency", "leap-month", "dst-month"])
async def test_gap_detector_and_api_share_adjacent_observation_witness(
    client, auth_headers, session, test_user, test_workspace, test_account,
    previous, current, gap, frequency, tmp_path,
):
    test_workspace.timezone = "UTC"
    expected = json.loads(GAP_FIXTURE.read_text())
    series, rows = await _seed(
        session, test_user, test_workspace, test_account, "Gap witness",
        [(previous - timedelta(days=31), "10.00"), (previous, "10.00"), (current, "12.00")],
        frequency=frequency, series_id=uuid.UUID(expected["recurring_id"]),
        transaction_ids=[uuid.UUID(int=100), uuid.UUID(expected["previous_transaction_id"]),
                         uuid.UUID(expected["current_transaction_id"])],
    )
    detected = detect_price_changes(series, rows)
    assert len(detected) == 1
    event = detected[0]
    assert (event.previous_date, event.current_date, event.gap_days) == (previous, current, gap)
    assert (event.previous_amount, event.current_amount, event.absolute_delta,
            event.percent_delta) == (Decimal("10.00"), Decimal("12.00"),
                                     Decimal("2.00"), Decimal("20.00"))

    async def snapshot():
        return [list((await session.execute(select(*model.__table__.c).order_by(model.id))).all())
                for model in (RecurringTransaction, Transaction)]

    before = await snapshot()
    history = await client.get(f"/api/recurring-transactions/{series.id}/price-changes",
                               headers=auth_headers)
    recent = await client.get(RECENT, headers=auth_headers)
    assert history.status_code == recent.status_code == 200
    body = history.json()
    assert body["eligible_occurrence_count"] == 3
    assert body["changes"] == [event.model_dump(mode="json")]
    assert await snapshot() == before
    if (previous, current, frequency) == (date(2026, 10, 1), date(2026, 11, 1), "monthly"):
        assert body["changes"] == [expected]
        assert recent.json() == [expected]
        # Retain the actual HTTP response in this invocation's owned pytest scratch.
        (tmp_path / "gap-history-response.json").write_text(json.dumps(body, indent=2) + "\n")
        (tmp_path / "gap-recent-response.json").write_text(json.dumps(recent.json(), indent=2) + "\n")
