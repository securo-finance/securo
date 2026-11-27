"""Foreign-currency conversion on the accounts list, summary and balance history."""

from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.models.account import Account
from app.models.fx_rate import FxRate


# ---------------------------------------------------------------------------
# list_accounts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_accounts_foreign_currency_conversion(
    client: AsyncClient, auth_headers, session: AsyncSession
):
    """A non-primary-currency account is converted with the stored rate."""
    session.add(FxRate(
        base_currency="USD", quote_currency="BRL", date=app_today(),
        rate=Decimal("5.0"), source="test",
    ))
    await session.commit()
    await client.post(
        "/api/accounts", headers=auth_headers,
        json={"name": "USD Wallet", "type": "checking", "balance": "100.00", "currency": "USD"},
    )
    resp = await client.get("/api/accounts", headers=auth_headers)
    assert resp.status_code == 200
    usd = next(a for a in resp.json() if a["name"] == "USD Wallet")
    assert usd["balance_primary"] == pytest.approx(500.0)
    assert usd["fx_fallback"] is False


@pytest.mark.asyncio
async def test_list_accounts_foreign_currency_without_rate(
    client: AsyncClient, auth_headers
):
    """Without a real rate the balance is not presented as converted (#1165).

    The 1:1 fallback used to come back as balance_primary, so the dashboard
    showed a EUR balance with the primary currency's symbol and summed it as
    if it were already converted.
    """
    await client.post(
        "/api/accounts", headers=auth_headers,
        json={"name": "EUR Wallet", "type": "checking", "balance": "100.00", "currency": "EUR"},
    )
    resp = await client.get("/api/accounts", headers=auth_headers)
    assert resp.status_code == 200
    eur = next(a for a in resp.json() if a["name"] == "EUR Wallet")
    assert eur["balance_primary"] is None
    assert eur["fx_fallback"] is True


@pytest.mark.asyncio
async def test_list_accounts_primary_currency_has_no_fallback_flag(
    client: AsyncClient, auth_headers, test_account: Account
):
    resp = await client.get("/api/accounts", headers=auth_headers)
    brl = next(a for a in resp.json() if a["id"] == str(test_account.id))
    assert brl["balance_primary"] is None
    assert brl["fx_fallback"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["rsd", " RSD", "Rsd "])
async def test_list_accounts_converts_a_non_canonical_currency_code(
    client: AsyncClient, auth_headers, session: AsyncSession, code: str
):
    """A code stored in another case or with spaces still finds its rate (#1474).

    The rate lookup matched the code literally, so an account saved as "rsd"
    got no rate, fell back to its raw amount and was summed 1:1 into a EUR
    total, while the reverse direction (a canonical EUR account under an RSD
    primary) converted fine.
    """
    session.add_all([
        FxRate(base_currency="USD", quote_currency="RSD", date=app_today(),
               rate=Decimal("100"), source="test"),
        FxRate(base_currency="USD", quote_currency="BRL", date=app_today(),
               rate=Decimal("5"), source="test"),
    ])
    await session.commit()
    await client.post(
        "/api/accounts", headers=auth_headers,
        json={"name": "Wallet", "type": "checking", "balance": "-1000.00", "currency": code},
    )
    resp = await client.get("/api/accounts", headers=auth_headers)
    wallet = next(a for a in resp.json() if a["name"] == "Wallet")
    assert wallet["fx_fallback"] is False
    assert wallet["balance_primary"] == pytest.approx(-50.0)


# ---------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_account_summary_foreign_currency_conversion(
    client: AsyncClient, auth_headers, session: AsyncSession
):
    """The summary converts a foreign account with the stored rate."""
    session.add(FxRate(
        base_currency="USD", quote_currency="BRL", date=app_today(),
        rate=Decimal("5.0"), source="test",
    ))
    await session.commit()
    created = await client.post(
        "/api/accounts", headers=auth_headers,
        json={"name": "USD Wallet", "type": "checking", "balance": "100.00", "currency": "USD"},
    )
    resp = await client.get(
        f"/api/accounts/{created.json()['id']}/summary", headers=auth_headers
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["current_balance_primary"] == pytest.approx(500.0)
    assert data["fx_fallback"] is False


@pytest.mark.asyncio
async def test_account_summary_foreign_currency_without_rate(
    client: AsyncClient, auth_headers
):
    """Without a real rate the summary does not return 1:1 values (#1165).

    Otherwise the detail page would show a converted balance for an account
    the accounts list shows in its native currency.
    """
    created = await client.post(
        "/api/accounts", headers=auth_headers,
        json={"name": "EUR Wallet", "type": "checking", "balance": "100.00", "currency": "EUR"},
    )
    resp = await client.get(
        f"/api/accounts/{created.json()['id']}/summary", headers=auth_headers
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["current_balance"] == pytest.approx(100.0)
    assert data["current_balance_primary"] is None
    assert data["opening_balance_primary"] is None
    assert data["fx_fallback"] is True


# ---------------------------------------------------------------------------
# balance-history
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_balance_history_foreign_currency_conversion(
    client: AsyncClient, auth_headers, session: AsyncSession
):
    """Each history point is converted with the stored rate."""
    session.add(FxRate(
        base_currency="USD", quote_currency="BRL", date=app_today(),
        rate=Decimal("5.0"), source="test",
    ))
    await session.commit()
    created = await client.post(
        "/api/accounts", headers=auth_headers,
        json={"name": "USD Wallet", "type": "checking", "balance": "100.00", "currency": "USD"},
    )
    resp = await client.get(
        f"/api/accounts/{created.json()['id']}/balance-history", headers=auth_headers
    )
    assert resp.status_code == 200
    points = resp.json()
    assert points
    for point in points:
        assert point["balance_primary"] == pytest.approx(point["balance"] * 5)
        assert "fx_fallback" not in point


@pytest.mark.asyncio
async def test_balance_history_foreign_currency_without_rate(
    client: AsyncClient, auth_headers
):
    """Without a real rate no point carries a 1:1 balance_primary (#1165)."""
    created = await client.post(
        "/api/accounts", headers=auth_headers,
        json={"name": "EUR Wallet", "type": "checking", "balance": "100.00", "currency": "EUR"},
    )
    resp = await client.get(
        f"/api/accounts/{created.json()['id']}/balance-history", headers=auth_headers
    )
    assert resp.status_code == 200
    points = resp.json()
    assert points
    for point in points:
        assert "balance_primary" not in point
        assert point["fx_fallback"] is True
