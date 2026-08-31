import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_list_currencies(client: AsyncClient):
    response = await client.get("/api/currencies")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) > 0
    for currency in data:
        assert "code" in currency
        assert "symbol" in currency
        assert "name" in currency
        assert "flag" in currency


@pytest.mark.asyncio
async def test_currencies_include_brl_and_usd(client: AsyncClient):
    response = await client.get("/api/currencies")
    codes = [c["code"] for c in response.json()]
    assert "BRL" in codes
    assert "USD" in codes


@pytest.mark.asyncio
async def test_currencies_include_clp_with_metadata(client: AsyncClient):
    response = await client.get("/api/currencies")
    data = response.json()
    clp = next((currency for currency in data if currency["code"] == "CLP"), None)

    assert clp is not None
    assert clp["symbol"] == "$"
    assert clp["name"] == "Peso Chileno"
    assert clp["flag"] == "🇨🇱"


@pytest.mark.asyncio
async def test_currencies_include_dop_with_metadata(client: AsyncClient):
    response = await client.get("/api/currencies")
    data = response.json()
    dop = next((currency for currency in data if currency["code"] == "DOP"), None)

    assert dop is not None
    assert dop["symbol"] == "RD$"
    assert dop["name"] == "Peso Dominicano"
    assert dop["flag"] == "🇩🇴"


@pytest.mark.asyncio
async def test_currencies_include_uah_with_metadata(client: AsyncClient):
    response = await client.get("/api/currencies")
    data = response.json()
    uah = next((currency for currency in data if currency["code"] == "UAH"), None)

    assert uah is not None
    assert uah["symbol"] == "₴"
    assert uah["name"] == "Ukrainian Hryvnia"
    assert uah["flag"] == "🇺🇦"


@pytest.mark.asyncio
async def test_currencies_include_nzd_with_metadata(client: AsyncClient):
    response = await client.get("/api/currencies")
    data = response.json()
    nzd = next((currency for currency in data if currency["code"] == "NZD"), None)

    assert nzd is not None
    assert nzd["symbol"] == "NZ$"
    assert nzd["name"] == "New Zealand Dollar"
    assert nzd["flag"] == "🇳🇿"


@pytest.mark.asyncio
async def test_currencies_include_vnd_with_metadata(client: AsyncClient):
    response = await client.get("/api/currencies")
    data = response.json()
    vnd = next((currency for currency in data if currency["code"] == "VND"), None)

    assert vnd is not None
    assert vnd["symbol"] == "₫"
    assert vnd["name"] == "Vietnamese Dong"
    assert vnd["flag"] == "🇻🇳"


@pytest.mark.asyncio
async def test_currencies_include_sgd_with_metadata(client: AsyncClient):
    response = await client.get("/api/currencies")
    data = response.json()
    sgd = next((currency for currency in data if currency["code"] == "SGD"), None)

    assert sgd is not None
    assert sgd["symbol"] == "S$"
    assert sgd["name"] == "Singapore Dollar"
    assert sgd["flag"] == "🇸🇬"


@pytest.mark.asyncio
async def test_currencies_include_azn_with_metadata(client: AsyncClient):
    response = await client.get("/api/currencies")
    data = response.json()
    
    azn = next((currency for currency in data if currency["code"] == "AZN"), None)
    
    assert azn is not None
    assert azn["symbol"] == "₼"
    assert azn["name"] == "Azerbaijani Manat"
    assert azn["flag"] == "🇦🇿"