from fastapi import APIRouter

from app.core.config import get_settings

router = APIRouter(prefix="/api/currencies", tags=["currencies"])

CURRENCY_META = {
    "BRL": {"symbol": "R$", "name": "Real Brasileiro", "flag": "\U0001f1e7\U0001f1f7"},
    "USD": {"symbol": "$", "name": "US Dollar", "flag": "\U0001f1fa\U0001f1f8"},
    "EUR": {"symbol": "\u20ac", "name": "Euro", "flag": "\U0001f1ea\U0001f1fa"},
    "GBP": {"symbol": "\u00a3", "name": "British Pound", "flag": "\U0001f1ec\U0001f1e7"},
    "JPY": {"symbol": "\u00a5", "name": "Japanese Yen", "flag": "\U0001f1ef\U0001f1f5"},
    "CAD": {"symbol": "C$", "name": "Canadian Dollar", "flag": "\U0001f1e8\U0001f1e6"},
    "AUD": {"symbol": "A$", "name": "Australian Dollar", "flag": "\U0001f1e6\U0001f1fa"},
    "CHF": {"symbol": "Fr", "name": "Swiss Franc", "flag": "\U0001f1e8\U0001f1ed"},
    "CNY": {"symbol": "\u00a5", "name": "Chinese Yuan", "flag": "\U0001f1e8\U0001f1f3"},
    "ARS": {"symbol": "$", "name": "Peso Argentino", "flag": "\U0001f1e6\U0001f1f7"},
    "MXN": {"symbol": "$", "name": "Peso Mexicano", "flag": "\U0001f1f2\U0001f1fd"},
    "CLP": {"symbol": "$", "name": "Peso Chileno", "flag": "\U0001f1e8\U0001f1f1"},
    "COP": {"symbol": "$", "name": "Peso Colombiano", "flag": "\U0001f1e8\U0001f1f4"},
    "PEN": {"symbol": "S/", "name": "Sol Peruano", "flag": "\U0001f1f5\U0001f1ea"},
    "UYU": {"symbol": "$U", "name": "Peso Uruguayo", "flag": "\U0001f1fa\U0001f1fe"},
    "INR": {"symbol": "\u20b9", "name": "Indian Rupee", "flag": "\U0001f1ee\U0001f1f3"},
    "SEK": {"symbol": "kr", "name": "Swedish Krona", "flag": "\U0001f1f8\U0001f1ea"},
    "DKK": {"symbol": "kr", "name": "Danish Krone", "flag": "\U0001f1e9\U0001f1f0"},
    "NOK": {"symbol": "kr", "name": "Norwegian Krone", "flag": "\U0001f1f3\U0001f1f4"},
    "PLN": {"symbol": "zł", "name": "Polish Złoty", "flag": "\U0001f1f5\U0001f1f1"},
    "CZK": {"symbol": "Kč", "name": "Czech Koruna", "flag": "\U0001f1e8\U0001f1ff"},
    "HUF": {"symbol": "Ft", "name": "Hungarian Forint", "flag": "\U0001f1ed\U0001f1fa"},
    "RON": {"symbol": "lei", "name": "Romanian Leu", "flag": "\U0001f1f7\U0001f1f4"},
    "CRC": {"symbol": "₡", "name": "Costa Rican Colón", "flag": "\U0001f1e8\U0001f1f7"},
    "IDR": {"symbol": "Rp", "name": "Indonesian Rupiah", "flag": "\U0001f1ee\U0001f1e9"},
    "DOP": {"symbol": "RD$", "name": "Peso Dominicano", "flag": "\U0001f1e9\U0001f1f4"},
    "RUB": {"symbol": "₽", "name": "Russian Ruble", "flag": "\U0001f1f7\U0001f1fa"},
    "GTQ": {"symbol": "Q", "name": "Guatemalan Quetzal", "flag": "\U0001f1ec\U0001f1f9"},
    "PHP": {"symbol": "₱", "name": "Philippine Peso", "flag": "\U0001f1f5\U0001f1ed"},
}


@router.get("")
async def list_currencies():
    """Return the list of supported currencies configured for this instance."""
    settings = get_settings()
    codes = [c.strip() for c in settings.supported_currencies.split(",") if c.strip()]

    currencies = []
    for code in codes:
        meta = CURRENCY_META.get(code, {})
        currencies.append(
            {
                "code": code,
                "symbol": meta.get("symbol", code),
                "name": meta.get("name", code),
                "flag": meta.get("flag", ""),
            }
        )

    return currencies
