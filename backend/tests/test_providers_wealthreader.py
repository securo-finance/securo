"""Unit tests for the Wealth Reader provider.

HTTP is mocked with httpx.MockTransport. PKCE lives in the in-process
fallback so tests do not need Redis.
"""
from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal
from unittest.mock import patch

import httpx
import pytest

from app.providers.base import SessionExpiredError
from app.providers.wealthreader import (
    WealthreaderProvider,
    _pending_pkce,
    generate_pkce,
)


@pytest.fixture
def wr_settings(monkeypatch):
    monkeypatch.setenv("WEALTHREADER_API_KEY", "11dfdeab")
    monkeypatch.setenv("WEALTHREADER_API_URL", "https://api.wealthreader.com")
    monkeypatch.setenv("WEALTHREADER_OAUTH_URL", "https://oauth.wealthreader.com")
    monkeypatch.setenv("WEALTHREADER_OAUTH_REDIRECT_URI", "https://app.example.com/oauth/callback")
    from app.core.config import get_settings

    get_settings.cache_clear()
    _pending_pkce.clear()

    async def _no_redis():
        raise RuntimeError("redis disabled in wealthreader unit tests")

    monkeypatch.setattr("app.core.redis.get_redis", _no_redis)
    monkeypatch.setattr("app.services.oauth_state.get_redis", _no_redis)
    yield
    get_settings.cache_clear()
    _pending_pkce.clear()


def _patch_client(provider: WealthreaderProvider, handler):
    transport = httpx.MockTransport(handler)

    def fake_client():
        return httpx.AsyncClient(transport=transport, timeout=30.0)

    return patch.object(provider, "_client", side_effect=fake_client)


def test_generate_pkce_matches_wealthreader_contract():
    challenge = generate_pkce("bbva")
    assert len(challenge["nonce"]) == 82
    assert len(challenge["code_verifier"]) == 82
    assert (
        challenge["challenge"]
        == hashlib.sha256(challenge["code_verifier"].encode("ascii")).hexdigest()
    )
    raw = bytes.fromhex(challenge["wr_conf"])
    assert b'"bbva"' in raw
    assert b"wait_full_response" in raw


@pytest.mark.asyncio
async def test_list_institutions_maps_entities(wr_settings):
    provider = WealthreaderProvider()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/entities/")
        return httpx.Response(
            200,
            json={
                "success": True,
                "payload": [
                    {"code": "bbva", "name": "BBVA", "country_code": "ES", "logo": "https://l/bbva.png"},
                    {"code": "caixabank", "name": "CaixaBank", "country_code": "ES"},
                    {"code": "bnp", "name": "BNP", "country_code": "FR"},
                ],
            },
        )

    with _patch_client(provider, handler):
        data = await provider.list_institutions()

    assert data.countries == ["ES", "FR"]
    assert [i.name for i in data.institutions] == ["bbva", "caixabank", "bnp"]
    assert data.institutions[0].display_name == "BBVA"
    assert data.institutions[0].logo == "https://l/bbva.png"


@pytest.mark.asyncio
async def test_get_oauth_url_requires_institution(wr_settings):
    provider = WealthreaderProvider()
    with pytest.raises(ValueError, match="institution_name"):
        await provider.get_oauth_url("https://app.example.com/oauth/callback", "state-1", {})


@pytest.mark.asyncio
async def test_get_oauth_url_embeds_pkce(wr_settings):
    provider = WealthreaderProvider()
    url = await provider.get_oauth_url(
        "https://app.example.com/oauth/callback",
        "securo-state",
        {"institution_name": "bbva"},
    )
    assert url.startswith("https://oauth.wealthreader.com/oauth2/?")
    assert "code_challenge_method=S256" in url
    assert "state=securo-state" in url
    assert "securo-state" in _pending_pkce
    stored = _pending_pkce["securo-state"]
    assert stored["entity_code"] == "bbva"
    assert stored["nonce"] in _pending_pkce


@pytest.mark.asyncio
async def test_handle_oauth_callback_exchanges_and_maps(wr_settings):
    provider = WealthreaderProvider()
    await provider.get_oauth_url(
        "https://app.example.com/oauth/callback",
        "securo-state",
        {"institution_name": "bbva"},
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/token/")
        assert b"code=the-code" in request.content
        assert b"code_verifier=" in request.content
        return httpx.Response(
            200,
            json={
                "success": True,
                "payload": {
                    "accounts": [
                        {
                            "uuid": "acc-1",
                            "subtype": "checking",
                            "code": "ES4914651234561234567890",
                            "name": "Cuenta NOMINA",
                            "currency": "EUR",
                            "balances": {"available": 14302.07, "current": 14302.07},
                            "transactions": [],
                        }
                    ]
                },
                "statistics": {"token": "FRJ0mHlaqZwLzu", "code": "bbva"},
            },
        )

    with _patch_client(provider, handler):
        conn = await provider.handle_oauth_callback("the-code", state="securo-state")

    assert conn.external_id == "bbva"
    assert conn.institution_name == "bbva"
    assert len(conn.accounts) == 1
    assert conn.accounts[0].external_id == "acc-1"
    assert conn.accounts[0].balance == Decimal("14302.07")
    assert conn.accounts[0].type == "checking"
    assert conn.accounts[0].masked_number == "7890"
    assert conn.credentials["code"] == "bbva"
    assert conn.credentials["token_enc"]


@pytest.mark.asyncio
async def test_get_transactions_maps_sign_and_payee(wr_settings):
    provider = WealthreaderProvider()
    fixture = {
        "success": True,
        "payload": {
            "accounts": [
                {
                    "uuid": "acc-1",
                    "currency": "EUR",
                    "transactions": [
                        {
                            "uuid": "tx-in",
                            "operation_date": "2024-02-01",
                            "amount": 1250.50,
                            "description": "Monthly salary",
                        },
                        {
                            "uuid": "tx-out",
                            "operation_date": "2024-02-02",
                            "amount": -27.45,
                            "description": "Example Grocer",
                            "transfer_details": {"concept": "Card purchase"},
                        },
                    ],
                }
            ]
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/entities/")
        return httpx.Response(200, json=fixture)

    with _patch_client(provider, handler), patch(
        "app.providers.wealthreader.decrypt", return_value="token"
    ):
        txs = await provider.get_transactions(
            {"token_enc": "enc", "code": "bbva"}, "acc-1", since=date(2024, 1, 1)
        )

    assert len(txs) == 2
    assert txs[0].type == "credit"
    assert txs[0].amount == Decimal("1250.50")
    assert txs[0].payee == "Monthly salary"
    assert txs[1].type == "debit"
    assert txs[1].amount == Decimal("27.45")
    assert txs[1].date == date(2024, 2, 2)


@pytest.mark.asyncio
async def test_auth_error_becomes_session_expired(wr_settings):
    provider = WealthreaderProvider()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"success": False, "error": {"code": 2020, "message": "token invalid"}},
        )

    with _patch_client(provider, handler), patch(
        "app.providers.wealthreader.decrypt", return_value="token"
    ):
        with pytest.raises(SessionExpiredError):
            await provider.get_accounts({"token_enc": "enc", "code": "bbva"})
