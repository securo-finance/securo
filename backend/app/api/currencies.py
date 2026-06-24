from fastapi import APIRouter

from app.core.config import get_settings

router = APIRouter(prefix="/api/currencies", tags=["currencies"])

CURRENCY_META = {
    "BRL": {"symbol": "R$", "name": "Real Brasileiro", "flag": "\U0001F1E7\U0001F1F7"},
    "USD": {"symbol": "$", "name": "US Dollar", "flag": "\U0001F1FA\U0001F1F8"},
    "EUR": {"symbol": "\u20ac", "name": "Euro", "flag": "\U0001F1EA\U0001F1FA"},
    "GBP": {"symbol": "\u00a3", "name": "British Pound", "flag": "\U0001F1EC\U0001F1E7"},
    "JPY": {"symbol": "\u00a5", "name": "Japanese Yen", "flag": "\U0001F1EF\U0001F1F5"},
    "CAD": {"symbol": "C$", "name": "Canadian Dollar", "flag": "\U0001F1E8\U0001F1E6"},
    "AUD": {"symbol": "A$", "name": "Australian Dollar", "flag": "\U0001F1E6\U0001F1FA"},
    "CHF": {"symbol": "Fr", "name": "Swiss Franc", "flag": "\U0001F1E8\U0001F1ED"},
    "CNY": {"symbol": "\u00a5", "name": "Chinese Yuan", "flag": "\U0001F1E8\U0001F1F3"},
    "ARS": {"symbol": "$", "name": "Peso Argentino", "flag": "\U0001F1E6\U0001F1F7"},
    "MXN": {"symbol": "$", "name": "Peso Mexicano", "flag": "\U0001F1F2\U0001F1FD"},
    "CLP": {"symbol": "$", "name": "Peso Chileno", "flag": "\U0001F1E8\U0001F1F1"},
    "COP": {"symbol": "$", "name": "Peso Colombiano", "flag": "\U0001F1E8\U0001F1F4"},
    "PEN": {"symbol": "S/", "name": "Sol Peruano", "flag": "\U0001F1F5\U0001F1EA"},
    "UYU": {"symbol": "$U", "name": "Peso Uruguayo", "flag": "\U0001F1FA\U0001F1FE"},
    "INR": {"symbol": "\u20B9", "name": "Indian Rupee", "flag": "\U0001F1EE\U0001F1F3"},
    "SEK": {"symbol": "kr", "name": "Swedish Krona", "flag": "\U0001F1F8\U0001F1EA"},
    "DKK": {"symbol": "kr", "name": "Danish Krone", "flag": "\U0001F1E9\U0001F1F0"},
    "NOK": {"symbol": "kr", "name": "Norwegian Krone", "flag": "\U0001F1F3\U0001F1F4"},
    "PLN": {"symbol": "zł", "name": "Polish Złoty", "flag": "\U0001F1F5\U0001F1F1"},
    "CZK": {"symbol": "Kč", "name": "Czech Koruna", "flag": "\U0001F1E8\U0001F1FF"},
    "HUF": {"symbol": "Ft", "name": "Hungarian Forint", "flag": "\U0001F1ED\U0001F1FA"},
    "RON": {"symbol": "lei", "name": "Romanian Leu", "flag": "\U0001F1F7\U0001F1F4"},
    "CRC": {"symbol": "₡", "name": "Costa Rican Colón", "flag": "\U0001F1E8\U0001F1F7"},
    "IDR": {"symbol": "Rp", "name": "Indonesian Rupiah", "flag": "\U0001F1EE\U0001F1E9"},
    "DOP": {"symbol": "RD$", "name": "Peso Dominicano", "flag": "\U0001F1E9\U0001F1F4"},
    "RUB": {"symbol": "₽", "name": "Russian Ruble", "flag": "\U0001F1F7\U0001F1FA"},
}


@router.get("")
async def list_currencies():
    """Return the list of supported currencies configured for this instance."""
    settings = get_settings()
    codes = [c.strip() for c in settings.supported_currencies.split(",") if c.strip()]

    currencies = []
    for code in codes:
        meta = CURRENCY_META.get(code, {})
        currencies.append({
            "code": code,
            "symbol": meta.get("symbol", code),
            "name": meta.get("name", code),
            "flag": meta.get("flag", ""),
        })

    return currencies
