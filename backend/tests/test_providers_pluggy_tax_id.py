"""Parser tests for `PluggyProvider._extract_tax_id`.

The counterparty's document is the only part of a Pix payload stable enough
to survive somebody correcting the payee's name, so these pin down that it
is read from the right side of the payment, that it is read independently
of whatever the connection's `payee_source` says, and that an unfamiliar
document type is ignored rather than stored as a kind nothing validates.

The httpx client is stubbed out, so no network traffic happens.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.providers.pluggy import PluggyProvider

CPF = "529.982.247-25"
CNPJ = "11.222.333/0001-81"


def _mock_httpx_client(results: list[dict]) -> MagicMock:
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value={"results": results, "totalPages": 1})

    client = MagicMock()
    client.get = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return client


async def _fetch(txns: list[dict], payee_source: str = "auto"):
    provider = PluggyProvider()
    fake_client = _mock_httpx_client(txns)
    with patch.object(
        PluggyProvider, "_ensure_api_key", new=AsyncMock(return_value="fake-key")
    ), patch("app.providers.pluggy.httpx.AsyncClient", return_value=fake_client):
        return await provider.get_transactions(
            {"item_id": "i"}, "acc-ext-1", payee_source=payee_source
        )


def _pix(txn_type: str, *, receiver: dict | None = None, payer: dict | None = None) -> dict:
    return {
        "id": "t1",
        "description": "PIX",
        "amount": -10 if txn_type == "DEBIT" else 10,
        "date": "2026-07-09T03:00:00.000Z",
        "type": txn_type,
        "paymentData": {"receiver": receiver, "payer": payer},
    }


def _doc(kind: str, value: str) -> dict:
    return {"name": None, "documentNumber": {"type": kind, "value": value}}


@pytest.mark.asyncio
async def test_debit_reads_the_receiver():
    """On a debit the money went to the receiver, so that is the counterparty."""
    result = await _fetch([
        _pix("DEBIT", receiver=_doc("CPF", CPF), payer=_doc("CNPJ", CNPJ)),
    ])
    assert result[0].payee_tax_id_kind == "cpf"
    assert result[0].payee_tax_id_value == CPF


@pytest.mark.asyncio
async def test_credit_reads_the_payer():
    result = await _fetch([
        _pix("CREDIT", receiver=_doc("CPF", CPF), payer=_doc("CNPJ", CNPJ)),
    ])
    assert result[0].payee_tax_id_kind == "cnpj"
    assert result[0].payee_tax_id_value == CNPJ


@pytest.mark.asyncio
async def test_document_is_kept_even_when_the_name_came_through():
    """A named counterparty still benefits from carrying its document.

    `_extract_payee` stops at the name, so extracting the document only as a
    fallback would leave every correctly-named company without one.
    """
    receiver = {"name": "ACME LTDA", "documentNumber": {"type": "CNPJ", "value": CNPJ}}
    result = await _fetch([_pix("DEBIT", receiver=receiver)])
    assert result[0].payee == "ACME LTDA"
    assert result[0].payee_tax_id_value == CNPJ


@pytest.mark.asyncio
@pytest.mark.parametrize("payee_source", ["auto", "none", "description", "merchant", "payment_data"])
async def test_extraction_ignores_payee_source(payee_source):
    """`payee_source` chooses what to call a counterparty, not how to identify it."""
    result = await _fetch([_pix("DEBIT", receiver=_doc("CPF", CPF))], payee_source=payee_source)
    assert result[0].payee_tax_id_value == CPF


@pytest.mark.asyncio
async def test_card_purchase_has_no_document():
    """Card transactions carry no paymentData at all, and must not invent one."""
    result = await _fetch([
        {
            "id": "t1",
            "description": "COFFEE BAR",
            "amount": -7,
            "date": "2026-04-28T03:00:00.000Z",
            "type": "DEBIT",
            "paymentData": None,
        }
    ])
    assert result[0].payee_tax_id_kind is None
    assert result[0].payee_tax_id_value is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "receiver",
    [
        None,
        {"name": "ACME", "documentNumber": None},
        {"name": "ACME", "documentNumber": {"type": "PASSPORT", "value": "X1234567"}},
        {"name": "ACME", "documentNumber": {"type": "CPF", "value": None}},
    ],
    ids=["no-party", "no-document", "unknown-type", "no-value"],
)
async def test_nothing_usable_yields_nothing(receiver):
    result = await _fetch([_pix("DEBIT", receiver=receiver)])
    assert result[0].payee_tax_id_kind is None
    assert result[0].payee_tax_id_value is None
