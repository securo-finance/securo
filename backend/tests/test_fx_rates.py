"""Tests for FX rate service, API endpoints, and multi-currency integration."""

import uuid
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.fx_rate import FxRate
from app.models.transaction import Transaction
from app.models.user import User


# ─── helpers ────────────────────────────────────────────────────────────────


async def _insert_rate(
    session: AsyncSession,
    quote_currency: str,
    rate: Decimal,
    rate_date: date | None = None,
) -> FxRate:
    """Insert a single FxRate record (base=USD)."""
    fx = FxRate(
        base_currency="USD",
        quote_currency=quote_currency,
        date=rate_date or date.today(),
        rate=rate,
        source="test",
    )
    session.add(fx)
    await session.commit()
    await session.refresh(fx)
    return fx


async def _make_account(
    session: AsyncSession,
    user: User,
    currency: str = "USD",
    balance: Decimal = Decimal("1000.00"),
    name: str = "Test USD Account",
) -> Account:
    """Create an account in a specific currency."""
    acct = Account(
        user_id=user.id,
        name=name,
        currency=currency,
        balance=balance,
        type="checking",
    )
    session.add(acct)
    await session.commit()
    await session.refresh(acct)
    return acct


# ─── fixtures ───────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def fx_rates(session: AsyncSession, clean_db) -> list[FxRate]:
    """Insert a realistic set of FX rates for testing."""
    today = date.today()
    rates_data = [
        ("BRL", Decimal("5.0000000000"), today),
        ("EUR", Decimal("0.9200000000"), today),
        ("GBP", Decimal("0.7900000000"), today),
    ]
    rates = []
    for currency, rate, dt in rates_data:
        fx = await _insert_rate(session, currency, rate, dt)
        rates.append(fx)
    return rates


@pytest_asyncio.fixture
async def historical_fx_rates(session: AsyncSession, clean_db) -> list[FxRate]:
    """Insert historical FX rates for a past month."""
    past_date = date(2025, 6, 15)
    rates_data = [
        ("BRL", Decimal("4.8000000000"), past_date),
        ("EUR", Decimal("0.8800000000"), past_date),
    ]
    rates = []
    for currency, rate, dt in rates_data:
        fx = await _insert_rate(session, currency, rate, dt)
        rates.append(fx)
    return rates


# ═══════════════════════════════════════════════════════════════════════════
# 1. FX RATE SERVICE — UNIT TESTS
# ═══════════════════════════════════════════════════════════════════════════


class TestGetRate:
    """Tests for fx_rate_service.get_rate()."""

    @pytest.mark.asyncio
    async def test_same_currency_returns_one(self, session: AsyncSession):
        from app.services.fx_rate_service import get_rate

        rate = await get_rate(session, "BRL", "BRL")
        assert rate == Decimal("1")

    @pytest.mark.asyncio
    async def test_same_currency_no_rates_needed(self, session: AsyncSession):
        """Same currency should return 1 even without any FxRate records."""
        from app.services.fx_rate_service import get_rate

        rate = await get_rate(session, "USD", "USD")
        assert rate == Decimal("1")

    @pytest.mark.asyncio
    async def test_usd_to_brl(self, session: AsyncSession, fx_rates):
        from app.services.fx_rate_service import get_rate

        rate = await get_rate(session, "USD", "BRL")
        assert rate == Decimal("5.0000000000")

    @pytest.mark.asyncio
    async def test_brl_to_usd(self, session: AsyncSession, fx_rates):
        from app.services.fx_rate_service import get_rate

        rate = await get_rate(session, "BRL", "USD")
        # BRL→USD: usd_to_target(USD)=1 / usd_to_source(BRL)=5 = 0.2
        expected = Decimal("1") / Decimal("5.0000000000")
        assert rate == expected

    @pytest.mark.asyncio
    async def test_cross_currency_eur_to_brl(self, session: AsyncSession, fx_rates):
        from app.services.fx_rate_service import get_rate

        rate = await get_rate(session, "EUR", "BRL")
        # EUR→BRL: usd_to_BRL / usd_to_EUR = 5.0 / 0.92
        expected = Decimal("5.0000000000") / Decimal("0.9200000000")
        assert rate == expected

    @pytest.mark.asyncio
    async def test_cross_currency_gbp_to_eur(self, session: AsyncSession, fx_rates):
        from app.services.fx_rate_service import get_rate

        rate = await get_rate(session, "GBP", "EUR")
        # GBP→EUR: usd_to_EUR / usd_to_GBP = 0.92 / 0.79
        expected = Decimal("0.9200000000") / Decimal("0.7900000000")
        assert rate == expected

    @pytest.mark.asyncio
    async def test_no_rates_returns_one_as_fallback(self, session: AsyncSession):
        from app.services.fx_rate_service import get_rate

        rate = await get_rate(session, "JPY", "CHF")
        assert rate == Decimal("1")

    @pytest.mark.asyncio
    async def test_historical_month_uses_closing_rate(
        self, session: AsyncSession, historical_fx_rates
    ):
        from app.services.fx_rate_service import get_rate

        rate = await get_rate(session, "USD", "BRL", target_date=date(2025, 6, 20))
        # Should find the June 2025 rate (4.8)
        assert rate == Decimal("4.8000000000")

    @pytest.mark.asyncio
    async def test_historical_cross_currency(
        self, session: AsyncSession, historical_fx_rates
    ):
        from app.services.fx_rate_service import get_rate

        rate = await get_rate(session, "EUR", "BRL", target_date=date(2025, 6, 20))
        expected = Decimal("4.8000000000") / Decimal("0.8800000000")
        assert rate == expected


class TestResolveRate:
    """Tests for fx_rate_service._resolve_rate() (returns None, no fake 1:1)."""

    @pytest.mark.asyncio
    async def test_returns_none_when_no_rate(self, session: AsyncSession):
        from app.services.fx_rate_service import _resolve_rate

        rate = await _resolve_rate(session, "JPY", "CHF", allow_fetch=False)
        assert rate is None

    @pytest.mark.asyncio
    async def test_same_currency_returns_one(self, session: AsyncSession):
        from app.services.fx_rate_service import _resolve_rate

        rate = await _resolve_rate(session, "EUR", "EUR", allow_fetch=False)
        assert rate == Decimal("1")

    @pytest.mark.asyncio
    async def test_resolves_real_rate(self, session: AsyncSession, fx_rates):
        from app.services.fx_rate_service import _resolve_rate

        rate = await _resolve_rate(session, "USD", "BRL", allow_fetch=False)
        assert rate == Decimal("5.0000000000")

    @pytest.mark.asyncio
    async def test_get_rate_still_falls_back_to_one_for_live_reads(
        self, session: AsyncSession
    ):
        """get_rate keeps the 1:1 fallback so balances/dashboards still render."""
        from app.services.fx_rate_service import get_rate

        rate = await get_rate(session, "JPY", "CHF", allow_fetch=False)
        assert rate == Decimal("1")


class TestConvert:
    """Tests for fx_rate_service.convert()."""

    @pytest.mark.asyncio
    async def test_same_currency(self, session: AsyncSession):
        from app.services.fx_rate_service import convert

        converted, rate = await convert(session, Decimal("100.00"), "BRL", "BRL")
        assert converted == Decimal("100.00")
        assert rate == Decimal("1")

    @pytest.mark.asyncio
    async def test_usd_to_brl(self, session: AsyncSession, fx_rates):
        from app.services.fx_rate_service import convert

        converted, rate = await convert(session, Decimal("100.00"), "USD", "BRL")
        assert converted == Decimal("500.00")
        assert rate == Decimal("5.0000000000")

    @pytest.mark.asyncio
    async def test_brl_to_usd(self, session: AsyncSession, fx_rates):
        from app.services.fx_rate_service import convert

        converted, rate = await convert(session, Decimal("500.00"), "BRL", "USD")
        assert converted == Decimal("100.00")

    @pytest.mark.asyncio
    async def test_cross_currency_rounds_to_two_decimals(
        self, session: AsyncSession, fx_rates
    ):
        from app.services.fx_rate_service import convert

        converted, rate = await convert(session, Decimal("100.00"), "EUR", "BRL")
        # 100 * (5.0 / 0.92) = 543.478...  → rounded to 543.48
        assert converted == Decimal("543.48")


class TestStampPrimaryAmount:
    """Tests for fx_rate_service.stamp_primary_amount()."""

    @pytest.mark.asyncio
    async def test_stamps_transaction_same_currency(
        self, session: AsyncSession, test_user: User, test_account: Account
    ):
        """Transaction in BRL (user primary=BRL) → amount_primary = amount."""
        from app.services.fx_rate_service import stamp_primary_amount

        txn = Transaction(
            user_id=test_user.id,
            account_id=test_account.id,
            description="Test BRL",
            amount=Decimal("100.00"),
            currency="BRL",
            date=date.today(),
            type="debit",
            source="manual",
        )
        session.add(txn)
        await session.flush()

        await stamp_primary_amount(session, test_user.id, txn)
        assert txn.amount_primary == Decimal("100.00")
        assert txn.fx_rate_used == Decimal("1")

    @pytest.mark.asyncio
    async def test_stamps_transaction_different_currency(
        self, session: AsyncSession, test_user: User, test_account: Account, fx_rates
    ):
        """Transaction in USD (user primary=BRL) → converts using FX rate."""
        from app.services.fx_rate_service import stamp_primary_amount

        txn = Transaction(
            user_id=test_user.id,
            account_id=test_account.id,
            description="Test USD",
            amount=Decimal("50.00"),
            currency="USD",
            date=date.today(),
            type="debit",
            source="manual",
        )
        session.add(txn)
        await session.flush()

        await stamp_primary_amount(session, test_user.id, txn)
        assert txn.amount_primary == Decimal("250.00")  # 50 * 5.0
        assert txn.fx_rate_used == Decimal("5.0000000000")

    @pytest.mark.asyncio
    async def test_no_rate_leaves_null_not_fake_1to1(
        self, session: AsyncSession, test_user: User, test_account: Account
    ):
        """Cross-currency tx with no rate available → amount_primary/fx stay NULL (issue #353)."""
        from app.services.fx_rate_service import stamp_primary_amount

        txn = Transaction(
            user_id=test_user.id,
            account_id=test_account.id,
            description="No rate USD",
            amount=Decimal("50.00"),
            currency="USD",  # user primary is BRL, no fx_rates fixture
            date=date.today(),
            type="debit",
            source="manual",
        )
        session.add(txn)
        await session.flush()

        await stamp_primary_amount(session, test_user.id, txn, allow_fetch=False)
        # No fake 1:1 persisted — honestly NULL so it can self-heal later.
        assert txn.amount_primary is None
        assert txn.fx_rate_used is None

    @pytest.mark.asyncio
    async def test_heals_legacy_1to1_fallback_row(
        self, session: AsyncSession, test_user: User, test_account: Account, fx_rates
    ):
        """A row wrongly stamped 1:1 gets re-stamped with the real rate once available."""
        from app.services.fx_rate_service import stamp_primary_amount

        # Simulate a legacy fallback row: USD amount stamped 1:1 into BRL.
        txn = Transaction(
            user_id=test_user.id,
            account_id=test_account.id,
            description="Legacy fallback",
            amount=Decimal("100.00"),
            currency="USD",
            date=date.today(),
            type="debit",
            source="manual",
            amount_primary=Decimal("100.00"),
            fx_rate_used=Decimal("1"),
        )
        session.add(txn)
        await session.flush()

        # fx_rates fixture has USD→BRL = 5.0; heal without hitting the provider.
        await stamp_primary_amount(session, test_user.id, txn, allow_fetch=False)
        assert txn.amount_primary == Decimal("500.00")  # 100 * 5.0
        assert txn.fx_rate_used == Decimal("5.0000000000")

    @pytest.mark.asyncio
    async def test_same_currency_1to1_preserved(
        self, session: AsyncSession, test_user: User, test_account: Account
    ):
        """Genuine same-currency 1:1 row is never nulled by re-stamping."""
        from app.services.fx_rate_service import stamp_primary_amount

        txn = Transaction(
            user_id=test_user.id,
            account_id=test_account.id,
            description="BRL 1:1",
            amount=Decimal("100.00"),
            currency="BRL",  # matches user primary
            date=date.today(),
            type="debit",
            source="manual",
            amount_primary=Decimal("100.00"),
            fx_rate_used=Decimal("1"),
        )
        session.add(txn)
        await session.flush()

        await stamp_primary_amount(session, test_user.id, txn, allow_fetch=False)
        assert txn.amount_primary == Decimal("100.00")
        assert txn.fx_rate_used == Decimal("1")

    @pytest.mark.asyncio
    async def test_stamps_with_no_user_returns_early(self, session: AsyncSession):
        """Non-existent user → does nothing."""
        from app.services.fx_rate_service import stamp_primary_amount

        class FakeObj:
            amount = Decimal("100")
            amount_primary = None
            fx_rate_used = None
            currency = "USD"
            date = date.today()

        obj = FakeObj()
        await stamp_primary_amount(session, uuid.uuid4(), obj)
        assert obj.amount_primary is None


# ═══════════════════════════════════════════════════════════════════════════
# 2. FX RATE API ENDPOINT TESTS
# ═══════════════════════════════════════════════════════════════════════════


class TestFxRatesAPI:
    """Tests for /api/fx-rates endpoints."""

    @pytest.mark.asyncio
    async def test_status_empty(
        self, client: AsyncClient, auth_headers
    ):
        response = await client.get("/api/fx-rates/status", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["last_sync_date"] is None
        assert data["total_rates"] == 0

    @pytest.mark.asyncio
    async def test_status_with_rates(
        self, client: AsyncClient, auth_headers, fx_rates
    ):
        response = await client.get("/api/fx-rates/status", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["last_sync_date"] == date.today().isoformat()
        assert data["total_rates"] == 3

    @pytest.mark.asyncio
    async def test_refresh_calls_sync(
        self, client: AsyncClient, auth_headers
    ):
        mock_sync = AsyncMock(return_value=150)
        with patch("app.api.fx_rates.sync_rates", mock_sync):
            response = await client.post(
                "/api/fx-rates/refresh", headers=auth_headers
            )
        assert response.status_code == 200
        data = response.json()
        assert data["synced"] is True
        assert data["rates_count"] == 150
        assert data["date"] == date.today().isoformat()
        mock_sync.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_status_unauthenticated(self, client: AsyncClient):
        response = await client.get("/api/fx-rates/status")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_refresh_unauthenticated(self, client: AsyncClient):
        response = await client.post("/api/fx-rates/refresh")
        assert response.status_code == 401


# ═══════════════════════════════════════════════════════════════════════════
# 3. INTEGRATION: TRANSACTION CREATION WITH FX STAMPING
# ═══════════════════════════════════════════════════════════════════════════


class TestTransactionFxIntegration:
    """Test that creating transactions via API stamps amount_primary."""

    @pytest.mark.asyncio
    async def test_create_transaction_stamps_primary_same_currency(
        self,
        client: AsyncClient,
        auth_headers,
        test_account: Account,
    ):
        """Creating a BRL transaction for a BRL-preference user stamps amount_primary = amount."""
        response = await client.post(
            "/api/transactions",
            headers=auth_headers,
            json={
                "account_id": str(test_account.id),
                "description": "Almoço",
                "amount": "32.50",
                "date": date.today().isoformat(),
                "type": "debit",
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["amount_primary"] == 32.50
        assert data["fx_rate_used"] == 1.0

    @pytest.mark.asyncio
    async def test_create_transaction_stamps_primary_different_currency(
        self,
        client: AsyncClient,
        auth_headers,
        test_user: User,
        session: AsyncSession,
        fx_rates,
    ):
        """Creating a USD transaction converts to BRL using FX rates."""
        # Create a USD account
        usd_account = await _make_account(session, test_user, currency="USD")

        response = await client.post(
            "/api/transactions",
            headers=auth_headers,
            json={
                "account_id": str(usd_account.id),
                "description": "Amazon purchase",
                "amount": "20.00",
                "date": date.today().isoformat(),
                "type": "debit",
                "currency": "USD",
            },
        )
        assert response.status_code == 201
        data = response.json()
        # USD→BRL at rate 5.0 → 20 * 5 = 100
        assert data["amount_primary"] == 100.00
        assert data["fx_rate_used"] is not None


# ═══════════════════════════════════════════════════════════════════════════
# 4. DASHBOARD MULTI-CURRENCY TOTALS
# ═══════════════════════════════════════════════════════════════════════════


class TestDashboardMultiCurrency:
    """Test that dashboard consolidates multi-currency balances."""

    @pytest.mark.asyncio
    async def test_dashboard_summary_with_multi_currency(
        self,
        session: AsyncSession,
        test_user: User,
        test_workspace,
        test_account: Account,
        fx_rates,
    ):
        """total_balance_primary sums all currencies into primary (BRL)."""
        from app.services.dashboard_service import get_summary

        # test_account is BRL (manual, balance from transactions)
        # Create a USD account with an opening balance transaction
        usd_account = await _make_account(
            session, test_user, currency="USD", balance=Decimal("200.00")
        )
        # Manual accounts compute balance from transactions, so add one
        txn = Transaction(
            user_id=test_user.id,
            account_id=usd_account.id,
            description="Opening balance",
            amount=Decimal("200.00"),
            currency="USD",
            date=date.today(),
            type="credit",
            source="opening_balance",
        )
        session.add(txn)
        await session.commit()

        summary = await get_summary(session, test_workspace.id, test_user.id)

        assert summary.primary_currency == "BRL"
        # USD balance: 200 * 5.0 = 1000 in BRL
        # BRL balance from test_account transactions (may be 0 if no txns)
        # Key assertion: USD portion is converted correctly
        assert summary.total_balance_primary >= 1000.0
        assert "USD" in summary.total_balance

    @pytest.mark.asyncio
    async def test_dashboard_summary_single_currency(
        self,
        client: AsyncClient,
        auth_headers,
        test_account: Account,
    ):
        """Single-currency dashboard still returns primary fields."""
        response = await client.get("/api/dashboard/summary", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()

        assert data["primary_currency"] == "BRL"
        assert "total_balance_primary" in data
        # Single BRL account → primary equals the BRL total
        assert data["total_balance_primary"] == pytest.approx(1500.00, abs=1.0)


# ═══════════════════════════════════════════════════════════════════════════
# 5. OPEN EXCHANGE RATES PROVIDER
# ═══════════════════════════════════════════════════════════════════════════


class TestOpenExchangeRatesProvider:
    """Tests for the OER provider (mocked HTTP calls)."""

    @pytest.mark.asyncio
    async def test_fetch_latest(self):
        from app.providers.openexchangerates import OpenExchangeRatesProvider

        provider = OpenExchangeRatesProvider()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "base": "USD",
            "rates": {"BRL": 5.1, "EUR": 0.93},
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.providers.openexchangerates.get_settings") as mock_settings, \
             patch("app.providers.openexchangerates.httpx.AsyncClient", return_value=mock_client):
            mock_settings.return_value.openexchangerates_app_id = "test-key"
            rates = await provider.fetch_latest()

        assert "BRL" in rates
        assert rates["BRL"] == Decimal("5.1")
        assert "EUR" in rates
        assert rates["EUR"] == Decimal("0.93")

    @pytest.mark.asyncio
    async def test_fetch_latest_no_api_key_raises(self):
        from app.providers.openexchangerates import OpenExchangeRatesProvider

        provider = OpenExchangeRatesProvider()
        with patch("app.providers.openexchangerates.get_settings") as mock_settings:
            mock_settings.return_value.openexchangerates_app_id = ""
            with pytest.raises(ValueError, match="not configured"):
                await provider.fetch_latest()

    @pytest.mark.asyncio
    async def test_fetch_historical(self):
        from app.providers.openexchangerates import OpenExchangeRatesProvider

        provider = OpenExchangeRatesProvider()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "base": "USD",
            "rates": {"BRL": 4.9, "GBP": 0.78},
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.providers.openexchangerates.get_settings") as mock_settings, \
             patch("app.providers.openexchangerates.httpx.AsyncClient", return_value=mock_client):
            mock_settings.return_value.openexchangerates_app_id = "test-key"
            rates = await provider.fetch_historical(date(2025, 6, 15))

        assert rates["BRL"] == Decimal("4.9")
        assert rates["GBP"] == Decimal("0.78")
        # Verify the correct URL was called
        call_args = mock_client.get.call_args
        assert "2025-06-15" in call_args[0][0]


# ═══════════════════════════════════════════════════════════════════════════
# 6. SYNC RATES (mocked provider — pg_insert incompatible with SQLite)
# ═══════════════════════════════════════════════════════════════════════════


class TestSyncRates:
    """Tests for fx_rate_service.sync_rates() with mocked provider."""

    @pytest.mark.asyncio
    async def test_sync_rates_calls_provider_latest(self):
        from app.services.fx_rate_service import sync_rates

        mock_provider = MagicMock()
        mock_provider.name = "test_provider"
        mock_provider.fetch_latest = AsyncMock(return_value={
            "BRL": Decimal("5.0"),
            "EUR": Decimal("0.92"),
        })

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock()
        mock_session.commit = AsyncMock()

        with patch("app.services.fx_rate_service._provider", mock_provider):
            count = await sync_rates(mock_session, date.today())

        mock_provider.fetch_latest.assert_awaited_once()
        assert count == 2
        assert mock_session.execute.await_count == 2
        mock_session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sync_rates_calls_provider_historical(self):
        from app.services.fx_rate_service import sync_rates

        target = date(2025, 6, 15)
        mock_provider = MagicMock()
        mock_provider.name = "test_provider"
        mock_provider.fetch_historical = AsyncMock(return_value={
            "BRL": Decimal("4.8"),
        })

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock()
        mock_session.commit = AsyncMock()

        with patch("app.services.fx_rate_service._provider", mock_provider):
            count = await sync_rates(mock_session, target)

        mock_provider.fetch_historical.assert_awaited_once_with(target)
        assert count == 1

    @pytest.mark.asyncio
    async def test_sync_rates_defaults_to_today(self):
        from app.services.fx_rate_service import sync_rates

        mock_provider = MagicMock()
        mock_provider.name = "test_provider"
        mock_provider.fetch_latest = AsyncMock(return_value={"BRL": Decimal("5.0")})

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock()
        mock_session.commit = AsyncMock()

        with patch("app.services.fx_rate_service._provider", mock_provider):
            await sync_rates(mock_session)

        # Should call fetch_latest (not fetch_historical) when no date given
        mock_provider.fetch_latest.assert_awaited_once()


# ═══════════════════════════════════════════════════════════════════════════
# 7. STAMP_PRIMARY_AMOUNT EDGE CASES
# ═══════════════════════════════════════════════════════════════════════════


class TestStampEdgeCases:
    """Edge cases for stamp_primary_amount."""

    @pytest.mark.asyncio
    async def test_null_amount_returns_early(
        self, session: AsyncSession, test_user: User
    ):
        """Object with None amount → does nothing."""
        from app.services.fx_rate_service import stamp_primary_amount

        class FakeObj:
            amount = None
            amount_primary = None
            fx_rate_used = None
            currency = "USD"
            date = date.today()

        obj = FakeObj()
        await stamp_primary_amount(session, test_user.id, obj)
        assert obj.amount_primary is None

    @pytest.mark.asyncio
    async def test_custom_field_names(
        self, session: AsyncSession, test_user: User, test_account: Account, fx_rates
    ):
        """stamp_primary_amount works with custom field names (e.g. for assets)."""
        from app.services.fx_rate_service import stamp_primary_amount

        class FakeAsset:
            purchase_price = Decimal("100.00")
            purchase_price_primary = None
            currency = "USD"
            purchase_date = date.today()

        obj = FakeAsset()
        await stamp_primary_amount(
            session, test_user.id, obj,
            amount_field="purchase_price",
            primary_field="purchase_price_primary",
            rate_field="fx_rate_used",
            date_field="purchase_date",
        )
        assert obj.purchase_price_primary == Decimal("500.00")

    @pytest.mark.asyncio
    async def test_missing_currency_field_defaults_to_default_currency(
        self, session: AsyncSession, test_user: User, fx_rates
    ):
        """Object without a currency field falls back to the default currency (USD)
        and converts to the user's primary (BRL) at the real rate."""
        from app.services.fx_rate_service import stamp_primary_amount

        class FakeObj:
            amount = Decimal("100.00")
            amount_primary = None
            fx_rate_used = None
            date = date.today()
            # No 'currency' attribute → defaults to settings.default_currency (USD)

        obj = FakeObj()
        await stamp_primary_amount(session, test_user.id, obj, allow_fetch=False)
        # USD→BRL at 5.0
        assert obj.amount_primary == Decimal("500.00")
        assert obj.fx_rate_used == Decimal("5.0000000000")


# ═══════════════════════════════════════════════════════════════════════════
# 8. TRANSACTION UPDATE RE-STAMPING
# ═══════════════════════════════════════════════════════════════════════════


class TestTransactionUpdateRestamp:
    """Test that updating a transaction re-stamps amount_primary when needed."""

    @pytest.mark.asyncio
    async def test_update_amount_restamps(
        self, client: AsyncClient, auth_headers, test_account: Account
    ):
        """Changing amount triggers re-stamp."""
        # Create a BRL transaction
        resp = await client.post(
            "/api/transactions",
            headers=auth_headers,
            json={
                "account_id": str(test_account.id),
                "description": "Test",
                "amount": "50.00",
                "date": date.today().isoformat(),
                "type": "debit",
            },
        )
        txn_id = resp.json()["id"]
        assert resp.json()["amount_primary"] == 50.0

        # Update the amount
        resp2 = await client.patch(
            f"/api/transactions/{txn_id}",
            headers=auth_headers,
            json={"amount": "75.00"},
        )
        assert resp2.status_code == 200
        assert resp2.json()["amount_primary"] == 75.0

    @pytest.mark.asyncio
    async def test_update_category_does_not_restamp(
        self, client: AsyncClient, auth_headers, test_account: Account, test_categories
    ):
        """Changing only category does NOT trigger re-stamp."""
        resp = await client.post(
            "/api/transactions",
            headers=auth_headers,
            json={
                "account_id": str(test_account.id),
                "description": "Test",
                "amount": "50.00",
                "date": date.today().isoformat(),
                "type": "debit",
            },
        )
        txn_id = resp.json()["id"]
        original_rate = resp.json()["fx_rate_used"]

        # Update only category — should not re-stamp
        resp2 = await client.patch(
            f"/api/transactions/{txn_id}",
            headers=auth_headers,
            json={"category_id": str(test_categories[0].id)},
        )
        assert resp2.status_code == 200
        assert resp2.json()["fx_rate_used"] == original_rate

    @pytest.mark.asyncio
    async def test_update_currency_restamps(
        self,
        client: AsyncClient,
        auth_headers,
        test_account: Account,
        fx_rates,
    ):
        """Changing currency triggers re-stamp with new FX rate."""
        resp = await client.post(
            "/api/transactions",
            headers=auth_headers,
            json={
                "account_id": str(test_account.id),
                "description": "Test",
                "amount": "100.00",
                "date": date.today().isoformat(),
                "type": "debit",
            },
        )
        txn_id = resp.json()["id"]
        assert resp.json()["amount_primary"] == 100.0  # BRL→BRL = 1:1

        # Change currency to USD — should re-stamp with USD→BRL rate
        resp2 = await client.patch(
            f"/api/transactions/{txn_id}",
            headers=auth_headers,
            json={"currency": "USD"},
        )
        assert resp2.status_code == 200
        # 100 USD * 5.0 = 500 BRL
        assert resp2.json()["amount_primary"] == 500.0


# ═══════════════════════════════════════════════════════════════════════════
# 9. REPORT SERVICE — NET WORTH WITH MULTI-CURRENCY
# ═══════════════════════════════════════════════════════════════════════════


class TestReportNetWorthMultiCurrency:
    """Test that net worth report converts multi-currency accounts."""

    @pytest.mark.asyncio
    async def test_net_worth_at_converts_currencies(
        self,
        session: AsyncSession,
        test_user: User,
        test_workspace,
        test_account: Account,
        fx_rates,
    ):
        """_net_worth_at converts each account balance to primary currency."""
        from app.services.report_service import _net_worth_at

        # Create USD account with a transaction
        usd_account = await _make_account(
            session, test_user, currency="USD", balance=Decimal("300.00")
        )
        txn = Transaction(
            user_id=test_user.id,
            account_id=usd_account.id,
            description="Opening",
            amount=Decimal("300.00"),
            currency="USD",
            date=date.today(),
            type="credit",
            source="opening_balance",
        )
        session.add(txn)
        await session.commit()

        dp = await _net_worth_at(session, test_workspace.id, date.today(), "BRL")

        # USD: 300 * 5.0 = 1500 BRL
        # Total should include the USD portion
        assert dp.value >= 1500.0
        assert dp.breakdowns["accounts"] >= 1500.0

    @pytest.mark.asyncio
    async def test_net_worth_report_uses_user_primary_currency(
        self,
        session: AsyncSession,
        test_user: User,
        test_workspace,
        test_account: Account,
    ):
        """get_net_worth_report reads the user's currency preference."""
        from app.services.report_service import get_net_worth_report

        report = await get_net_worth_report(session, test_workspace.id, test_user.id, months=1)

        # test_user has currency_display=BRL
        assert report is not None
        assert len(report.trend) >= 1


# ═══════════════════════════════════════════════════════════════════════════
# 10. RECURRING TRANSACTION STAMPING
# ═══════════════════════════════════════════════════════════════════════════


class TestRecurringTransactionStamping:
    """Test that recurring transactions get FX stamping."""

    @pytest.mark.asyncio
    async def test_create_recurring_stamps_primary(
        self,
        session: AsyncSession,
        test_user: User,
        test_workspace,
        test_account: Account,
        fx_rates,
    ):
        """Creating a recurring transaction in USD stamps amount_primary in BRL."""
        from app.schemas.recurring_transaction import RecurringTransactionCreate
        from app.services.recurring_transaction_service import create_recurring_transaction

        data = RecurringTransactionCreate(
            description="Monthly subscription",
            amount=Decimal("10.00"),
            type="debit",
            frequency="monthly",
            start_date=date.today(),
            account_id=test_account.id,
            currency="USD",
        )
        rec = await create_recurring_transaction(session, test_workspace.id, test_user.id, data)

        assert rec.amount_primary is not None
        # USD→BRL: 10 * 5.0 = 50
        assert rec.amount_primary == Decimal("50.00")
        assert rec.fx_rate_used == Decimal("5.0000000000")

    @pytest.mark.asyncio
    async def test_create_recurring_same_currency(
        self,
        session: AsyncSession,
        test_user: User,
        test_workspace,
        test_account: Account,
    ):
        """Recurring in BRL (user primary=BRL) → rate = 1."""
        from app.schemas.recurring_transaction import RecurringTransactionCreate
        from app.services.recurring_transaction_service import create_recurring_transaction

        data = RecurringTransactionCreate(
            description="Rent",
            amount=Decimal("2000.00"),
            type="debit",
            frequency="monthly",
            start_date=date.today(),
            account_id=test_account.id,
            currency="BRL",
        )
        rec = await create_recurring_transaction(session, test_workspace.id, test_user.id, data)

        assert rec.amount_primary == Decimal("2000.00")
        assert rec.fx_rate_used == Decimal("1")

    @pytest.mark.asyncio
    async def test_generate_pending_stamps_transactions(
        self,
        session: AsyncSession,
        test_user: User,
        test_workspace,
        test_account: Account,
    ):
        """generate_pending creates transactions with amount_primary stamped."""
        from sqlalchemy import select as sa_select

        from app.schemas.recurring_transaction import RecurringTransactionCreate
        from app.services.recurring_transaction_service import (
            create_recurring_transaction,
            generate_pending,
        )

        start = date.today() - timedelta(days=35)
        data = RecurringTransactionCreate(
            description="Weekly sub",
            amount=Decimal("15.00"),
            type="debit",
            frequency="weekly",
            start_date=start,
            account_id=test_account.id,
            currency="BRL",
        )
        await create_recurring_transaction(session, test_workspace.id, test_user.id, data)

        count = await generate_pending(session, test_user.id, up_to=date.today())
        assert count >= 1

        # Verify generated transactions have amount_primary stamped
        result = await session.execute(
            sa_select(Transaction).where(
                Transaction.user_id == test_user.id,
                Transaction.source == "recurring",
            )
        )
        txns = result.scalars().all()
        assert len(txns) >= 1
        for txn in txns:
            assert txn.amount_primary is not None
            assert txn.amount_primary == Decimal("15.00")


# ═══════════════════════════════════════════════════════════════════════════
# 11. BACKFILL TASK (core logic, mocked session)
# ═══════════════════════════════════════════════════════════════════════════


class TestBackfillTask:
    """Test the backfill logic with mocked infrastructure."""

    @pytest.mark.asyncio
    async def test_backfill_stamps_null_transactions(
        self,
        session: AsyncSession,
        test_user: User,
        test_account: Account,
        fx_rates,
    ):
        """Transactions with amount_primary=NULL get backfilled."""
        from app.services.fx_rate_service import convert

        # Create transaction with NULL amount_primary (simulating pre-migration data)
        txn = Transaction(
            user_id=test_user.id,
            account_id=test_account.id,
            description="Old transaction",
            amount=Decimal("100.00"),
            currency="USD",
            date=date.today(),
            type="debit",
            source="manual",
            amount_primary=None,
            fx_rate_used=None,
        )
        session.add(txn)
        await session.commit()
        await session.refresh(txn)

        assert txn.amount_primary is None

        # Simulate what backfill does: convert and stamp
        primary_currency = (test_user.preferences or {}).get("currency_display", "BRL")
        converted, rate = await convert(
            session, Decimal(str(txn.amount)),
            txn.currency, primary_currency, txn.date,
        )
        txn.amount_primary = converted
        txn.fx_rate_used = rate
        await session.commit()

        assert txn.amount_primary == Decimal("500.00")  # 100 USD * 5.0
        assert txn.fx_rate_used == Decimal("5.0000000000")

    @pytest.mark.asyncio
    async def test_backfill_skips_already_stamped(
        self,
        session: AsyncSession,
        test_user: User,
        test_account: Account,
    ):
        """Transactions with existing amount_primary should not be re-stamped by backfill query."""
        from sqlalchemy import select

        txn = Transaction(
            user_id=test_user.id,
            account_id=test_account.id,
            description="Already stamped",
            amount=Decimal("50.00"),
            currency="BRL",
            date=date.today(),
            type="debit",
            source="manual",
            amount_primary=Decimal("50.00"),
            fx_rate_used=Decimal("1"),
        )
        session.add(txn)
        await session.commit()

        # The backfill query filters amount_primary IS NULL
        result = await session.execute(
            select(Transaction).where(
                Transaction.amount_primary.is_(None),
                Transaction.user_id == test_user.id,
            )
        )
        null_txns = result.scalars().all()

        # Our stamped transaction should not appear
        assert txn.id not in [t.id for t in null_txns]

    @pytest.mark.asyncio
    async def test_heal_selection_covers_1to1_fallback_rows(
        self,
        session: AsyncSession,
        test_user: User,
        test_account: Account,
    ):
        """The widened heal query (NULL OR fx_rate_used==1, cross-currency) picks up
        legacy 1:1 fallback rows but skips genuine same-currency 1:1 rows (issue #353)."""
        from sqlalchemy import or_, select

        primary = test_user.primary_currency  # BRL

        # (a) cross-currency row wrongly stamped 1:1 → should be selected
        fallback = Transaction(
            user_id=test_user.id, account_id=test_account.id,
            description="USD fallback", amount=Decimal("100.00"), currency="USD",
            date=date.today(), type="debit", source="manual",
            amount_primary=Decimal("100.00"), fx_rate_used=Decimal("1"),
        )
        # (b) never-stamped cross-currency row → should be selected
        null_row = Transaction(
            user_id=test_user.id, account_id=test_account.id,
            description="EUR null", amount=Decimal("10.00"), currency="EUR",
            date=date.today(), type="debit", source="manual",
            amount_primary=None, fx_rate_used=None,
        )
        # (c) genuine same-currency 1:1 row → must NOT be selected/healed
        same_ccy = Transaction(
            user_id=test_user.id, account_id=test_account.id,
            description="BRL 1:1", amount=Decimal("50.00"), currency="BRL",
            date=date.today(), type="debit", source="manual",
            amount_primary=Decimal("50.00"), fx_rate_used=Decimal("1"),
        )
        session.add_all([fallback, null_row, same_ccy])
        await session.commit()

        result = await session.execute(
            select(Transaction).where(
                or_(
                    Transaction.amount_primary.is_(None),
                    Transaction.fx_rate_used == 1,
                )
            )
        )
        candidates = [
            tx for tx in result.scalars().all() if tx.currency != primary
        ]
        ids = {tx.id for tx in candidates}
        assert fallback.id in ids
        assert null_row.id in ids
        assert same_ccy.id not in ids

    @pytest.mark.asyncio
    async def test_healer_never_nulls_existing_row_without_rate(
        self,
        session: AsyncSession,
        test_user: User,
        test_account: Account,
    ):
        """Re-stamping must NEVER downgrade an existing 1:1 row to NULL when no real
        rate is available — that would push a visible transaction into limbo
        (issue #353). stamp_primary_amount only upgrades; otherwise it leaves the
        row exactly as-is (this is what makes the periodic healer safe)."""
        from decimal import Decimal as D

        from app.services.fx_rate_service import stamp_primary_amount

        # USD row stamped 1:1 (legacy fallback). No fx_rates fixture → no rate.
        tx = Transaction(
            user_id=test_user.id, account_id=test_account.id,
            description="USD legacy 1:1", amount=D("100.00"), currency="USD",
            date=date.today(), type="debit", source="manual",
            amount_primary=D("100.00"), fx_rate_used=D("1"),
        )
        session.add(tx)
        await session.flush()

        # Re-stamp with no rate available — must leave the row untouched.
        await stamp_primary_amount(session, test_user.id, tx, allow_fetch=False)

        # Still visible with its previous value, not NULL.
        assert tx.amount_primary == D("100.00")
        assert tx.fx_rate_used == D("1")


# ═══════════════════════════════════════════════════════════════════════════
# 12. RECURRING FX RE-STAMP
# ═══════════════════════════════════════════════════════════════════════════


class TestRecurringFxRestamp:
    """Test that recurring transactions get re-stamped with latest FX rates."""

    @pytest.mark.asyncio
    async def test_restamp_updates_with_new_rate(
        self,
        session: AsyncSession,
        test_user: User,
        test_workspace,
        test_account: Account,
        fx_rates,
    ):
        """Re-stamping a USD recurring transaction updates amount_primary with latest rate."""
        from app.schemas.recurring_transaction import RecurringTransactionCreate
        from app.services.recurring_transaction_service import create_recurring_transaction
        from app.services.fx_rate_service import stamp_primary_amount

        data = RecurringTransactionCreate(
            description="USD subscription",
            amount=Decimal("10.00"),
            type="debit",
            frequency="monthly",
            start_date=date.today(),
            account_id=test_account.id,
            currency="USD",
        )
        rec = await create_recurring_transaction(session, test_workspace.id, test_user.id, data)
        assert rec.amount_primary == Decimal("50.00")  # 10 * 5.0

        # Simulate rate change: update the USD rate to 5.5
        from sqlalchemy import update as sa_update

        await session.execute(
            sa_update(FxRate).where(
                FxRate.quote_currency == "BRL",
            ).values(rate=Decimal("5.5000000000"))
        )
        await session.commit()

        # Re-stamp
        await stamp_primary_amount(session, test_user.id, rec, date_field="start_date")
        await session.commit()

        assert rec.amount_primary == Decimal("55.00")  # 10 * 5.5
        assert rec.fx_rate_used == Decimal("5.5000000000")

    @pytest.mark.asyncio
    async def test_restamp_skips_same_currency(
        self,
        session: AsyncSession,
        test_user: User,
        test_workspace,
        test_account: Account,
    ):
        """BRL recurring transaction is not affected by re-stamp (rate stays 1)."""
        from app.schemas.recurring_transaction import RecurringTransactionCreate
        from app.services.recurring_transaction_service import create_recurring_transaction

        data = RecurringTransactionCreate(
            description="BRL rent",
            amount=Decimal("2000.00"),
            type="debit",
            frequency="monthly",
            start_date=date.today(),
            account_id=test_account.id,
            currency="BRL",
        )
        rec = await create_recurring_transaction(session, test_workspace.id, test_user.id, data)
        assert rec.amount_primary == Decimal("2000.00")
        assert rec.fx_rate_used == Decimal("1")


# ═══════════════════════════════════════════════════════════════════════════
# 13. MANUAL FX OVERRIDE
# ═══════════════════════════════════════════════════════════════════════════


class TestManualFxOverride:
    """Test manual FX override on create and update."""

    @pytest.mark.asyncio
    async def test_create_with_both_overrides(
        self,
        client: AsyncClient,
        auth_headers,
        test_user: User,
        session: AsyncSession,
        fx_rates,
    ):
        """Providing both amount_primary and fx_rate_used uses exact values."""
        usd_account = await _make_account(session, test_user, currency="USD")
        response = await client.post(
            "/api/transactions",
            headers=auth_headers,
            json={
                "account_id": str(usd_account.id),
                "description": "Manual FX both",
                "amount": "100.00",
                "date": date.today().isoformat(),
                "type": "debit",
                "currency": "USD",
                "amount_primary": "520.00",
                "fx_rate_used": "5.2",
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["amount_primary"] == 520.0
        assert data["fx_rate_used"] == 5.2

    @pytest.mark.asyncio
    async def test_create_with_only_fx_rate(
        self,
        client: AsyncClient,
        auth_headers,
        test_user: User,
        session: AsyncSession,
        fx_rates,
    ):
        """Providing only fx_rate_used derives amount_primary."""
        usd_account = await _make_account(session, test_user, currency="USD")
        response = await client.post(
            "/api/transactions",
            headers=auth_headers,
            json={
                "account_id": str(usd_account.id),
                "description": "Manual FX rate only",
                "amount": "50.00",
                "date": date.today().isoformat(),
                "type": "debit",
                "currency": "USD",
                "fx_rate_used": "5.3",
            },
        )
        assert response.status_code == 201
        data = response.json()
        # 50 * 5.3 = 265.00
        assert data["amount_primary"] == 265.0
        assert data["fx_rate_used"] == 5.3

    @pytest.mark.asyncio
    async def test_create_with_only_amount_primary(
        self,
        client: AsyncClient,
        auth_headers,
        test_user: User,
        session: AsyncSession,
        fx_rates,
    ):
        """Providing only amount_primary derives fx_rate_used."""
        usd_account = await _make_account(session, test_user, currency="USD")
        response = await client.post(
            "/api/transactions",
            headers=auth_headers,
            json={
                "account_id": str(usd_account.id),
                "description": "Manual FX amount only",
                "amount": "80.00",
                "date": date.today().isoformat(),
                "type": "debit",
                "currency": "USD",
                "amount_primary": "400.00",
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["amount_primary"] == 400.0
        # 400 / 80 = 5.0
        assert data["fx_rate_used"] == 5.0

    @pytest.mark.asyncio
    async def test_update_with_fx_override_skips_auto_stamp(
        self,
        client: AsyncClient,
        auth_headers,
        test_user: User,
        session: AsyncSession,
        fx_rates,
    ):
        """Update with FX override uses manual values, not auto-stamp."""
        usd_account = await _make_account(session, test_user, currency="USD")
        resp = await client.post(
            "/api/transactions",
            headers=auth_headers,
            json={
                "account_id": str(usd_account.id),
                "description": "Will override",
                "amount": "100.00",
                "date": date.today().isoformat(),
                "type": "debit",
                "currency": "USD",
            },
        )
        txn_id = resp.json()["id"]

        # Update with manual FX override
        resp2 = await client.patch(
            f"/api/transactions/{txn_id}",
            headers=auth_headers,
            json={
                "amount_primary": "550.00",
                "fx_rate_used": "5.5",
            },
        )
        assert resp2.status_code == 200
        data = resp2.json()
        assert data["amount_primary"] == 550.0
        assert data["fx_rate_used"] == 5.5

    @pytest.mark.asyncio
    async def test_update_clear_fx_override_restamps(
        self,
        client: AsyncClient,
        auth_headers,
        test_user: User,
        session: AsyncSession,
        fx_rates,
    ):
        usd_account = await _make_account(session, test_user, currency="USD")
        resp = await client.post(
            "/api/transactions",
            headers=auth_headers,
            json={
                "account_id": str(usd_account.id),
                "description": "Clear override",
                "amount": "100.00",
                "date": date.today().isoformat(),
                "type": "debit",
                "currency": "USD",
                "amount_primary": "550.00",
                "fx_rate_used": "5.5",
            },
        )
        assert resp.status_code == 201
        txn_id = resp.json()["id"]

        resp2 = await client.patch(
            f"/api/transactions/{txn_id}",
            headers=auth_headers,
            json={"amount_primary": None, "fx_rate_used": None},
        )
        assert resp2.status_code == 200
        data = resp2.json()
        assert data["amount_primary"] == 500.0
        assert data["fx_rate_used"] == 5.0

    @pytest.mark.asyncio
    async def test_update_without_fx_override_auto_stamps(
        self,
        client: AsyncClient,
        auth_headers,
        test_user: User,
        session: AsyncSession,
        fx_rates,
    ):
        """Update without FX override still auto-stamps (backward compat)."""
        usd_account = await _make_account(session, test_user, currency="USD")
        resp = await client.post(
            "/api/transactions",
            headers=auth_headers,
            json={
                "account_id": str(usd_account.id),
                "description": "Auto stamp test",
                "amount": "100.00",
                "date": date.today().isoformat(),
                "type": "debit",
                "currency": "USD",
            },
        )
        txn_id = resp.json()["id"]

        # Update amount without FX override → should auto re-stamp
        resp2 = await client.patch(
            f"/api/transactions/{txn_id}",
            headers=auth_headers,
            json={"amount": "200.00"},
        )
        assert resp2.status_code == 200
        data = resp2.json()
        # 200 * 5.0 = 1000
        assert data["amount_primary"] == 1000.0


class TestFxFallbackFlag:
    """Test that fx_fallback is set correctly in API responses."""

    @pytest.mark.asyncio
    async def test_foreign_currency_no_rates_returns_fx_fallback_true(
        self,
        client: AsyncClient,
        auth_headers,
        test_user: User,
        session: AsyncSession,
    ):
        """Cross-currency transaction with no FX rates → left NULL, fx_fallback=True (issue #353)."""
        usd_account = await _make_account(session, test_user, currency="USD")

        resp = await client.post(
            "/api/transactions",
            headers=auth_headers,
            json={
                "account_id": str(usd_account.id),
                "description": "No FX rate available",
                "amount": "50.00",
                "date": date.today().isoformat(),
                "type": "debit",
                "currency": "USD",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        # No FX rate available → we honestly leave it unconverted (NULL) rather
        # than persisting a fake 1:1 conversion that would never self-heal.
        assert data["amount_primary"] is None
        assert data["fx_rate_used"] is None
        assert data["fx_fallback"] is True

    @pytest.mark.asyncio
    async def test_foreign_currency_with_rates_returns_fx_fallback_false(
        self,
        client: AsyncClient,
        auth_headers,
        test_user: User,
        session: AsyncSession,
        fx_rates,
    ):
        """Cross-currency transaction with valid FX rates → fx_fallback=False."""
        usd_account = await _make_account(session, test_user, currency="USD")

        resp = await client.post(
            "/api/transactions",
            headers=auth_headers,
            json={
                "account_id": str(usd_account.id),
                "description": "Has FX rate",
                "amount": "20.00",
                "date": date.today().isoformat(),
                "type": "debit",
                "currency": "USD",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["fx_rate_used"] == 5.0
        assert data["fx_fallback"] is False

    @pytest.mark.asyncio
    async def test_same_currency_returns_fx_fallback_false(
        self,
        client: AsyncClient,
        auth_headers,
        test_user: User,
        session: AsyncSession,
    ):
        """Same-currency transaction → fx_fallback=False (not applicable)."""
        brl_account = await _make_account(
            session, test_user, currency="BRL", name="BRL Account",
        )

        resp = await client.post(
            "/api/transactions",
            headers=auth_headers,
            json={
                "account_id": str(brl_account.id),
                "description": "Same currency",
                "amount": "100.00",
                "date": date.today().isoformat(),
                "type": "debit",
                "currency": "BRL",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["fx_fallback"] is False

    @pytest.mark.asyncio
    async def test_list_endpoint_tags_fx_fallback(
        self,
        client: AsyncClient,
        auth_headers,
        test_user: User,
        session: AsyncSession,
    ):
        """GET /api/transactions tags fx_fallback on listed items."""
        usd_account = await _make_account(session, test_user, currency="USD")

        # Create a cross-currency transaction with no rates → left unconverted
        resp = await client.post(
            "/api/transactions",
            headers=auth_headers,
            json={
                "account_id": str(usd_account.id),
                "description": "Fallback in list",
                "amount": "10.00",
                "date": date.today().isoformat(),
                "type": "debit",
                "currency": "USD",
            },
        )
        assert resp.status_code == 201

        # List transactions
        resp2 = await client.get("/api/transactions", headers=auth_headers)
        assert resp2.status_code == 200
        items = resp2.json()["items"]
        fallback_items = [i for i in items if i["description"] == "Fallback in list"]
        assert len(fallback_items) == 1
        assert fallback_items[0]["amount_primary"] is None
        assert fallback_items[0]["fx_fallback"] is True
