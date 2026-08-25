"""Unit tests for the read-only Interactive Brokers Flex provider."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.providers.base import ProviderRateLimited, ProviderUserActionRequired
from app.providers.ibkr import IbkrFlexProvider, _parse_xml


def _report_xml(*, second_account: bool = False, position_detail: str = "SUMMARY") -> str:
    second = (
        '<FlexStatement accountId="U222"><AccountInformation accountId="U222" '
        'currency="USD"/><CashReport><CashReportCurrency currency="USD" '
        'endingCash="0"/></CashReport><StmtFunds/><OpenPositions/></FlexStatement>'
        if second_account
        else ""
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<FlexQueryResponse>
  <FlexStatements count="{2 if second_account else 1}">
    <FlexStatement accountId="U1234567" accountAlias="Long Term" currency="USD">
      <AccountInformation accountId="U1234567" accountAlias="Long Term"
        name="Example Account" currency="USD" />
      <CashReport>
        <CashReportCurrency currency="BASE_SUMMARY" endingCash="1100" />
        <CashReportCurrency currency="USD" endingCash="1000.25" />
        <CashReportCurrency currency="EUR" endingCash="-50.50" />
      </CashReport>
      <StmtFunds>
        <StmtFund currency="USD" date="20260810" transactionID="TX-DIV"
          tradeID=""
          activityCode="DIV" activityDescription="AAPL dividend" amount="12.34"
          symbol="AAPL" balance="1000.25" />
        <StmtFund currency="USD" date="20260811" transactionID="TX-FEE"
          tradeID=""
          activityCode="FEE" activityDescription="Market data fee" debit="5.00"
          symbol="" balance="995.25" />
        <StmtFund currency="USD" date="20260812" transactionID="TX-TRADE"
          tradeID="TR-1" tradeQuantity="2" activityDescription="Buy AAPL"
          amount="-400" symbol="AAPL" balance="595.25" />
        <StmtFund currency="EUR" date="2026-08-09" transactionID="TX-INT"
          tradeID="" activityCode="INT" activityDescription="Credit interest"
          credit="1.25" symbol="" balance="-50.50" />
        <StmtFund currency="USD" date="" transactionID="" tradeID=""
          activityCode="StartingCash" amount="900" symbol="" balance="900" />
      </StmtFunds>
      <OpenPositions>
        <OpenPosition accountId="U1234567" model="Core" conid="265598"
          currency="USD" assetCategory="STK" symbol="AAPL"
          description="APPLE INC" position="3" multiplier="1" markPrice="220"
          positionValue="660" costBasisMoney="500" isin="US0378331005"
          side="Long" openDateTime="20250102;093000" levelOfDetail="{position_detail}" />
        <OpenPosition accountId="U1234567" conid="999" currency="USD"
          assetCategory="OPT" symbol="AAPL  260116P00200000"
          description="AAPL JAN 2026 200 Put" position="-1" multiplier="100"
          markPrice="3.5" positionValue="-350" costBasisMoney="-300"
          isin="" side="Short" openDateTime="" levelOfDetail="SUMMARY" />
      </OpenPositions>
    </FlexStatement>
    {second}
  </FlexStatements>
</FlexQueryResponse>"""


def _patched_client(handler):
    transport = httpx.MockTransport(handler)

    def fake_client(self):
        return httpx.AsyncClient(transport=transport, timeout=30)

    return patch.object(IbkrFlexProvider, "_client", fake_client)


def test_parse_report_requires_one_account():
    with pytest.raises(ProviderUserActionRequired, match="exactly one account"):
        _parse_xml(_report_xml(second_account=True))


def test_parse_report_requires_summary_positions():
    with pytest.raises(ProviderUserActionRequired, match="Summary"):
        _parse_xml(_report_xml(position_detail="LOT"))


def test_single_currency_base_summary_maps_to_account_base_currency():
    xml = (
        _report_xml()
        .replace(
            '        <CashReportCurrency currency="USD" endingCash="1000.25" />\n',
            "",
        )
        .replace(
            '        <CashReportCurrency currency="EUR" endingCash="-50.50" />\n',
            "",
        )
    )

    report = _parse_xml(xml)

    assert report.cash_balances == {"USD": Decimal("1100")}


@pytest.mark.asyncio
async def test_maps_currency_accounts_non_trade_activity_and_positions():
    provider = IbkrFlexProvider()
    provider._report = _parse_xml(_report_xml())

    accounts = await provider.get_accounts({})
    assert [(a.currency, a.balance) for a in accounts] == [
        ("EUR", Decimal("-50.50")),
        ("USD", Decimal("1000.25")),
    ]
    assert accounts[1].external_id == "U1234567:USD"
    assert accounts[1].masked_number == "4567"

    transactions = await provider.get_transactions({}, "U1234567:USD")
    assert [t.external_id for t in transactions] == [
        "ibkr:U1234567:USD:TX-DIV",
        "ibkr:U1234567:USD:TX-FEE",
    ]
    assert transactions[0].type == "credit"
    assert transactions[0].amount == Decimal("12.34")
    assert transactions[1].type == "debit"
    assert transactions[1].amount == Decimal("5.00")
    assert transactions[0].raw_data["transactionID"] == "TX-DIV"

    holdings = await provider.get_holdings({})
    assert len(holdings) == 2
    assert holdings[0].external_id == "U1234567:Core:265598"
    assert holdings[0].current_value == Decimal("660")
    assert holdings[0].quantity == Decimal("3")
    assert holdings[0].purchase_price == Decimal("500")
    assert holdings[0].purchase_date == date(2025, 1, 2)
    assert holdings[1].quantity == Decimal("-1")
    assert holdings[1].current_value == Decimal("-350")


@pytest.mark.asyncio
async def test_connect_generates_one_report_and_reuses_it(caplog):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        assert request.url.params["t"] == "secret-token"
        if request.url.path.endswith("/SendRequest"):
            assert request.url.params["v"] == "3"
            assert request.url.params["q"] == "123456"
            from_date = date.fromisoformat(
                f"{request.url.params['fd'][:4]}-{request.url.params['fd'][4:6]}-"
                f"{request.url.params['fd'][6:]}"
            )
            to_date = date.fromisoformat(
                f"{request.url.params['td'][:4]}-{request.url.params['td'][4:6]}-"
                f"{request.url.params['td'][6:]}"
            )
            assert (to_date - from_date).days == 364
            assert to_date == date.today() - timedelta(days=1)
            return httpx.Response(
                200,
                text=(
                    "<FlexStatementResponse><Status>Success</Status>"
                    "<ReferenceCode>REF-1</ReferenceCode>"
                    "<Url>https://ndcdyn.interactivebrokers.com/"
                    "AccountManagement/FlexWebService/GetStatement</Url>"
                    "</FlexStatementResponse>"
                ),
            )
        return httpx.Response(200, text=_report_xml())

    settings = SimpleNamespace(
        ibkr_flex_api_url=("https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService")
    )
    provider = IbkrFlexProvider()
    caplog.set_level(logging.INFO, logger="httpx")
    with (
        patch("app.providers.ibkr.get_settings", return_value=settings),
        patch("app.providers.ibkr.asyncio.sleep", new=AsyncMock()),
        _patched_client(handler),
    ):
        connection = await provider.connect_with_token("secret-token", {"query_id": "123456"})
        await provider.get_accounts(connection.credentials)
        await provider.get_transactions(connection.credentials, "U1234567:USD")
        await provider.get_holdings(connection.credentials)

    assert calls == [
        "/AccountManagement/FlexWebService/SendRequest",
        "/AccountManagement/FlexWebService/GetStatement",
    ]
    assert connection.external_id == "ibkr:U1234567"
    assert connection.institution_name == "Interactive Brokers - Long Term"
    assert connection.credentials["query_id"] == "123456"
    assert "secret-token" not in str(connection.credentials)
    assert "secret-token" not in caplog.text
    assert "%5BREDACTED%5D" in caplog.text


@pytest.mark.asyncio
async def test_generation_can_continue_beyond_the_old_eight_poll_window():
    statement_polls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal statement_polls
        if request.url.path.endswith("/SendRequest"):
            return httpx.Response(
                200,
                text=(
                    "<FlexStatementResponse><Status>Success</Status>"
                    "<ReferenceCode>REF-SLOW</ReferenceCode>"
                    "<Url>https://ndcdyn.interactivebrokers.com/"
                    "AccountManagement/FlexWebService/GetStatement</Url>"
                    "</FlexStatementResponse>"
                ),
            )
        statement_polls += 1
        if statement_polls <= 10:
            return httpx.Response(
                200,
                text=(
                    "<FlexStatementResponse><Status>Fail</Status>"
                    "<ErrorCode>1019</ErrorCode>"
                    "<ErrorMessage>Statement generation in progress</ErrorMessage>"
                    "</FlexStatementResponse>"
                ),
            )
        return httpx.Response(200, text=_report_xml())

    settings = SimpleNamespace(
        ibkr_flex_api_url=("https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService")
    )
    sleep = AsyncMock()
    with (
        patch("app.providers.ibkr.get_settings", return_value=settings),
        patch("app.providers.ibkr.asyncio.sleep", new=sleep),
        _patched_client(handler),
    ):
        connection = await IbkrFlexProvider().connect_with_token(
            "secret-token", {"query_id": "123456"}
        )

    assert connection.external_id == "ibkr:U1234567"
    assert statement_polls == 11
    assert sleep.await_count == 11
    sleep.assert_awaited_with(10)


@pytest.mark.asyncio
async def test_statement_retrieval_retries_temporary_http_failure():
    statement_polls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal statement_polls
        if request.url.path.endswith("/SendRequest"):
            return httpx.Response(
                200,
                text=(
                    "<FlexStatementResponse><Status>Success</Status>"
                    "<ReferenceCode>REF-RETRY</ReferenceCode>"
                    "<Url>https://ndcdyn.interactivebrokers.com/"
                    "AccountManagement/FlexWebService/GetStatement</Url>"
                    "</FlexStatementResponse>"
                ),
            )
        statement_polls += 1
        if statement_polls == 1:
            return httpx.Response(503, text="temporarily unavailable")
        return httpx.Response(200, text=_report_xml())

    settings = SimpleNamespace(
        ibkr_flex_api_url=("https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService")
    )
    with (
        patch("app.providers.ibkr.get_settings", return_value=settings),
        patch("app.providers.ibkr.asyncio.sleep", new=AsyncMock()),
        _patched_client(handler),
    ):
        connection = await IbkrFlexProvider().connect_with_token(
            "secret-token", {"query_id": "123456"}
        )

    assert connection.external_id == "ibkr:U1234567"
    assert statement_polls == 2


@pytest.mark.asyncio
async def test_statement_retrieval_uses_validated_returned_host():
    requested_hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/SendRequest"):
            return httpx.Response(
                200,
                text=(
                    "<FlexStatementResponse><Status>Success</Status>"
                    "<ReferenceCode>REF-DNS</ReferenceCode>"
                    "<Url>https://gdcdyn.interactivebrokers.com/"
                    "AccountManagement/FlexWebService/GetStatement</Url>"
                    "</FlexStatementResponse>"
                ),
            )
        requested_hosts.append(request.url.host)
        return httpx.Response(200, text=_report_xml())

    settings = SimpleNamespace(
        ibkr_flex_api_url=("https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService")
    )
    with (
        patch("app.providers.ibkr.get_settings", return_value=settings),
        patch("app.providers.ibkr.asyncio.sleep", new=AsyncMock()),
        _patched_client(handler),
    ):
        connection = await IbkrFlexProvider().connect_with_token(
            "secret-token", {"query_id": "123456"}
        )

    assert connection.external_id == "ibkr:U1234567"
    assert requested_hosts == ["gdcdyn.interactivebrokers.com"]


@pytest.mark.asyncio
async def test_text_report_requires_xml_query_output():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/SendRequest"):
            return httpx.Response(
                200,
                text=(
                    "<FlexStatementResponse><Status>Success</Status>"
                    "<ReferenceCode>REF-TEXT</ReferenceCode>"
                    "<Url>https://gdcdyn.interactivebrokers.com/legacy</Url>"
                    "</FlexStatementResponse>"
                ),
            )
        return httpx.Response(
            200,
            text="ClientAccountID,Currency,EndingCash\nU1234567,USD,100.00",
        )

    settings = SimpleNamespace(
        ibkr_flex_api_url=("https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService")
    )
    with (
        patch("app.providers.ibkr.get_settings", return_value=settings),
        patch("app.providers.ibkr.asyncio.sleep", new=AsyncMock()),
        _patched_client(handler),
        pytest.raises(ProviderUserActionRequired) as exc,
    ):
        await IbkrFlexProvider().connect_with_token("secret-token", {"query_id": "123456"})

    assert exc.value.code == "flex_query_output_format"
    assert "must be XML" in str(exc.value)


@pytest.mark.asyncio
async def test_rate_limit_is_typed_and_token_is_not_in_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=(
                "<FlexStatementResponse><Status>Fail</Status>"
                "<ErrorCode>1018</ErrorCode>"
                "<ErrorMessage>Too many requests for do-not-leak</ErrorMessage>"
                "</FlexStatementResponse>"
            ),
        )

    settings = SimpleNamespace(
        ibkr_flex_api_url=("https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService")
    )
    provider = IbkrFlexProvider()
    with (
        patch("app.providers.ibkr.get_settings", return_value=settings),
        _patched_client(handler),
        pytest.raises(ProviderRateLimited, match="rate limit reached") as exc,
    ):
        await provider.connect_with_token("do-not-leak", {"query_id": "123"})
    assert "do-not-leak" not in str(exc.value)


@pytest.mark.asyncio
async def test_expired_token_requires_user_action():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=(
                "<FlexStatementResponse><Status>Fail</Status>"
                "<ErrorCode>1012</ErrorCode><ErrorMessage>Token expired</ErrorMessage>"
                "</FlexStatementResponse>"
            ),
        )

    settings = SimpleNamespace(
        ibkr_flex_api_url=("https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService")
    )
    with (
        patch("app.providers.ibkr.get_settings", return_value=settings),
        _patched_client(handler),
        pytest.raises(ProviderUserActionRequired) as exc,
    ):
        await IbkrFlexProvider().connect_with_token("expired", {"query_id": "123"})
    assert exc.value.code == "credentials_expired"


def test_rejects_untrusted_statement_url():
    provider = IbkrFlexProvider()
    with pytest.raises(ValueError, match="untrusted"):
        provider._validate_statement_url("https://attacker.example/GetStatement")


def test_rejects_statement_url_with_embedded_credentials_or_query():
    provider = IbkrFlexProvider()
    with pytest.raises(ValueError, match="untrusted"):
        provider._validate_statement_url(
            "https://user:password@ndcdyn.interactivebrokers.com/GetStatement"
        )
    with pytest.raises(ValueError, match="untrusted"):
        provider._validate_statement_url(
            "https://ndcdyn.interactivebrokers.com/GetStatement?token=secret"
        )


def test_query_validation_reports_the_specific_missing_field():
    xml = _report_xml().replace(' tradeID=""', "")

    with pytest.raises(ProviderUserActionRequired) as exc:
        _parse_xml(xml)

    assert exc.value.code == "flex_query_missing_field"
    assert "Trade ID" in str(exc.value)


def test_rejects_duplicate_summary_positions():
    duplicate = """
        <OpenPosition accountId="U1234567" model="Core" conid="265598"
          currency="USD" assetCategory="STK" symbol="AAPL"
          description="APPLE INC" position="3" multiplier="1" markPrice="220"
          positionValue="660" costBasisMoney="500" isin="US0378331005"
          side="Long" openDateTime="20250102 093000" levelOfDetail="SUMMARY" />
    """
    xml = _report_xml().replace("      </OpenPositions>", duplicate + "      </OpenPositions>")

    with pytest.raises(ProviderUserActionRequired, match="duplicate Summary"):
        _parse_xml(xml)


def test_rejects_non_finite_position_numbers():
    xml = _report_xml().replace('positionValue="660"', 'positionValue="NaN"', 1)

    with pytest.raises(ValueError, match="finite number"):
        _parse_xml(xml)


def test_rejects_malformed_statement_and_position_dates_during_parse():
    malformed_funds = _report_xml().replace('date="20260810"', 'date="not-a-date"', 1)
    with pytest.raises(ValueError, match="Statement of Funds / Date"):
        _parse_xml(malformed_funds)

    malformed_position = _report_xml().replace(
        'openDateTime="20250102;093000"',
        'openDateTime="not-a-date"',
        1,
    )
    with pytest.raises(ValueError, match="Open Positions / Open Date"):
        _parse_xml(malformed_position)


def test_plaintext_flex_token_is_never_accepted_from_stored_credentials():
    assert IbkrFlexProvider._token({"flex_token": "plaintext-secret"}) == ""


@pytest.mark.asyncio
async def test_statement_poll_retries_ibkr_throttle_response():
    statement_polls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal statement_polls
        if request.url.path.endswith("/SendRequest"):
            return httpx.Response(
                200,
                text=(
                    "<FlexStatementResponse><Status>Success</Status>"
                    "<ReferenceCode>REF-THROTTLED</ReferenceCode>"
                    "</FlexStatementResponse>"
                ),
            )
        statement_polls += 1
        if statement_polls == 1:
            return httpx.Response(
                200,
                text=(
                    "<FlexStatementResponse><Status>Fail</Status>"
                    "<ErrorCode>1018</ErrorCode>"
                    "<ErrorMessage>Too many requests</ErrorMessage>"
                    "</FlexStatementResponse>"
                ),
            )
        return httpx.Response(200, text=_report_xml())

    settings = SimpleNamespace(
        ibkr_flex_api_url=("https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService")
    )
    with (
        patch("app.providers.ibkr.get_settings", return_value=settings),
        patch("app.providers.ibkr.asyncio.sleep", new=AsyncMock()),
        _patched_client(handler),
    ):
        connection = await IbkrFlexProvider().connect_with_token(
            "secret-token", {"query_id": "123456"}
        )

    assert connection.external_id == "ibkr:U1234567"
    assert statement_polls == 2


@pytest.mark.asyncio
async def test_send_request_http_429_is_typed_rate_limit():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="rate limited")

    settings = SimpleNamespace(
        ibkr_flex_api_url=("https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService")
    )
    with (
        patch("app.providers.ibkr.get_settings", return_value=settings),
        _patched_client(handler),
        pytest.raises(ProviderRateLimited, match="rate limited"),
    ):
        await IbkrFlexProvider().connect_with_token("secret-token", {"query_id": "123456"})
