from datetime import date, datetime, timezone
from unittest.mock import patch

import pytest
from zoneinfo import ZoneInfo

from app.core.app_clock import app_today, use_timezone
from app.models.app_settings import AppSetting


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 5, 19, 1, 0, tzinfo=timezone.utc).astimezone(tz)


@pytest.mark.asyncio
async def test_system_local_timezone_is_fallback_when_tz_is_unset(
    session, clean_db, monkeypatch
):
    monkeypatch.delenv("TZ", raising=False)
    with (
        patch("tzlocal.get_localzone", return_value=ZoneInfo("America/Bahia")),
        patch("app.core.app_clock.datetime", FixedDatetime),
    ):
        async with use_timezone(session):
            assert app_today().isoformat() == "2026-05-18"


@pytest.mark.asyncio
async def test_invalid_saved_timezone_leaves_routes_and_repair_available(
    client, admin_auth_headers, session, monkeypatch, caplog
):
    monkeypatch.setenv("TZ", "America/Sao_Paulo")
    session.add(AppSetting(key="timezone", value="Unavailable/Zone"))
    await session.commit()
    response = await client.get("/api/setup/status")
    assert response.status_code == 200
    response = await client.get("/api/admin/timezone", headers=admin_auth_headers)
    assert response.json()["timezone"] == "America/Sao_Paulo"
    assert "Saved application timezone is unavailable" in caplog.text
    response = await client.patch(
        "/api/admin/settings/timezone", headers=admin_auth_headers, json={"value": "UTC"}
    )
    assert response.status_code == 200
    response = await client.get("/api/admin/timezone", headers=admin_auth_headers)
    assert response.json()["timezone"] == "UTC"


@pytest.mark.asyncio
async def test_recurring_fx_worker_uses_local_day_through_commit(
    session, test_user, test_workspace, monkeypatch
):
    from datetime import date
    from decimal import Decimal
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from app.models.fx_rate import FxRate
    from app.models.recurring_transaction import RecurringTransaction
    from app.tasks.fx_rate_tasks import _restamp_recurring_fx

    monkeypatch.setenv("TZ", "UTC")
    test_user.preferences = {"currency_display": "USD"}
    session.add(AppSetting(key="timezone", value="America/Sao_Paulo"))
    for day, rate in ((18, 5), (19, 10)):
        session.add(FxRate(base_currency="USD", quote_currency="BRL",
                           date=date(2026, 5, day), rate=rate, source="test"))
    rec = RecurringTransaction(
        user_id=test_user.id, workspace_id=test_workspace.id, description="Local FX",
        amount=100, currency="BRL", type="debit", frequency="monthly",
        start_date=date(2026, 5, 19), next_occurrence=date(2026, 5, 19),
    )
    session.add(rec)
    await session.commit()
    maker = async_sessionmaker(session.bind, expire_on_commit=False)
    with (
        patch("app.core.app_clock.datetime", FixedDatetime),
        patch("app.tasks.fx_rate_tasks._make_session_maker",
              return_value=(SimpleNamespace(dispose=AsyncMock()), maker)),
    ):
        assert await _restamp_recurring_fx() == 1
    await session.refresh(rec)
    assert rec.amount_primary == Decimal("20.00")
    assert rec.fx_rate_used == Decimal("0.2")


@pytest.mark.asyncio
async def test_timezone_changes_calendar_date_for_new_operations(session, clean_db):
    setting = AppSetting(key="timezone", value="America/Sao_Paulo")
    session.add(setting)
    await session.commit()
    with patch("app.core.app_clock.datetime", FixedDatetime):
        async with use_timezone(session):
            assert app_today().isoformat() == "2026-05-18"
        setting.value = "Asia/Tokyo"
        await session.commit()
        async with use_timezone(session):
            assert app_today().isoformat() == "2026-05-19"


@pytest.mark.asyncio
async def test_context_primer_uses_application_timezone(session, test_user):
    from app.agents.services.context_service import build_context_primer

    setting = AppSetting(key="timezone", value="America/Sao_Paulo")
    session.add(setting)
    await session.commit()
    with patch("app.core.app_clock.datetime", FixedDatetime):
        async with use_timezone(session):
            setting.value = "Asia/Tokyo"
            await session.commit()
            primer = await build_context_primer(session, test_user)
    assert "Today is 2026-05-18 (America/Sao_Paulo)" in primer
    assert "Timezone: America/Sao_Paulo" in primer


@pytest.mark.asyncio
async def test_account_service_uses_application_day_for_dates_and_balances(
    session, test_user, test_workspace, monkeypatch
):
    from decimal import Decimal

    from sqlalchemy import select

    from app.models.transaction import Transaction
    from app.schemas.account import AccountCreate
    from app.services.account_service import create_account, get_accounts

    class HostDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 5, 19)

    monkeypatch.setenv("TZ", "UTC")
    session.add(AppSetting(key="timezone", value="America/Sao_Paulo"))
    await session.commit()
    with (
        patch("app.core.app_clock.datetime", FixedDatetime),
        patch("app.services.account_service._Date", HostDate),
    ):
        async with use_timezone(session):
            account = await create_account(
                session,
                test_workspace.id,
                test_user.id,
                AccountCreate(
                    name="Local calendar account",
                    type="checking",
                    balance=Decimal("100.00"),
                    currency="BRL",
                ),
            )
            opening = await session.scalar(
                select(Transaction).where(
                    Transaction.account_id == account.id,
                    Transaction.source == "opening_balance",
                )
            )
            assert opening is not None
            assert opening.date == date(2026, 5, 18)

            session.add(
                Transaction(
                    user_id=test_user.id,
                    workspace_id=test_workspace.id,
                    account_id=account.id,
                    description="Tomorrow locally",
                    amount=Decimal("50.00"),
                    currency="BRL",
                    date=date(2026, 5, 19),
                    type="credit",
                    source="manual",
                )
            )
            await session.commit()
            [serialized] = await get_accounts(session, test_workspace.id)

    assert serialized["current_balance"] == 100.0


@pytest.mark.asyncio
async def test_fx_backfill_worker_uses_application_timezone(
    session, test_workspace, monkeypatch
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.models.user import User
    from app.tasks import fx_backfill_tasks

    monkeypatch.setenv("TZ", "UTC")
    session.add(AppSetting(key="timezone", value="America/Sao_Paulo"))
    await session.commit()
    observed_days = []
    original_select = fx_backfill_tasks.select

    def observing_select(*entities):
        if entities and entities[0] is User:
            observed_days.append(app_today())
        return original_select(*entities)

    maker = async_sessionmaker(session.bind, expire_on_commit=False)
    with (
        patch("app.core.app_clock.datetime", FixedDatetime),
        patch.object(fx_backfill_tasks, "select", side_effect=observing_select),
        patch.object(
            fx_backfill_tasks,
            "_make_session_maker",
            return_value=(SimpleNamespace(dispose=AsyncMock()), maker),
        ),
    ):
        await fx_backfill_tasks._backfill_primary_amounts()

    assert observed_days == [date(2026, 5, 18)]


@pytest.mark.asyncio
async def test_tesouro_cache_warm_uses_application_timezone(
    session, test_workspace, monkeypatch
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.config import get_settings
    from app.main import _warm_tesouro_cache

    monkeypatch.setenv("TZ", "UTC")
    monkeypatch.setattr(get_settings(), "tesouro_direto_enabled", True)
    test_workspace.default_currency = "BRL"
    session.add(AppSetting(key="timezone", value="America/Sao_Paulo"))
    await session.commit()
    observed_days = []

    async def get_available_bonds():
        observed_days.append(app_today())
        return []

    provider = SimpleNamespace(
        get_available_bonds=AsyncMock(side_effect=get_available_bonds)
    )
    maker = async_sessionmaker(session.bind, expire_on_commit=False)
    with (
        patch("app.core.app_clock.datetime", FixedDatetime),
        patch("app.core.database.async_session_maker", maker),
        patch(
            "app.providers.tesouro_direto.get_tesouro_direto_provider",
            return_value=provider,
        ),
    ):
        await _warm_tesouro_cache()

    assert observed_days == [date(2026, 5, 18)]


@pytest.mark.asyncio
async def test_admin_can_set_timezone_and_invalid_names_are_rejected(client, admin_auth_headers):
    response = await client.patch(
        "/api/admin/settings/timezone",
        headers=admin_auth_headers,
        json={"value": "America/Sao_Paulo"},
    )
    assert response.status_code == 200
    response = await client.get("/api/admin/timezone", headers=admin_auth_headers)
    assert response.json()["timezone"] == "America/Sao_Paulo"
    assert "Europe/London" in response.json()["available"]
    for value in ("Invalid/Timezone", "../UTC", ""):
        response = await client.patch(
            "/api/admin/settings/timezone", headers=admin_auth_headers, json={"value": value}
        )
        assert response.status_code == 400


@pytest.mark.asyncio
async def test_environment_timezone_is_fallback(session, clean_db, monkeypatch):
    monkeypatch.setenv("TZ", "America/Sao_Paulo")
    with patch("app.core.app_clock.datetime", FixedDatetime):
        async with use_timezone(session):
            assert app_today().isoformat() == "2026-05-18"


@pytest.mark.asyncio
async def test_recurring_generation_waits_until_the_local_due_date(
    session, test_user, test_workspace, test_account, capsys, monkeypatch
):
    from datetime import date
    from app.schemas.recurring_transaction import RecurringTransactionCreate
    from app.services.recurring_transaction_service import (
        create_recurring_transaction,
        generate_pending,
    )
    from app.cli import generate_recurring
    from sqlalchemy.ext.asyncio import async_sessionmaker

    monkeypatch.setenv("TZ", "UTC")

    await create_recurring_transaction(
        session,
        test_workspace.id,
        test_user.id,
        RecurringTransactionCreate(
            description="Local due date",
            amount=10,
            type="debit",
            frequency="monthly",
            start_date=date(2026, 5, 19),
            account_id=test_account.id,
            auto_generate=True,
        ),
    )
    setting = AppSetting(key="timezone", value="America/Sao_Paulo")
    session.add(setting)
    await session.commit()
    with patch("app.core.app_clock.datetime", FixedDatetime):
        with patch("app.cli.async_session_maker", async_sessionmaker(session.bind, expire_on_commit=False)):
            await generate_recurring()
        assert "Total generated: 0" in capsys.readouterr().out
        async with use_timezone(session):
            assert await generate_pending(session, test_user.id) == 0
        setting.value = "UTC"
        await session.commit()
        async with use_timezone(session):
            assert await generate_pending(session, test_user.id) == 1


@pytest.mark.asyncio
async def test_mcp_defaults_use_application_month(session, test_user, test_workspace):
    from mcp_server.registry import call_tool
    from mcp_server.auth import CallContext
    import mcp_server.tools  # noqa: F401

    class MonthBoundary(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 6, 1, 1, 0, tzinfo=timezone.utc).astimezone(tz)

    session.add(AppSetting(key="timezone", value="America/Sao_Paulo"))
    await session.commit()
    with patch("app.core.app_clock.datetime", MonthBoundary):
        result = await call_tool(
            session,
            CallContext(user_id=test_user.id, workspace_id=test_workspace.id),
            "get_budget_vs_actual",
            {},
        )
    assert result["month"] == "2026-05-01"
