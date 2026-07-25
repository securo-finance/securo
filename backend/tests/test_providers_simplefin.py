"""Unit tests for the SimpleFIN provider.

The bridge is fully fakeable via ``httpx.MockTransport`` — no SimpleFIN
credentials needed, no network. Each test stands up the smallest payload
required and asserts the parse / dispatch behavior we care about.
"""
from __future__ import annotations

import base64
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

import httpx
import pytest

from app.providers.base import ProviderUserActionRequired, SessionExpiredError
from app.providers.simplefin import (
    SimpleFinProvider,
    _accounts_url_and_auth,
    _decode_setup_token,
    _epoch_to_date,
)


def _encode_token(url: str) -> str:
    return base64.b64encode(url.encode("utf-8")).decode("ascii")


def _patched_client(handler):
    """Replace SimpleFinProvider._client with one wired to a MockTransport."""

    transport = httpx.MockTransport(handler)

    async def fake_client(self, credentials=None):  # noqa: ANN001
        return httpx.AsyncClient(transport=transport, timeout=30)

    return patch.object(SimpleFinProvider, "_client", fake_client)


# ----- pure helpers -----------------------------------------------------------


def test_decode_setup_token_round_trips():
    raw = _encode_token("https://bridge.simplefin.org/simplefin/claim/abc123")
    assert (
        _decode_setup_token(raw)
        == "https://bridge.simplefin.org/simplefin/claim/abc123"
    )


def test_decode_setup_token_strips_whitespace_and_repads():
    raw = _encode_token("https://bridge.simplefin.org/simplefin/claim/xyz")
    # Strip padding to simulate a copy-pasted token, surround with whitespace.
    sloppy = "  " + raw.rstrip("=") + "\n  "
    assert "claim/xyz" in _decode_setup_token(sloppy)


def test_decode_setup_token_rejects_empty():
    with pytest.raises(ValueError):
        _decode_setup_token("   ")


def test_decode_setup_token_rejects_non_url():
    with pytest.raises(ValueError):
        _decode_setup_token(_encode_token("ftp://nope.example"))


def test_decode_setup_token_rejects_garbage():
    with pytest.raises(ValueError):
        _decode_setup_token("this-is-not-base64!!!@@@")


def test_epoch_to_date_handles_unset():
    assert _epoch_to_date(None) is None
    assert _epoch_to_date("") is None
    assert _epoch_to_date(0) is None


def test_epoch_to_date_parses_seconds():
    assert _epoch_to_date(1672531200) == date(2023, 1, 1)


def test_accounts_url_and_auth_strips_userinfo():
    url, auth = _accounts_url_and_auth("https://u:p@bridge.example/simplefin")
    assert url == "https://bridge.example/simplefin/accounts"
    assert auth == ("u", "p")


# ----- claim flow -------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_oauth_callback_claims_and_parses_accounts():
    """Token paste → claim → first /accounts → ConnectionData."""

    state = {"step": "claim"}

    def handler(request: httpx.Request) -> httpx.Response:
        if state["step"] == "claim":
            assert request.method == "POST"
            assert request.url.path.endswith("/simplefin/claim/demo")
            state["step"] = "accounts"
            return httpx.Response(200, text="https://u:p@bridge.example/simplefin")
        # /accounts request
        assert request.method == "GET"
        assert request.url.path == "/simplefin/accounts"
        return httpx.Response(
            200,
            json={
                "errlist": [],
                "connections": [
                    {"conn_id": "CON-1", "name": "Demo Bank"}
                ],
                "accounts": [
                    {
                        "id": "acc-1",
                        "name": "Checking",
                        "currency": "USD",
                        "balance": "1234.56",
                        "conn_id": "CON-1",
                        "transactions": [],
                        "holdings": [],
                    }
                ],
            },
        )

    provider = SimpleFinProvider()
    token = _encode_token("https://bridge.example/simplefin/claim/demo")
    with _patched_client(handler):
        conn = await provider.handle_oauth_callback(token)

    assert conn.external_id == "CON-1"
    assert conn.institution_name == "Demo Bank"
    assert "access_url_enc" in conn.credentials
    # The plaintext URL must never end up in credentials.
    assert "u:p@" not in str(conn.credentials.get("access_url_enc"))
    assert len(conn.accounts) == 1
    acc = conn.accounts[0]
    assert acc.external_id == "acc-1"
    assert acc.balance == Decimal("1234.56")
    assert acc.currency == "USD"


@pytest.mark.asyncio
async def test_handle_oauth_callback_403_signals_reused_token():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="token already used")

    provider = SimpleFinProvider()
    token = _encode_token("https://bridge.example/simplefin/claim/demo")
    with _patched_client(handler):
        with pytest.raises(ProviderUserActionRequired) as exc:
            await provider.handle_oauth_callback(token)
    assert exc.value.code == "setup_token_used"


# ----- error mapping ----------------------------------------------------------


@pytest.mark.asyncio
async def test_auth_errlist_raises_user_action_required():
    """SimpleFIN ``con.auth`` / ``gen.auth`` → user must regenerate the token."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "errlist": [
                    {"code": "con.auth", "msg": "Authentication failed", "conn_id": "C"}
                ],
                "accounts": [],
            },
        )

    creds = {"access_url_enc": None, "access_url": "https://u:p@bridge.example/simplefin"}
    provider = SimpleFinProvider()
    with _patched_client(handler):
        with pytest.raises(ProviderUserActionRequired) as exc:
            await provider.get_accounts(creds)
    assert exc.value.code == "credentials_invalid"


@pytest.mark.asyncio
async def test_act_failed_is_soft_warning(caplog):
    """``act.failed`` is transient — keep going, just log."""
    import logging as stdlogging

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "errlist": [
                    {"code": "act.failed", "msg": "transient", "account_id": "X"}
                ],
                "accounts": [
                    {
                        "id": "acc-1",
                        "name": "Checking",
                        "currency": "USD",
                        "balance": "10.00",
                    }
                ],
            },
        )

    creds = {"access_url": "https://u:p@bridge.example/simplefin"}
    provider = SimpleFinProvider()
    with caplog.at_level(stdlogging.WARNING, logger="app.providers.simplefin"), _patched_client(handler):
        accounts = await provider.get_accounts(creds)
    assert len(accounts) == 1
    assert any("act.failed" in rec.getMessage() for rec in caplog.records)


@pytest.mark.asyncio
async def test_401_response_signals_credentials_invalid():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="nope")

    creds = {"access_url": "https://u:p@bridge.example/simplefin"}
    with _patched_client(handler):
        with pytest.raises(ProviderUserActionRequired):
            await SimpleFinProvider().get_accounts(creds)


@pytest.mark.asyncio
async def test_accounts_request_moves_url_userinfo_to_auth_header():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.userinfo == b""
        assert "authorization" in request.headers
        return httpx.Response(200, json={"accounts": []})

    creds = {"access_url": "https://u:p@bridge.example/simplefin"}
    with _patched_client(handler):
        await SimpleFinProvider().get_accounts(creds)


@pytest.mark.asyncio
async def test_missing_access_url_raises_session_expired():
    with pytest.raises(SessionExpiredError):
        await SimpleFinProvider().get_accounts({})


# ----- transactions -----------------------------------------------------------


@pytest.mark.asyncio
async def test_get_transactions_filters_by_account_and_parses_signs():
    """Negative amount → debit; positive → credit."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/simplefin/accounts"
        assert "u:p@" not in str(request.url)
        assert request.headers["authorization"].startswith("Basic ")
        # We always request a specific account
        assert request.url.params.get("account") == "acc-1"
        assert request.url.params.get("pending") == "1"
        return httpx.Response(
            200,
            json={
                "accounts": [
                    {
                        "id": "acc-1",
                        "currency": "USD",
                        "balance": "0",
                        "transactions": [
                            {
                                "id": "t1",
                                "amount": "-12.34",
                                "posted": 1672531200,  # 2023-01-01 UTC
                                "description": "Coffee",
                                "payee": "Cafe",
                            },
                            {
                                "id": "t2",
                                "amount": "100.00",
                                "posted": 1672617600,  # 2023-01-02 UTC
                                "description": "Payroll",
                                "pending": True,
                            },
                        ],
                    },
                    {  # noise — a different account in the same response
                        "id": "acc-2",
                        "transactions": [
                            {"id": "tX", "amount": "5", "posted": 1672531200},
                        ],
                    },
                ]
            },
        )

    creds = {"access_url": "https://u:p@bridge.example/simplefin"}
    provider = SimpleFinProvider()
    with _patched_client(handler):
        txns = await provider.get_transactions(
            creds, "acc-1", since=date(2023, 1, 1)
        )
    by_id = {t.external_id: t for t in txns}
    assert set(by_id) == {"t1", "t2"}
    assert by_id["t1"].type == "debit"
    assert by_id["t1"].amount == Decimal("12.34")
    assert by_id["t1"].status == "posted"
    assert by_id["t2"].status == "pending"
    assert by_id["t2"].type == "credit"


@pytest.mark.asyncio
async def test_get_transactions_uses_transacted_at_when_posted_zero():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "accounts": [
                    {
                        "id": "acc-1",
                        "transactions": [
                            {
                                "id": "t1",
                                "amount": "-12.34",
                                "posted": 0,
                                "transacted_at": 1672617600,
                                "description": "Coffee",
                            },
                        ],
                    },
                ]
            },
        )

    creds = {"access_url": "https://u:p@bridge.example/simplefin"}
    with _patched_client(handler):
        txns = await SimpleFinProvider().get_transactions(
            creds, "acc-1", since=date(2023, 1, 1)
        )

    assert txns[0].date == date(2023, 1, 2)


@pytest.mark.asyncio
async def test_get_transactions_chunks_long_windows():
    """``since`` more than 90 days ago → multiple requests with shifting windows."""

    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(
            (
                request.url.params.get("start-date", ""),
                request.url.params.get("end-date", ""),
            )
        )
        return httpx.Response(
            200, json={"accounts": [{"id": "acc-1", "transactions": []}]}
        )

    creds = {"access_url": "https://u:p@bridge.example/simplefin"}
    today = date.today()
    long_ago = today - timedelta(days=200)
    with _patched_client(handler):
        await SimpleFinProvider().get_transactions(creds, "acc-1", since=long_ago)
    assert len(calls) >= 3  # 200 days / 90-day window


# ----- holdings ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_holdings_parses_investment_data():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "accounts": [
                    {
                        "id": "acc-1",
                        "currency": "USD",
                        "holdings": [
                            {
                                "id": "h-1",
                                "description": "Apple",
                                "symbol": "AAPL",
                                "market_value": "105884.80",
                                "shares": "550.0",
                                "purchase_price": "0.10",
                                "cost_basis": "55.00",
                            },
                            {  # no market value → dropped
                                "id": "h-2",
                                "description": "Mystery",
                                "shares": "1",
                            },
                        ],
                    }
                ]
            },
        )

    creds = {"access_url": "https://u:p@bridge.example/simplefin"}
    with _patched_client(handler):
        holdings = await SimpleFinProvider().get_holdings(creds)
    assert len(holdings) == 1
    h = holdings[0]
    assert h.external_id == "h-1"
    assert h.current_value == Decimal("105884.80")
    assert h.quantity == Decimal("550.0")
    assert (h.metadata or {}).get("symbol") == "AAPL"
    # Also promoted to the dedicated column, not just the metadata blob.
    assert h.ticker == "AAPL"


@pytest.mark.asyncio
async def test_get_holdings_crypto_ticker_currency_falls_back_to_account_currency():
    """A connector-supplied ticker like ``DOGE`` isn't an ISO currency code.

    ``HoldingData.currency`` maps to a ``VARCHAR(3)`` DB column — writing a
    4-letter ticker there overflows and used to crash the entire sync for
    the account (see issue #448). It should fall back to the account's
    currency instead.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "accounts": [
                    {
                        "id": "acc-1",
                        "currency": "USD",
                        "holdings": [
                            {
                                "id": "h-crypto",
                                "description": "Dogecoin",
                                "symbol": "DOGE",
                                "currency": "DOGE",
                                "market_value": "42.00",
                                "shares": "100",
                            },
                        ],
                    }
                ]
            },
        )

    creds = {"access_url": "https://u:p@bridge.example/simplefin"}
    with _patched_client(handler):
        holdings = await SimpleFinProvider().get_holdings(creds)
    assert len(holdings) == 1
    assert holdings[0].currency == "USD"
    # The ticker itself belongs in the dedicated 32-char column, not in
    # `currency` — that's the pairing issue #448 asks for.
    assert holdings[0].ticker == "DOGE"


@pytest.mark.asyncio
async def test_get_accounts_non_iso_currency_falls_back_to_usd():
    """The same connector quirk on an *account* would overflow accounts.currency.

    Accounts are upserted before holdings during a sync, so an unguarded
    account currency crashes the connection before the holdings guard is
    ever reached.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "accounts": [
                    {
                        "id": "acc-1",
                        "name": "Crypto Wallet",
                        "currency": "DOGE",
                        "balance": "10.00",
                        "transactions": [],
                    }
                ]
            },
        )

    creds = {"access_url": "https://u:p@bridge.example/simplefin"}
    with _patched_client(handler):
        accounts = await SimpleFinProvider().get_accounts(creds)
    assert accounts[0].currency == "USD"


def test_build_transaction_non_iso_currency_is_none():
    """A bogus transaction currency resolves to None, not a bad 3-char write.

    The sync layer reads `txn_data.currency or acc_data.currency or
    user_currency`, so None correctly defers to the account's currency.
    """
    raw = {
        "id": "t1",
        "amount": "-1.00",
        "posted": 1672531200,
        "currency": "DOGE",
        "description": "buy",
    }
    txn = SimpleFinProvider._build_transaction(raw, "description")
    assert txn is not None
    assert txn.currency is None
    # A real ISO code still passes through, normalized.
    ok = SimpleFinProvider._build_transaction({**raw, "currency": "eur"}, "description")
    assert ok.currency == "EUR"


# ----- misc -------------------------------------------------------------------


def test_flow_type_is_token():
    p = SimpleFinProvider()
    assert p.flow_type == "token"
    assert p.name == "simplefin"


def test_get_oauth_url_raises_for_token_flow():
    with pytest.raises(NotImplementedError):
        SimpleFinProvider().get_oauth_url("https://x", "state")
