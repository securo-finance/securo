"""Parser tests for the Pluggy /bills endpoint introduced for issue #92.

Mirrors the test_providers_pluggy.py pattern: httpx is stubbed out so no
network traffic happens; we exercise PluggyProvider.get_bills end-to-end
against the JSON shapes Pluggy may emit. The goal is to pin down every
"what does the parser do when X is malformed/missing" decision so we can
fearlessly turn /bills sync on for real users.
"""

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.providers.pluggy import PluggyProvider


def _mock_httpx_client(results: list[dict]) -> MagicMock:
    """Single-page client mock — same shape as test_providers_pluggy.py."""
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value={"results": results, "totalPages": 1})

    client = MagicMock()
    client.get = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return client


def _mock_httpx_client_paged(pages: list[list[dict]]) -> MagicMock:
    """Multi-page client mock — each .get() returns the next page."""
    total = len(pages)
    responses = []
    for page_results in pages:
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={"results": page_results, "totalPages": total})
        responses.append(resp)

    client = MagicMock()
    client.get = AsyncMock(side_effect=responses)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return client


async def _fetch(bills: list[dict]):
    provider = PluggyProvider()
    fake_client = _mock_httpx_client(bills)
    with patch.object(
        PluggyProvider, "_ensure_api_key", new=AsyncMock(return_value="fake-key")
    ), patch("app.providers.pluggy.httpx.AsyncClient", return_value=fake_client):
        return await provider.get_bills({"item_id": "i"}, "acc-ext-1")


async def _fetch_paged(pages: list[list[dict]]):
    provider = PluggyProvider()
    fake_client = _mock_httpx_client_paged(pages)
    with patch.object(
        PluggyProvider, "_ensure_api_key", new=AsyncMock(return_value="fake-key")
    ), patch("app.providers.pluggy.httpx.AsyncClient", return_value=fake_client):
        return await provider.get_bills({"item_id": "i"}, "acc-ext-1")


# ---- Happy path ------------------------------------------------------------


@pytest.mark.asyncio
async def test_bills_parser_full_payload():
    """Universal credit-card billing fields flow through into BillData."""
    raw = {
        "id": "bill-1",
        "dueDate": "2026-04-15",
        "totalAmount": 2431.99,
        "totalAmountCurrencyCode": "BRL",
        "minimumPaymentAmount": 250.00,
    }
    result = await _fetch([raw])
    assert len(result) == 1
    bill = result[0]
    assert bill.external_id == "bill-1"
    assert bill.due_date == date(2026, 4, 15)
    assert bill.total_amount == Decimal("2431.99")
    assert bill.currency == "BRL"
    assert bill.minimum_payment == Decimal("250.00")
    assert bill.raw_data == raw


@pytest.mark.asyncio
async def test_bills_parser_minimal_payload_uses_defaults():
    """Optional fields default to None; currency falls back to BRL."""
    result = await _fetch([
        {"id": "bill-2", "dueDate": "2026-03-15", "totalAmount": 100}
    ])
    bill = result[0]
    assert bill.currency == "BRL"
    assert bill.minimum_payment is None
    assert bill.total_amount == Decimal("100")


@pytest.mark.asyncio
async def test_bills_parser_preserves_provider_extras_in_raw_data():
    """Provider-specific fields we don't promote to columns survive verbatim
    in raw_data — the contract for "we don't lose data when adding a new
    integration." Today these are Pluggy fields; tomorrow it could be Belvo
    or a custom script, and downstream code can opt into them via raw_data."""
    raw = {
        "id": "bill-extras",
        "dueDate": "2026-04-15",
        "totalAmount": 100,
        "financeCharges": [{"type": "IOF", "amount": 12.34}],
        "payments": [{"id": "p-1", "amount": 100, "paymentDate": "2026-04-15"}],
        "allowsInstallments": True,
        "someFutureField": {"nested": "value"},
    }
    bill = (await _fetch([raw]))[0]
    assert bill.raw_data == raw
    # And we explicitly do NOT promote them to BillData fields.
    assert not hasattr(bill, "finance_charges")
    assert not hasattr(bill, "payments")
    assert not hasattr(bill, "allows_installments")


# ---- Required-field handling: skip rather than crash ----------------------


@pytest.mark.asyncio
async def test_bills_parser_skips_missing_id():
    result = await _fetch([
        {"dueDate": "2026-03-15", "totalAmount": 50},
        {"id": "bill-ok", "dueDate": "2026-04-15", "totalAmount": 50},
    ])
    assert [b.external_id for b in result] == ["bill-ok"]


@pytest.mark.asyncio
async def test_bills_parser_skips_empty_string_id():
    """Falsy id must be skipped, not coerced to "" — that would collide on the
    unique(account_id, external_id) constraint downstream."""
    result = await _fetch([
        {"id": "", "dueDate": "2026-04-15", "totalAmount": 10},
        {"id": "bill-ok", "dueDate": "2026-04-15", "totalAmount": 50},
    ])
    assert [b.external_id for b in result] == ["bill-ok"]


@pytest.mark.asyncio
async def test_bills_parser_skips_missing_due_date():
    result = await _fetch([
        {"id": "bill-x", "totalAmount": 10},
        {"id": "bill-ok", "dueDate": "2026-04-15", "totalAmount": 50},
    ])
    assert [b.external_id for b in result] == ["bill-ok"]


@pytest.mark.asyncio
async def test_bills_parser_skips_malformed_due_date():
    result = await _fetch([
        {"id": "bill-x", "dueDate": "not-a-date", "totalAmount": 10},
        {"id": "bill-ok", "dueDate": "2026-04-15", "totalAmount": 50},
    ])
    assert [b.external_id for b in result] == ["bill-ok"]


@pytest.mark.asyncio
async def test_bills_parser_skips_missing_total_amount():
    result = await _fetch([
        {"id": "bill-x", "dueDate": "2026-04-15"},
        {"id": "bill-ok", "dueDate": "2026-04-15", "totalAmount": 50},
    ])
    assert [b.external_id for b in result] == ["bill-ok"]


@pytest.mark.asyncio
async def test_bills_parser_skips_malformed_total_amount():
    result = await _fetch([
        {"id": "bill-x", "dueDate": "2026-04-15", "totalAmount": "not-a-number"},
        {"id": "bill-ok", "dueDate": "2026-04-15", "totalAmount": 50},
    ])
    assert [b.external_id for b in result] == ["bill-ok"]


# ---- Edge formats Pluggy may send -----------------------------------------


@pytest.mark.asyncio
async def test_bills_parser_due_date_with_time_suffix():
    """ISO datetime strings get truncated to date cleanly."""
    result = await _fetch([
        {"id": "bill-3", "dueDate": "2026-05-10T03:00:00.000Z", "totalAmount": 200}
    ])
    assert result[0].due_date == date(2026, 5, 10)


@pytest.mark.asyncio
async def test_bills_parser_total_amount_as_string():
    result = await _fetch([
        {"id": "b", "dueDate": "2026-04-15", "totalAmount": "1234.56"}
    ])
    assert result[0].total_amount == Decimal("1234.56")


@pytest.mark.asyncio
async def test_bills_parser_total_amount_as_int():
    result = await _fetch([
        {"id": "b", "dueDate": "2026-04-15", "totalAmount": 100}
    ])
    assert result[0].total_amount == Decimal("100")


@pytest.mark.asyncio
async def test_bills_parser_negative_total_preserves_sign():
    """Pluggy reports credit-balance bills as negative — keep as-is, not abs'd.
    A negative bill means the bank owes the user money; flipping the sign
    would silently turn a credit into a debt in any aggregation."""
    result = await _fetch([
        {"id": "b", "dueDate": "2026-04-15", "totalAmount": -12.50}
    ])
    assert result[0].total_amount == Decimal("-12.50")


@pytest.mark.asyncio
async def test_bills_parser_zero_total_is_kept():
    """A R$0,00 fatura is still a valid statement (e.g. a closed cycle with
    no spend) — must not be confused with "missing totalAmount"."""
    result = await _fetch([
        {"id": "b", "dueDate": "2026-04-15", "totalAmount": 0}
    ])
    assert len(result) == 1
    assert result[0].total_amount == Decimal("0")


@pytest.mark.asyncio
async def test_bills_parser_currency_defaults_to_brl():
    result = await _fetch([
        {"id": "b", "dueDate": "2026-04-15", "totalAmount": 10}
    ])
    assert result[0].currency == "BRL"


@pytest.mark.asyncio
async def test_bills_parser_currency_explicit_overrides_default():
    result = await _fetch([
        {
            "id": "b",
            "dueDate": "2026-04-15",
            "totalAmount": 10,
            "totalAmountCurrencyCode": "USD",
        }
    ])
    assert result[0].currency == "USD"


@pytest.mark.asyncio
async def test_bills_parser_id_coerced_to_string():
    """Pluggy normally returns string IDs but we coerce defensively — the
    column is String(255) and a numeric id would otherwise blow up the
    upsert with a type error."""
    result = await _fetch([
        {"id": 12345, "dueDate": "2026-04-15", "totalAmount": 10}
    ])
    assert result[0].external_id == "12345"
    assert isinstance(result[0].external_id, str)


@pytest.mark.asyncio
async def test_bills_parser_minimum_payment_string():
    result = await _fetch([
        {
            "id": "b",
            "dueDate": "2026-04-15",
            "totalAmount": 10,
            "minimumPaymentAmount": "12.50",
        }
    ])
    assert result[0].minimum_payment == Decimal("12.50")


@pytest.mark.asyncio
async def test_bills_parser_minimum_payment_garbage_drops_to_none():
    result = await _fetch([
        {
            "id": "b",
            "dueDate": "2026-04-15",
            "totalAmount": 10,
            "minimumPaymentAmount": "n/a",
        }
    ])
    assert result[0].minimum_payment is None


# ---- Pagination & empty results -------------------------------------------


@pytest.mark.asyncio
async def test_bills_parser_empty_results():
    result = await _fetch([])
    assert result == []


@pytest.mark.asyncio
async def test_bills_parser_paginates_across_multiple_pages():
    page1 = [
        {"id": "b-1", "dueDate": "2026-01-15", "totalAmount": 100},
        {"id": "b-2", "dueDate": "2026-02-15", "totalAmount": 200},
    ]
    page2 = [
        {"id": "b-3", "dueDate": "2026-03-15", "totalAmount": 300},
    ]
    result = await _fetch_paged([page1, page2])
    assert [b.external_id for b in result] == ["b-1", "b-2", "b-3"]


@pytest.mark.asyncio
async def test_bills_parser_mixed_valid_and_invalid_in_same_page():
    """Bad rows in the middle of a page don't poison the good ones."""
    result = await _fetch([
        {"id": "ok-1", "dueDate": "2026-01-15", "totalAmount": 100},
        {"id": "bad", "dueDate": "garbage", "totalAmount": 50},
        {"id": "ok-2", "dueDate": "2026-02-15", "totalAmount": 200},
    ])
    assert [b.external_id for b in result] == ["ok-1", "ok-2"]


# ---- Default abstract method on BankProvider ------------------------------


@pytest.mark.asyncio
async def test_default_get_bills_returns_empty_list():
    """Providers that don't override get_bills get an empty list — the sync
    layer reads "no bills" as "fall back to local cycle math"."""
    from app.providers.base import BankProvider

    class StubProvider(BankProvider):
        @property
        def name(self) -> str:
            return "stub"

        async def get_oauth_url(self, redirect_uri: str, state: str, flow_params: dict | None = None) -> str:
            return ""

        async def handle_oauth_callback(self, code):
            raise NotImplementedError

        async def get_accounts(self, credentials):
            return []

        async def get_transactions(self, credentials, account_external_id, since=None, payee_source="auto"):
            return []

        async def refresh_credentials(self, credentials):
            return credentials

    assert await StubProvider().get_bills({}, "acc") == []
