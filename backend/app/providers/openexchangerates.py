import logging
from datetime import date
from decimal import Decimal

import httpx

from app.core.config import Settings, get_settings
from app.providers.base import FxRateProvider

logger = logging.getLogger(__name__)

BASE_URL = "https://openexchangerates.org/api"


class _RedactAppId(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                arg.copy_set_param("app_id", "[redacted]")
                if isinstance(arg, httpx.URL) and arg.host == "openexchangerates.org" and "app_id" in arg.params
                else arg
                for arg in record.args
            )
        return True


# HTTPX logs request URLs at INFO, including this provider's query credential.
logging.getLogger("httpx").addFilter(_RedactAppId())


class OpenExchangeRatesProvider(FxRateProvider):
    """FX rate provider using Open Exchange Rates API."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings

    @property
    def name(self) -> str:
        return "openexchangerates"

    def _app_id(self) -> str:
        return (self.settings or get_settings()).openexchangerates_app_id

    def _symbols(self) -> str:
        """Return comma-separated supported currencies for the OER `symbols` param."""
        return get_settings().supported_currencies

    async def fetch_latest(self) -> dict[str, Decimal]:
        return await self._fetch("latest.json")

    async def fetch_historical(self, target_date: date) -> dict[str, Decimal]:
        return await self._fetch(f"historical/{target_date.isoformat()}.json")

    async def _fetch(self, path: str) -> dict[str, Decimal]:
        app_id = self._app_id()
        if not app_id:
            raise ValueError("openexchangerates_app_id not configured")
        params = {"app_id": app_id, "symbols": self._symbols()}
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(f"{BASE_URL}/{path}", params=params)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError:
            # HTTPX exception messages contain the credential-bearing URL.
            raise ValueError("Exchange-rate provider request failed") from None
        return {code: Decimal(str(rate)) for code, rate in data.get("rates", {}).items()}
