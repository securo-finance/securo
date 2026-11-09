from datetime import date
from xml.sax.saxutils import escape
import xml.etree.ElementTree as ET

import httpx
import pytest

from app.providers.benchmark import (
    BenchmarkProviderError,
    DailyRateHistoryLoader,
    DailyRateSource,
    SGSSoapRateSource,
)


def source():
    return SGSSoapRateSource(
        name="Test SOAP",
        url="https://soap.example/history",
        series=999,
        date_field="DATA",
        rate_field="VALOR",
        date_format="%d/%m/%Y",
    )


def envelope(payload):
    return (
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
        "<s:Body><getValoresSeriesXMLResponse><getValoresSeriesXMLReturn>"
        + escape(payload)
        + "</getValoresSeriesXMLReturn></getValoresSeriesXMLResponse>"
        "</s:Body></s:Envelope>"
    )


def fault(message):
    return (
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
        "<s:Body><s:Fault><faultstring>"
        + escape(message)
        + "</faultstring></s:Fault></s:Body></s:Envelope>"
    )


async def test_soap_filters_missing_blocked_and_out_of_range_observations():
    def respond(request):
        assert request.method == "POST"
        body = ET.fromstring(request.content)
        assert body.findtext(".//item") == "999"
        assert body.findtext(".//in1") == "02/01/2026"
        assert body.findtext(".//in2") == "06/01/2026"
        assert request.headers["SOAPAction"] == '""'
        return httpx.Response(
            200,
            text=envelope("""<SERIES><SERIE ID="999">
          <ITEM><DATA>1/1/2026</DATA><VALOR>5</VALOR></ITEM>
          <ITEM><DATA>3/1/2026</DATA><VALOR>0,2</VALOR></ITEM>
          <ITEM><DATA>2/1/2026</DATA><VALOR>0.1</VALOR></ITEM>
          <ITEM><DATA>4/1/2026</DATA><VALOR /></ITEM>
          <ITEM><DATA>5/1/2026</DATA><VALOR>10</VALOR><BLOQUEADO>true</BLOQUEADO></ITEM>
        </SERIE></SERIES>"""),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        points = await DailyRateHistoryLoader((source(),), client=client).history(
            date(2026, 1, 2), date(2026, 1, 6)
        )
    assert [p.date for p in points] == [date(2026, 1, 2), date(2026, 1, 3)]
    assert [p.value for p in points] == pytest.approx([100.1, 100.3002])


@pytest.mark.parametrize("case", ["no_data", "fault", "xml", "series", "rate", "timeout", "429"])
async def test_soap_errors_and_empty_ranges_allow_fallback(case):
    calls = []

    def respond(request):
        calls.append(request.url.host)
        if request.url.host == "json.example":
            return httpx.Response(200, json=[{"date": "2026-01-02", "rate": "0.1"}])
        if case == "timeout":
            raise httpx.ReadTimeout("timeout", request=request)
        if case == "429":
            return httpx.Response(429)
        if case in ("no_data", "fault"):
            message = (
                "br.gov.bcb.pec.sgs.comum.excecoes.SGSNegocioException: Value(s) not found"
                if case == "no_data"
                else "Internal failure"
            )
            return httpx.Response(500, text=fault(message))
        if case == "xml":
            return httpx.Response(200, text="<invalid")
        if case == "series":
            return httpx.Response(200, text=envelope('<SERIES><SERIE ID="123" /></SERIES>'))
        return httpx.Response(
            200,
            text=envelope(
                '<SERIES><SERIE ID="999"><ITEM>'
                "<DATA>2/1/2026</DATA><VALOR>NaN</VALOR></ITEM></SERIE></SERIES>"
            ),
        )

    backup = DailyRateSource(
        name="Backup", url="https://json.example", date_field="date", rate_field="rate"
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        points = await DailyRateHistoryLoader((source(), backup), client=client).history(
            date(2026, 1, 2), date(2026, 1, 3)
        )
    assert points[0].value == pytest.approx(100.1)
    assert calls == ["soap.example", "json.example"]


async def test_single_observation_falls_through_to_a_fuller_source():
    def respond(request):
        if request.url.host == "json.example":
            return httpx.Response(
                200,
                json=[{"date": "2026-01-02", "rate": "0.1"}, {"date": "2026-01-03", "rate": "0.2"}],
            )
        return httpx.Response(
            200,
            text=envelope(
                '<SERIES><SERIE ID="999"><ITEM>'
                "<DATA>2/1/2026</DATA><VALOR>0.1</VALOR></ITEM></SERIE></SERIES>"
            ),
        )

    backup = DailyRateSource(
        name="Backup", url="https://json.example", date_field="date", rate_field="rate"
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        points = await DailyRateHistoryLoader((source(), backup), client=client).history(
            date(2026, 1, 2), date(2026, 1, 3)
        )
    assert [p.value for p in points] == pytest.approx([100.1, 100.3002])


@pytest.mark.parametrize("missing", [True, False])
async def test_soap_distinguishes_no_data_from_service_failure(missing):
    message = (
        "br.gov.bcb.pec.sgs.comum.excecoes.SGSNegocioException: Value(s) not found"
        if missing
        else "Service unavailable"
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(500, text=fault(message)))
    ) as client:
        loader = DailyRateHistoryLoader((source(),), client=client)
        if missing:
            assert await loader.history(date(2026, 1, 2), date(2026, 1, 3)) == []
        else:
            with pytest.raises(BenchmarkProviderError):
                await loader.history(date(2026, 1, 2), date(2026, 1, 3))
