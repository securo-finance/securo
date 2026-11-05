"""Coverage-focused tests for app/api/accounts.py.

Exercises list filters, get/create/update/delete, close/reopen, summary,
balance-history and bills endpoints, plus 404/400 error branches and the
non-primary-currency conversion path.
"""

from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.main import app
from app.models.account import Account
from app.models.fx_rate import FxRate


NONEXISTENT = "00000000-0000-0000-0000-000000000000"


# ---------------------------------------------------------------------------
# list_accounts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_accounts_include_closed(client: AsyncClient, auth_headers):
    """A manually-closed account is hidden by default and shown with the flag."""
    create = await client.post(
        "/api/accounts", headers=auth_headers,
        json={"name": "Closeable", "type": "savings", "balance": "10.00"},
    )
    acc_id = create.json()["id"]
    await client.post(f"/api/accounts/{acc_id}/close", headers=auth_headers)

    default = await client.get("/api/accounts", headers=auth_headers)
    assert all(a["id"] != acc_id for a in default.json())

    with_closed = await client.get(
        "/api/accounts?include_closed=true", headers=auth_headers
    )
    assert any(a["id"] == acc_id for a in with_closed.json())


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


# ---------------------------------------------------------------------------
# get_account
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_account_found(client: AsyncClient, auth_headers, test_account: Account):
    resp = await client.get(f"/api/accounts/{test_account.id}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["name"] == "Conta Corrente"


# ---------------------------------------------------------------------------
# create / update / delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_credit_card_account(client: AsyncClient, auth_headers):
    resp = await client.post(
        "/api/accounts", headers=auth_headers,
        json={
            "name": "Visa", "type": "credit_card", "balance": "0.00",
            "credit_limit": "5000.00", "statement_close_day": 10,
            "payment_due_day": 18, "card_brand": "visa",
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["type"] == "credit_card"
    assert data["credit_limit"] == 5000.0


@pytest.mark.asyncio
async def test_update_account_name(client: AsyncClient, auth_headers):
    create = await client.post(
        "/api/accounts", headers=auth_headers,
        json={"name": "Old", "type": "checking", "balance": "0.00"},
    )
    acc_id = create.json()["id"]
    resp = await client.patch(
        f"/api/accounts/{acc_id}", headers=auth_headers, json={"name": "New"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "New"


@pytest.mark.asyncio
async def test_update_account_not_found(client: AsyncClient, auth_headers, test_account):
    resp = await client.patch(
        f"/api/accounts/{NONEXISTENT}", headers=auth_headers, json={"name": "X"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_bank_connected_account_rejected(
    client: AsyncClient, auth_headers, test_account: Account
):
    """test_account is connection-backed; editing the name must 400."""
    resp = await client.patch(
        f"/api/accounts/{test_account.id}", headers=auth_headers,
        json={"name": "Hacked"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_delete_account(client: AsyncClient, auth_headers):
    create = await client.post(
        "/api/accounts", headers=auth_headers,
        json={"name": "Temp", "type": "checking", "balance": "0.00"},
    )
    acc_id = create.json()["id"]
    resp = await client.delete(f"/api/accounts/{acc_id}", headers=auth_headers)
    assert resp.status_code == 204
    # Gone now
    assert (await client.get(f"/api/accounts/{acc_id}", headers=auth_headers)).status_code == 404


@pytest.mark.asyncio
async def test_delete_account_not_found(client: AsyncClient, auth_headers, test_account):
    resp = await client.delete(f"/api/accounts/{NONEXISTENT}", headers=auth_headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_bank_connected_account_rejected(
    client: AsyncClient, auth_headers, test_account: Account
):
    resp = await client.delete(f"/api/accounts/{test_account.id}", headers=auth_headers)
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# close / reopen
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_then_reopen(client: AsyncClient, auth_headers):
    create = await client.post(
        "/api/accounts", headers=auth_headers,
        json={"name": "Lifecycle", "type": "checking", "balance": "0.00"},
    )
    acc_id = create.json()["id"]

    closed = await client.post(f"/api/accounts/{acc_id}/close", headers=auth_headers)
    assert closed.status_code == 200
    assert closed.json()["is_closed"] is True

    # Double-close is a 400
    again = await client.post(f"/api/accounts/{acc_id}/close", headers=auth_headers)
    assert again.status_code == 400

    reopened = await client.post(f"/api/accounts/{acc_id}/reopen", headers=auth_headers)
    assert reopened.status_code == 200
    assert reopened.json()["is_closed"] is False

    # Reopen an open account is a 400
    again2 = await client.post(f"/api/accounts/{acc_id}/reopen", headers=auth_headers)
    assert again2.status_code == 400


@pytest.mark.asyncio
async def test_close_account_not_found(client: AsyncClient, auth_headers, test_account):
    resp = await client.post(f"/api/accounts/{NONEXISTENT}/close", headers=auth_headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_reopen_account_not_found(client: AsyncClient, auth_headers, test_account):
    resp = await client.post(f"/api/accounts/{NONEXISTENT}/reopen", headers=auth_headers)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_account_summary(client: AsyncClient, auth_headers, test_transactions):
    account_id = test_transactions[0].account_id
    resp = await client.get(
        f"/api/accounts/{account_id}/summary", headers=auth_headers
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "current_balance" in data
    assert "monthly_income" in data
    assert "monthly_expenses" in data


@pytest.mark.asyncio
async def test_account_summary_with_date_range(
    client: AsyncClient, auth_headers, test_transactions
):
    account_id = test_transactions[0].account_id
    resp = await client.get(
        f"/api/accounts/{account_id}/summary?from=2020-01-01&to=2020-12-31",
        headers=auth_headers,
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_account_summary_not_found(client: AsyncClient, auth_headers, test_account):
    resp = await client.get(f"/api/accounts/{NONEXISTENT}/summary", headers=auth_headers)
    assert resp.status_code == 404


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
async def test_balance_history(client: AsyncClient, auth_headers, test_transactions):
    account_id = test_transactions[0].account_id
    resp = await client.get(
        f"/api/accounts/{account_id}/balance-history", headers=auth_headers
    )
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_balance_history_not_found(client: AsyncClient, auth_headers, test_account):
    resp = await client.get(
        f"/api/accounts/{NONEXISTENT}/balance-history", headers=auth_headers
    )
    assert resp.status_code == 404


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


# ---------------------------------------------------------------------------
# bills
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_account_bills_empty_for_checking(
    client: AsyncClient, auth_headers, test_account: Account
):
    """Non-CC accounts return an empty bills list (not a 404)."""
    resp = await client.get(f"/api/accounts/{test_account.id}/bills", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_account_bills_not_found(client: AsyncClient, auth_headers, test_account):
    resp = await client.get(f"/api/accounts/{NONEXISTENT}/bills", headers=auth_headers)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_accounts_requires_auth(client: AsyncClient):
    resp = await client.get("/api/accounts")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# exclude_from_reports (PR-5, T-16)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_accounts_api_roundtrips_exclude_from_reports(
    client: AsyncClient, auth_headers, test_account: Account
):
    """GET carries the flag; PATCH sets/clears it on manual AND connected
    accounts (the connected-account editable_fields gate, #343 audience)."""
    # Default: False in AccountRead.
    resp = await client.get(f"/api/accounts/{test_account.id}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["exclude_from_reports"] is False

    # Manual account: create -> set -> read back -> clear.
    create = await client.post(
        "/api/accounts", headers=auth_headers,
        json={"name": "Mirror", "type": "savings", "balance": "10.00"},
    )
    assert create.status_code == 201, create.text
    acc_id = create.json()["id"]
    assert create.json()["exclude_from_reports"] is False

    patched = await client.patch(
        f"/api/accounts/{acc_id}", headers=auth_headers,
        json={"exclude_from_reports": True},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["exclude_from_reports"] is True
    got = await client.get(f"/api/accounts/{acc_id}", headers=auth_headers)
    assert got.json()["exclude_from_reports"] is True

    cleared = await client.patch(
        f"/api/accounts/{acc_id}", headers=auth_headers,
        json={"exclude_from_reports": False},
    )
    assert cleared.status_code == 200
    assert cleared.json()["exclude_from_reports"] is False

    # Connected account: the flag itself is editable...
    connected = await client.patch(
        f"/api/accounts/{test_account.id}", headers=auth_headers,
        json={"exclude_from_reports": True},
    )
    assert connected.status_code == 200, connected.text
    assert connected.json()["exclude_from_reports"] is True
    # ...while provider-owned fields stay rejected by the same gate.
    rejected = await client.patch(
        f"/api/accounts/{test_account.id}", headers=auth_headers,
        json={"name": "Hacked"},
    )
    assert rejected.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize("connected", [False, True], ids=["manual", "connected"])
@pytest.mark.parametrize("excluded", [False, True], ids=["included", "excluded"])
async def test_account_exclusion_null_rejected_without_mutation(
    client: AsyncClient, auth_headers, test_account: Account, connected: bool, excluded: bool
):
    if connected:
        acc_id = str(test_account.id)
    else:
        created = await client.post(
            "/api/accounts", headers=auth_headers,
            json={"name": "Null regression", "type": "checking", "balance": "10.00"},
        )
        assert created.status_code == 201, created.text
        acc_id = created.json()["id"]
    url = f"/api/accounts/{acc_id}"
    seeded = await client.patch(
        url, headers=auth_headers, json={"exclude_from_reports": excluded},
    )
    assert seeded.status_code == 200, seeded.text
    before = await client.get(url, headers=auth_headers)
    assert before.status_code == 200, before.text

    # Observe the HTTP response even if a regression raises inside the app.
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        rejected = await http.patch(
            url, headers=auth_headers,
            json={"exclude_from_reports": None, "display_name": "Must not be saved"},
        )
    assert rejected.status_code == 422, rejected.text
    assert any(
        error["loc"] == ["body", "exclude_from_reports"]
        for error in rejected.json()["detail"]
    )
    after = await client.get(url, headers=auth_headers)
    assert after.status_code == 200, after.text
    assert after.json() == before.json()


@pytest.mark.asyncio
@pytest.mark.parametrize("connected", [False, True], ids=["manual", "connected"])
async def test_account_exclusion_omitted_patch_preserves_boolean_roundtrip(
    client: AsyncClient, auth_headers, test_account: Account, connected: bool
):
    if connected:
        acc_id = str(test_account.id)
    else:
        created = await client.post(
            "/api/accounts", headers=auth_headers,
            json={"name": "Omission regression", "type": "checking", "balance": "10.00"},
        )
        assert created.status_code == 201, created.text
        acc_id = created.json()["id"]
    url = f"/api/accounts/{acc_id}"
    for excluded in (True, False):
        patched = await client.patch(
            url, headers=auth_headers, json={"exclude_from_reports": excluded},
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["exclude_from_reports"] is excluded
        for payload in ({}, {"display_name": "Omitted exclusion"}):
            omitted = await client.patch(url, headers=auth_headers, json=payload)
            assert omitted.status_code == 200, omitted.text
            assert omitted.json()["exclude_from_reports"] is excluded
            got = await client.get(url, headers=auth_headers)
            assert got.status_code == 200, got.text
            assert got.json()["exclude_from_reports"] is excluded


@pytest.mark.asyncio
async def test_viewer_can_read_but_cannot_change_account_exclusion(
    client: AsyncClient, viewer_auth_headers, test_account: Account
):
    response = await client.get(
        f"/api/accounts/{test_account.id}", headers=viewer_auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["exclude_from_reports"] is False

    response = await client.patch(
        f"/api/accounts/{test_account.id}", headers=viewer_auth_headers,
        json={"exclude_from_reports": True},
    )
    assert response.status_code == 403
    unchanged = await client.get(
        f"/api/accounts/{test_account.id}", headers=viewer_auth_headers,
    )
    assert unchanged.json()["exclude_from_reports"] is False
