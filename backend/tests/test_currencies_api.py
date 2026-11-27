from httpx import AsyncClient

from app.core.config import get_settings


async def test_lists_the_configured_currencies_in_order_with_fallback_metadata(
    client: AsyncClient, monkeypatch
):
    """The list follows SUPPORTED_CURRENCIES; a code with no metadata falls
    back to its own code as symbol and name."""
    monkeypatch.setattr(get_settings(), "supported_currencies", " USD, BRL,,XYZ ")

    response = await client.get("/api/currencies")

    assert response.status_code == 200
    assert response.json() == [
        {"code": "USD", "symbol": "$", "name": "US Dollar", "flag": "🇺🇸"},
        {"code": "BRL", "symbol": "R$", "name": "Real Brasileiro", "flag": "🇧🇷"},
        {"code": "XYZ", "symbol": "XYZ", "name": "XYZ", "flag": ""},
    ]
