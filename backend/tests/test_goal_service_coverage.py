"""Coverage-focused unit tests for app.services.goal_service.

Exercises the pure helpers directly (cheap branch coverage) plus the
service functions against the SQLite test DB (resolve current amount for
each tracking_type, enrich currency conversion, get/update/delete/summary).
"""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.asset import Asset
from app.models.asset_group import AssetGroup
from app.models.asset_value import AssetValue
from app.models.bank_connection import BankConnection
from app.models.fx_rate import FxRate
from app.models.goal import Goal
from app.models.transaction import Transaction
from app.schemas.goal import GoalUpdate
from app.services.goal_service import (
    _compute_monthly_contribution,
    _compute_on_track,
    _compute_percentage,
    _enrich_goal,
    _resolve_current_amount,
    update_goal,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_account(
    session, user_id, workspace_id, *, balance="0.00", currency="BRL",
    connection_id=None, acc_type="checking",
):
    acc = Account(
        id=uuid.uuid4(), user_id=user_id, workspace_id=workspace_id,
        name="Goal Acct", type=acc_type, balance=Decimal(balance),
        currency=currency, connection_id=connection_id,
    )
    session.add(acc)
    await session.commit()
    await session.refresh(acc)
    return acc


async def _add_txn(
    session, user_id, account_id, workspace_id, amount, typ, dt,
    currency="BRL", status="posted",
):
    txn = Transaction(
        id=uuid.uuid4(), user_id=user_id, account_id=account_id,
        workspace_id=workspace_id, description="t", amount=Decimal(str(amount)),
        date=dt, type=typ, source="manual", currency=currency,
        status=status,
        created_at=datetime.now(timezone.utc),
    )
    session.add(txn)
    await session.commit()


async def _make_asset(session, user_id, workspace_id, *, currency="BRL", price="0.00", group_id=None):
    asset = Asset(
        id=uuid.uuid4(), user_id=user_id, workspace_id=workspace_id,
        name="Goal Asset", type="investment", currency=currency,
        purchase_price=Decimal(price), group_id=group_id,
    )
    session.add(asset)
    await session.commit()
    await session.refresh(asset)
    return asset


async def _make_asset_group(session, user_id, workspace_id, *, name="Emergency Wallet"):
    group = AssetGroup(
        id=uuid.uuid4(), user_id=user_id, workspace_id=workspace_id,
        name=name, icon="wallet", color="#0EA5E9",
    )
    session.add(group)
    await session.commit()
    await session.refresh(group)
    return group


async def _make_goal(session, user_id, workspace_id, **kwargs):
    defaults = dict(
        id=uuid.uuid4(), user_id=user_id, workspace_id=workspace_id,
        name="G", target_amount=Decimal("1000"), current_amount=Decimal("100"),
        initial_amount=Decimal("0"), currency="BRL", tracking_type="manual",
        status="active",
    )
    defaults.update(kwargs)
    goal = Goal(**defaults)
    session.add(goal)
    await session.commit()
    await session.refresh(goal)
    return goal


# ---------------------------------------------------------------------------
# Pure helpers — _compute_percentage
# ---------------------------------------------------------------------------


def test_compute_percentage_normal():
    assert _compute_percentage(Decimal("250"), Decimal("1000")) == 25.0


def test_compute_percentage_zero_target_with_current():
    assert _compute_percentage(Decimal("5"), Decimal("0")) == 100.0


def test_compute_percentage_zero_target_no_current():
    assert _compute_percentage(Decimal("0"), Decimal("0")) == 0.0


def test_compute_percentage_negative_target():
    assert _compute_percentage(Decimal("0"), Decimal("-10")) == 0.0


# ---------------------------------------------------------------------------
# Pure helpers — _compute_monthly_contribution
# ---------------------------------------------------------------------------


def test_monthly_contribution_no_target_date():
    assert _compute_monthly_contribution(Decimal("0"), Decimal("1000"), None) is None


def test_monthly_contribution_target_in_past():
    past = date.today() - timedelta(days=5)
    assert _compute_monthly_contribution(Decimal("0"), Decimal("1000"), past) == 0.0


def test_monthly_contribution_already_reached():
    future = date.today() + timedelta(days=365)
    assert _compute_monthly_contribution(Decimal("2000"), Decimal("1000"), future) == 0.0


def test_monthly_contribution_normal():
    with patch("app.services.goal_service.app_today", return_value=date(2026, 1, 15)):
        val = _compute_monthly_contribution(Decimal("0"), Decimal("1200"), date(2027, 1, 15))
    assert val == 100.0  # 1200 over 12 months


def test_monthly_contribution_months_clamped_to_one():
    # Target date in the same calendar month but a later day -> months == 0,
    # clamped to 1 (line 111). today < target_date so we don't hit the past branch.
    with patch("app.services.goal_service.app_today", return_value=date(2026, 1, 15)):
        val = _compute_monthly_contribution(Decimal("0"), Decimal("500"), date(2026, 1, 28))
    assert val == 500.0  # remaining / 1 month


# ---------------------------------------------------------------------------
# Pure helpers — _compute_on_track
# ---------------------------------------------------------------------------


def test_on_track_no_target_date():
    assert _compute_on_track(Decimal("0"), Decimal("1000"), None) is None


def test_on_track_achieved():
    future = date.today() + timedelta(days=100)
    assert _compute_on_track(Decimal("1000"), Decimal("1000"), future) == "achieved"


def test_on_track_overdue():
    past = date.today() - timedelta(days=10)
    assert _compute_on_track(Decimal("10"), Decimal("1000"), past) == "overdue"


def test_on_track_total_days_zero_returns_on_track():
    # created today, target today -> total_days <= 0 branch
    today = date.today()
    assert _compute_on_track(Decimal("100"), Decimal("1000"), today, today) == "on_track"


def test_on_track_total_needed_non_positive():
    # initial_amount >= target -> total_needed <= 0 -> achieved
    start = date.today() - timedelta(days=10)
    future = date.today() + timedelta(days=10)
    result = _compute_on_track(
        Decimal("500"), Decimal("1000"), future, start, initial_amount=Decimal("1000")
    )
    assert result == "achieved"


def test_on_track_ahead():
    start = date.today() - timedelta(days=50)
    future = date.today() + timedelta(days=50)
    # Halfway in time but saved almost everything -> ahead
    result = _compute_on_track(
        Decimal("990"), Decimal("1000"), future, start, initial_amount=Decimal("0")
    )
    assert result == "ahead"


def test_on_track_on_track():
    start = date.today() - timedelta(days=50)
    future = date.today() + timedelta(days=50)
    # ~halfway, saved ~half -> on_track
    result = _compute_on_track(
        Decimal("500"), Decimal("1000"), future, start, initial_amount=Decimal("0")
    )
    assert result == "on_track"


def test_on_track_behind():
    start = date.today() - timedelta(days=80)
    future = date.today() + timedelta(days=20)
    # 80% through time but saved almost nothing -> behind
    result = _compute_on_track(
        Decimal("10"), Decimal("1000"), future, start, initial_amount=Decimal("0")
    )
    assert result == "behind"


def test_on_track_created_at_none_uses_today():
    future = date.today() + timedelta(days=30)
    # created_at None -> start = today, so no progress is expected yet
    result = _compute_on_track(Decimal("0"), Decimal("1000"), future, None)
    assert result == "on_track"


# ---------------------------------------------------------------------------
# _resolve_current_amount — manual fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_manual(session: AsyncSession, test_user, test_workspace):
    goal = await _make_goal(
        session, test_user.id, test_workspace.id,
        tracking_type="manual", current_amount=Decimal("777"),
    )
    val = await _resolve_current_amount(session, goal, test_user.id)
    assert val == Decimal("777")


# ---------------------------------------------------------------------------
# _resolve_current_amount — account tracking
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_account_excludes_pending_and_future_rows(
    session, test_user, test_workspace
):
    """Goal current amount follows posted balance, including for forecast rows."""
    today = date.today()
    acc = await _make_account(session, test_user.id, test_workspace.id, currency="BRL")
    await _add_txn(session, test_user.id, acc.id, test_workspace.id, 1500, "credit", today)
    await _add_txn(
        session, test_user.id, acc.id, test_workspace.id, 200, "debit", today,
        status="pending",
    )
    await _add_txn(
        session, test_user.id, acc.id, test_workspace.id, 300, "debit",
        today + timedelta(days=3),
    )
    goal = await _make_goal(
        session, test_user.id, test_workspace.id,
        tracking_type="account", account_id=acc.id, currency="BRL",
    )
    val = await _resolve_current_amount(session, goal, test_user.id)
    assert val == pytest.approx(Decimal("1500"))


@pytest.mark.asyncio
async def test_resolve_account_missing_account_fallback(session, test_user, test_workspace):
    goal = await _make_goal(
        session, test_user.id, test_workspace.id,
        tracking_type="account", account_id=uuid.uuid4(),
        current_amount=Decimal("42"),
    )
    val = await _resolve_current_amount(session, goal, test_user.id)
    assert val == Decimal("42")


@pytest.mark.asyncio
async def test_resolve_account_no_account_id_fallback(session, test_user, test_workspace):
    # tracking_type account but no account_id -> falls through to else
    goal = await _make_goal(
        session, test_user.id, test_workspace.id,
        tracking_type="account", account_id=None, current_amount=Decimal("13"),
    )
    val = await _resolve_current_amount(session, goal, test_user.id)
    assert val == Decimal("13")


# ---------------------------------------------------------------------------
# _resolve_current_amount — asset tracking
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_asset_with_asset_value(session, test_user, test_workspace):
    asset = await _make_asset(session, test_user.id, test_workspace.id, currency="BRL", price="100")
    session.add(AssetValue(
        id=uuid.uuid4(), asset_id=asset.id, workspace_id=test_workspace.id,
        amount=Decimal("9999"), date=date.today(), source="manual",
    ))
    await session.commit()
    goal = await _make_goal(
        session, test_user.id, test_workspace.id,
        tracking_type="asset", asset_id=asset.id, currency="BRL",
    )
    val = await _resolve_current_amount(session, goal, test_user.id)
    assert val == pytest.approx(Decimal("9999"))


@pytest.mark.asyncio
async def test_resolve_asset_missing_fallback(session, test_user, test_workspace):
    goal = await _make_goal(
        session, test_user.id, test_workspace.id,
        tracking_type="asset", asset_id=uuid.uuid4(), current_amount=Decimal("55"),
    )
    val = await _resolve_current_amount(session, goal, test_user.id)
    assert val == Decimal("55")


@pytest.mark.asyncio
async def test_resolve_asset_group_cross_currency(session, test_user, test_workspace):
    today = date.today()
    session.add(FxRate(base_currency="USD", quote_currency="BRL", date=today,
                       rate=Decimal("5.0"), source="test"))
    group = await _make_asset_group(session, test_user.id, test_workspace.id)
    await _make_asset(
        session, test_user.id, test_workspace.id,
        currency="USD", price="100", group_id=group.id,
    )
    await session.commit()
    goal = await _make_goal(
        session, test_user.id, test_workspace.id,
        tracking_type="asset_group", asset_group_id=group.id, currency="BRL",
    )
    val = await _resolve_current_amount(session, goal, test_user.id)
    assert float(val) == pytest.approx(500.0, abs=1.0)


@pytest.mark.asyncio
async def test_resolve_asset_group_missing_fallback(session, test_user, test_workspace):
    goal = await _make_goal(
        session, test_user.id, test_workspace.id,
        tracking_type="asset_group", asset_group_id=uuid.uuid4(), current_amount=Decimal("88"),
    )
    val = await _resolve_current_amount(session, goal, test_user.id)
    assert val == Decimal("88")


# ---------------------------------------------------------------------------
# _resolve_current_amount — net_worth
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_net_worth_same_currency(session, test_user, test_workspace):
    acc = await _make_account(session, test_user.id, test_workspace.id, currency="BRL")
    await _add_txn(session, test_user.id, acc.id, test_workspace.id, 2000, "credit", date.today())
    asset = await _make_asset(session, test_user.id, test_workspace.id, currency="BRL", price="3000")
    session.add(AssetValue(
        id=uuid.uuid4(), asset_id=asset.id, workspace_id=test_workspace.id,
        amount=Decimal("3000"), date=date.today(), source="manual",
    ))
    await session.commit()
    goal = await _make_goal(
        session, test_user.id, test_workspace.id,
        tracking_type="net_worth", currency="BRL",
    )
    val = await _resolve_current_amount(session, goal, test_user.id)
    # 2000 account + 3000 asset
    assert float(val) == pytest.approx(5000.0, abs=1.0)


@pytest.mark.asyncio
async def test_resolve_net_worth_counts_shared_credit_line_once(session, test_user, test_workspace):
    acc = await _make_account(session, test_user.id, test_workspace.id, currency="BRL")
    await _add_txn(session, test_user.id, acc.id, test_workspace.id, 2000, "credit", date.today())
    conn = BankConnection(
        id=uuid.uuid4(), user_id=test_user.id, provider="pluggy",
        external_id="ext-goal-shared", institution_name="Shared line",
        credentials={}, status="active", created_at=datetime.now(timezone.utc),
    )
    session.add(conn)
    await session.flush()
    for name in ("Shared Visa", "Shared Mastercard"):
        session.add(Account(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
            connection_id=conn.id, name=name, type="credit_card",
            balance=Decimal("500"), currency="BRL",
            shared_balance_group=f"{conn.id}:shared-credit",
        ))
    await session.commit()
    goal = await _make_goal(
        session, test_user.id, test_workspace.id,
        tracking_type="net_worth", currency="BRL",
    )
    val = await _resolve_current_amount(session, goal, test_user.id)
    # 2000 account - 500 shared credit line (not 1000)
    assert float(val) == pytest.approx(1500.0, abs=1.0)


@pytest.mark.asyncio
async def test_resolve_net_worth_cross_currency(session, test_user, test_workspace):
    today = date.today()
    for quote, rate in [("BRL", "5.0"), ("USD", "1.0")]:
        session.add(FxRate(base_currency="USD", quote_currency=quote, date=today,
                           rate=Decimal(rate), source="test"))
    # USD account + USD asset, goal in BRL -> conversion branch for both
    acc = await _make_account(session, test_user.id, test_workspace.id, currency="USD")
    await _add_txn(session, test_user.id, acc.id, test_workspace.id, 100, "credit", today, currency="USD")
    asset = await _make_asset(session, test_user.id, test_workspace.id, currency="USD", price="200")
    session.add(AssetValue(
        id=uuid.uuid4(), asset_id=asset.id, workspace_id=test_workspace.id,
        amount=Decimal("200"), date=today, source="manual",
    ))
    await session.commit()
    goal = await _make_goal(
        session, test_user.id, test_workspace.id,
        tracking_type="net_worth", currency="BRL",
    )
    val = await _resolve_current_amount(session, goal, test_user.id)
    # (100 + 200) USD * 5.0 = 1500 BRL
    assert float(val) == pytest.approx(1500.0, abs=5.0)


# ---------------------------------------------------------------------------
# _enrich_goal — names + primary-currency conversion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enrich_goal_linked_names(session, test_user, test_workspace):
    acc = await _make_account(session, test_user.id, test_workspace.id, currency="BRL")
    asset = await _make_asset(session, test_user.id, test_workspace.id, currency="BRL", price="0")
    group = await _make_asset_group(session, test_user.id, test_workspace.id, name="Reserva de emergência")
    goal = await _make_goal(
        session, test_user.id, test_workspace.id,
        tracking_type="manual", account_id=acc.id, asset_id=asset.id, asset_group_id=group.id,
    )
    enriched = await _enrich_goal(session, goal, test_user.id)
    assert enriched.account_name == "Goal Acct"
    assert enriched.asset_name == "Goal Asset"
    assert enriched.asset_group_name == "Reserva de emergência"


@pytest.mark.asyncio
async def test_enrich_goal_primary_currency_conversion(session, test_user, test_workspace):
    # test_user primary currency is BRL; make goal in USD to hit conversion branch
    today = date.today()
    session.add(FxRate(base_currency="USD", quote_currency="BRL", date=today,
                       rate=Decimal("5.0"), source="test"))
    await session.commit()
    goal = await _make_goal(
        session, test_user.id, test_workspace.id,
        tracking_type="manual", currency="USD",
        target_amount=Decimal("100"), current_amount=Decimal("50"),
    )
    enriched = await _enrich_goal(session, goal, test_user.id)
    assert enriched.target_amount_primary == Decimal("500.00")
    assert enriched.current_amount_primary == Decimal("250.00")


@pytest.mark.asyncio
async def test_enrich_goal_same_currency_no_conversion(session, test_user, test_workspace):
    goal = await _make_goal(
        session, test_user.id, test_workspace.id, currency="BRL",
    )
    enriched = await _enrich_goal(session, goal, test_user.id)
    assert enriched.target_amount_primary is None
    assert enriched.current_amount_primary is None


# ---------------------------------------------------------------------------
# get_goal / update_goal / delete_goal / get_goal_summary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_goal_not_found(session, test_user, test_workspace):
    result = await update_goal(
        session, uuid.uuid4(), test_workspace.id, test_user.id,
        GoalUpdate(name="Nope"),
    )
    assert result is None


