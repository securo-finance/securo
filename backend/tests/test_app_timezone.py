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
    from app.core.app_clock import app_today, invalidate_timezone_cache, use_timezone

    setting = AppSetting(key="timezone", value="America/Sao_Paulo")
    session.add(setting)
    await session.commit()

    with patch("app.core.app_clock.datetime", FixedDatetime):
        async with use_timezone(session):
            assert app_today().isoformat() == "2026-05-18"

            setting.value = "Asia/Tokyo"
            await session.commit()
            # Writing straight to the table bypasses the admin endpoint, which
            # is what drops the cache; do it by hand like a second process
            # would see it after the cache expires.
            invalidate_timezone_cache()
            assert app_today().isoformat() == "2026-05-18"

        async with use_timezone(session):
            assert app_today().isoformat() == "2026-05-19"


@pytest.mark.asyncio
async def test_saved_timezone_is_cached_between_reads(session, clean_db):
    from app.core.app_clock import get_timezone, invalidate_timezone_cache

    setting = AppSetting(key="timezone", value="America/Sao_Paulo")
    session.add(setting)
    await session.commit()
    assert str(await get_timezone(session)) == "America/Sao_Paulo"

    setting.value = "Asia/Tokyo"
    await session.commit()
    assert str(await get_timezone(session)) == "America/Sao_Paulo"

    invalidate_timezone_cache()
    assert str(await get_timezone(session)) == "Asia/Tokyo"


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
    from app.core.app_clock import invalidate_timezone_cache

    monkeypatch.setenv("TZ", "America/Sao_Paulo")
    session.add(AppSetting(key="timezone", value="Unavailable/Zone"))
    await session.commit()
    invalidate_timezone_cache()

    response = await client.get("/api/setup/status")
    assert response.status_code == 200

    response = await client.get("/api/admin/timezone", headers=admin_auth_headers)
    assert response.status_code == 200
    body = response.json()
    # The endpoint tells the administrator what is saved and what is in use,
    # so a broken value is visible in the UI and not only in the logs.
    assert body["timezone"] == "America/Sao_Paulo"
    assert body["saved"] == "Unavailable/Zone"
    assert body["fallback"] == "America/Sao_Paulo"
    assert "Saved application timezone is unavailable" in caplog.text

    response = await client.patch(
        "/api/admin/settings/timezone",
        headers=admin_auth_headers,
        json={"value": "UTC"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_admin_can_forget_the_saved_timezone(client, admin_auth_headers, monkeypatch):
    monkeypatch.setenv("TZ", "America/Sao_Paulo")
    response = await client.patch(
        "/api/admin/settings/timezone",
        headers=admin_auth_headers,
        json={"value": "Asia/Tokyo"},
    )
    assert response.status_code == 200
    response = await client.get("/api/admin/timezone", headers=admin_auth_headers)
    assert response.json()["saved"] == "Asia/Tokyo"
    assert response.json()["timezone"] == "Asia/Tokyo"

    response = await client.delete("/api/admin/settings/timezone", headers=admin_auth_headers)
    assert response.status_code == 204
    response = await client.get("/api/admin/timezone", headers=admin_auth_headers)
    assert response.json()["saved"] is None
    assert response.json()["timezone"] == "America/Sao_Paulo"

    # Only configurable keys can be forgotten, and only by an administrator.
    response = await client.delete("/api/admin/settings/secret_key", headers=admin_auth_headers)
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_names_that_are_files_but_not_timezones_are_rejected(client, admin_auth_headers):
    # `ZoneInfo("localtime")` loads on many hosts, but it is not a timezone
    # anyone should pick, and it would never round-trip through the picker.
    for value in ("localtime", "posixrules", "Factory"):
        response = await client.patch(
            "/api/admin/settings/timezone",
            headers=admin_auth_headers,
            json={"value": value},
        )
        assert response.status_code == 400, value


@pytest.mark.asyncio
async def test_signed_in_members_can_list_timezones(client, auth_headers, monkeypatch):
    monkeypatch.setenv("TZ", "America/Sao_Paulo")
    response = await client.get("/api/timezones", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["default"] == "America/Sao_Paulo"
    assert "Europe/London" in body["available"]
    assert "localtime" not in body["available"]

    response = await client.get("/api/timezones")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_workspace_keeps_its_own_calendar(
    client, auth_headers, session, test_workspace, monkeypatch
):
    monkeypatch.setenv("TZ", "America/Sao_Paulo")
    headers = {**auth_headers, "X-Workspace-Id": str(test_workspace.id)}

    with patch("app.core.app_clock.datetime", FixedDatetime):
        response = await client.get("/api/dashboard/summary", headers=headers)
        assert response.status_code == 200
        assert response.json()["balance_date"] == "2026-05-18"

        response = await client.patch(
            f"/api/workspaces/{test_workspace.id}",
            headers=auth_headers,
            json={"timezone": "Asia/Tokyo"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["timezone"] == "Asia/Tokyo"

        response = await client.get("/api/dashboard/summary", headers=headers)
        assert response.json()["balance_date"] == "2026-05-19"

        # Null puts the workspace back on the application timezone.
        response = await client.patch(
            f"/api/workspaces/{test_workspace.id}",
            headers=auth_headers,
            json={"timezone": None},
        )
        assert response.status_code == 200, response.text
        assert response.json()["timezone"] is None
        response = await client.get("/api/dashboard/summary", headers=headers)
        assert response.json()["balance_date"] == "2026-05-18"

    response = await client.patch(
        f"/api/workspaces/{test_workspace.id}",
        headers=auth_headers,
        json={"timezone": "Mars/Olympus_Mons"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_recurring_generation_follows_each_workspace_calendar(
    session, test_user, test_workspace, test_account, monkeypatch
):
    from decimal import Decimal

    from app.core.app_clock import use_timezone
    from app.models.account import Account
    from app.schemas.recurring_transaction import RecurringTransactionCreate
    from app.services.recurring_transaction_service import (
        create_recurring_transaction,
        generate_pending,
    )
    from app.services.workspace_service import create_workspace

    monkeypatch.setenv("TZ", "America/Sao_Paulo")
    tokyo = await create_workspace(
        session, name="Tokyo books", creator=test_user, self_membership=True
    )
    tokyo.timezone = "Asia/Tokyo"
    tokyo_account = Account(
        user_id=test_user.id,
        workspace_id=tokyo.id,
        name="Tokyo checking",
        type="checking",
        balance=Decimal("0"),
        currency="JPY",
    )
    session.add(tokyo_account)
    await session.commit()

    for workspace, account in ((test_workspace, test_account), (tokyo, tokyo_account)):
        await create_recurring_transaction(
            session,
            workspace.id,
            test_user.id,
            RecurringTransactionCreate(
                description=f"Due on the 19th in {workspace.name}",
                amount=10,
                type="debit",
                frequency="monthly",
                start_date=date(2026, 5, 19),
                account_id=account.id,
                auto_generate=True,
            ),
        )

    # 01:00 UTC on May 19 is still May 18 in Sao Paulo and already May 19 in
    # Tokyo: only the Tokyo workspace's row is due.
    with patch("app.core.app_clock.datetime", FixedDatetime):
        async with use_timezone(session):
            assert await generate_pending(session, test_user.id) == 1

    from sqlalchemy import select

    from app.models.transaction import Transaction

    generated = (await session.execute(select(Transaction.workspace_id))).scalars().all()
    assert generated == [tokyo.id]


@pytest.mark.asyncio
async def test_mcp_calls_use_the_workspace_calendar(session, test_user, test_workspace):
    from mcp_server.auth import CallContext
    from mcp_server.registry import call_tool
    import mcp_server.tools  # noqa: F401

    class MonthBoundary(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 6, 1, 1, 0, tzinfo=timezone.utc).astimezone(tz)

    session.add(AppSetting(key="timezone", value="America/Sao_Paulo"))
    test_workspace.timezone = "Asia/Tokyo"
    await session.commit()

    with patch("app.core.app_clock.datetime", MonthBoundary):
        result = await call_tool(
            session,
            CallContext(user_id=test_user.id, workspace_id=test_workspace.id),
            "get_budget_vs_actual",
            {},
        )

    assert result["month"] == "2026-06-01"


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
    from app.core.app_clock import invalidate_timezone_cache, use_timezone
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
        invalidate_timezone_cache()
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


@pytest.mark.asyncio
async def test_recurring_job_reads_the_saved_timezone_past_a_stale_cache(
    session, test_user, test_workspace, test_account, monkeypatch
):
    """A worker that cached the old zone must not stamp the old day on rows.

    The admin endpoint only drops the cache of the process that handled the
    save, so the job reads the setting itself instead of trusting the cache.
    """
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.app_clock import get_timezone
    from app.schemas.recurring_transaction import RecurringTransactionCreate
    from app.services.recurring_transaction_service import create_recurring_transaction
    from app.tasks.recurring_tasks import _generate_all

    monkeypatch.setenv("TZ", "UTC")
    await create_recurring_transaction(
        session,
        test_workspace.id,
        test_user.id,
        RecurringTransactionCreate(
            description="Due on the 19th",
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
    # This process caches Sao Paulo, where May 19 has not started yet...
    assert str(await get_timezone(session)) == "America/Sao_Paulo"
    # ...then another process saves UTC, where it has.
    setting.value = "UTC"
    await session.commit()
    assert str(await get_timezone(session)) == "America/Sao_Paulo"

    maker = async_sessionmaker(session.bind, expire_on_commit=False)
    with (
        patch("app.core.app_clock.datetime", FixedDatetime),
        patch(
            "app.tasks.recurring_tasks._make_session_maker",
            return_value=(SimpleNamespace(dispose=AsyncMock()), maker),
        ),
    ):
        assert await _generate_all() == 1
