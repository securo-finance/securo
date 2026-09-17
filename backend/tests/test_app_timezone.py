from datetime import date, datetime, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from app.models.app_settings import AppSetting


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 5, 19, 1, 0, tzinfo=timezone.utc).astimezone(tz)


@pytest.mark.asyncio
async def test_saved_timezone_takes_effect_for_new_operations(session, clean_db):
    from app.core.app_clock import app_today, use_timezone

    setting = AppSetting(key="timezone", value="America/Sao_Paulo")
    session.add(setting)
    await session.commit()

    with patch("app.core.app_clock.datetime", FixedDatetime):
        async with use_timezone(session):
            assert app_today().isoformat() == "2026-05-18"

            setting.value = "Asia/Tokyo"
            await session.commit()
            assert app_today().isoformat() == "2026-05-18"

        async with use_timezone(session):
            assert app_today().isoformat() == "2026-05-19"


@pytest.mark.asyncio
async def test_tz_environment_falls_back_to_system_timezone(session, clean_db, monkeypatch):
    from app.core.app_clock import app_today, use_timezone

    monkeypatch.setenv("TZ", "Unavailable/Zone")
    with (
        patch("tzlocal.get_localzone", return_value=ZoneInfo("America/Bahia")),
        patch("app.core.app_clock.datetime", FixedDatetime),
    ):
        async with use_timezone(session):
            assert app_today().isoformat() == "2026-05-18"


@pytest.mark.asyncio
async def test_invalid_saved_timezone_keeps_repair_endpoint_available(
    client, admin_auth_headers, session, monkeypatch, caplog
):
    monkeypatch.setenv("TZ", "America/Sao_Paulo")
    session.add(AppSetting(key="timezone", value="Unavailable/Zone"))
    await session.commit()

    response = await client.get("/api/setup/status")
    assert response.status_code == 200

    response = await client.get("/api/admin/timezone", headers=admin_auth_headers)
    assert response.status_code == 200
    assert response.json()["timezone"] == "America/Sao_Paulo"
    assert "Saved application timezone is unavailable" in caplog.text

    response = await client.patch(
        "/api/admin/settings/timezone",
        headers=admin_auth_headers,
        json={"value": "UTC"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_admin_can_set_timezone_and_invalid_names_are_rejected(
    client, admin_auth_headers
):
    response = await client.patch(
        "/api/admin/settings/timezone",
        headers=admin_auth_headers,
        json={"value": "America/Sao_Paulo"},
    )
    assert response.status_code == 200

    response = await client.get("/api/admin/timezone", headers=admin_auth_headers)
    assert response.status_code == 200
    assert response.json()["timezone"] == "America/Sao_Paulo"
    assert "Europe/London" in response.json()["available"]

    for value in ("Invalid/Timezone", "../UTC", ""):
        response = await client.patch(
            "/api/admin/settings/timezone",
            headers=admin_auth_headers,
            json={"value": value},
        )
        assert response.status_code == 400


@pytest.mark.asyncio
async def test_context_primer_uses_application_timezone(session, test_user):
    from app.agents.services.context_service import build_context_primer
    from app.core.app_clock import use_timezone

    setting = AppSetting(key="timezone", value="America/Sao_Paulo")
    session.add(setting)
    await session.commit()

    with patch("app.core.app_clock.datetime", FixedDatetime):
        async with use_timezone(session):
            primer = await build_context_primer(session, test_user)

    assert "Today is 2026-05-18 (America/Sao_Paulo)" in primer
    assert "Timezone: America/Sao_Paulo" in primer


@pytest.mark.asyncio
async def test_account_service_uses_application_day_for_dates_and_balances(
    session, test_user, test_workspace, monkeypatch
):
    from decimal import Decimal

    from sqlalchemy import select

    from app.core.app_clock import use_timezone
    from app.models.transaction import Transaction
    from app.schemas.account import AccountCreate
    from app.services.account_service import create_account, get_accounts

    monkeypatch.setenv("TZ", "UTC")
    session.add(AppSetting(key="timezone", value="America/Sao_Paulo"))
    await session.commit()

    with patch("app.core.app_clock.datetime", FixedDatetime):
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
async def test_recurring_generation_waits_until_the_local_due_date(
    session, test_user, test_workspace, test_account, monkeypatch
):
    from app.core.app_clock import use_timezone
    from app.schemas.recurring_transaction import RecurringTransactionCreate
    from app.services.recurring_transaction_service import (
        create_recurring_transaction,
        generate_pending,
    )

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
        async with use_timezone(session):
            assert await generate_pending(session, test_user.id) == 0

        setting.value = "UTC"
        await session.commit()
        async with use_timezone(session):
            assert await generate_pending(session, test_user.id) == 1


@pytest.mark.asyncio
async def test_recurring_fx_worker_uses_local_day_through_commit(
    session, test_user, test_workspace, monkeypatch
):
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
        session.add(
            FxRate(
                base_currency="USD",
                quote_currency="BRL",
                date=date(2026, 5, day),
                rate=rate,
                source="test",
            )
        )
    recurring = RecurringTransaction(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        description="Local FX",
        amount=100,
        currency="BRL",
        type="debit",
        frequency="monthly",
        start_date=date(2026, 5, 19),
        next_occurrence=date(2026, 5, 19),
    )
    session.add(recurring)
    await session.commit()
    maker = async_sessionmaker(session.bind, expire_on_commit=False)

    with (
        patch("app.core.app_clock.datetime", FixedDatetime),
        patch(
            "app.tasks.fx_rate_tasks._make_session_maker",
            return_value=(SimpleNamespace(dispose=AsyncMock()), maker),
        ),
    ):
        assert await _restamp_recurring_fx() == 1

    await session.refresh(recurring)
    assert recurring.amount_primary == Decimal("20.00")
    assert recurring.fx_rate_used == Decimal("0.2")


@pytest.mark.asyncio
async def test_mcp_defaults_use_application_month(session, test_user, test_workspace):
    from mcp_server.auth import CallContext
    from mcp_server.registry import call_tool
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
