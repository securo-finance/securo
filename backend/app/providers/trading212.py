"""Read-only Trading 212 brokerage provider."""
from __future__ import annotations

import base64
import hashlib
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

import httpx

from app.agents.services.crypto import decrypt, encrypt
from app.providers.base import (
    AccountData,
    BankProvider,
    ConnectionData,
    HoldingData,
    TransactionData,
    ProviderRateLimited,
    ProviderUserActionRequired,
)

TRADING212_LIVE_BASE_URL = "https://live.trading212.com"
TRADING212_DEMO_BASE_URL = "https://demo.trading212.com"
TRADING212_TIMEOUT = 30.0
_ACCOUNT_SUMMARY_PATH = "/api/v0/equity/account/summary"
_POSITIONS_PATH = "/api/v0/equity/positions"
_HISTORY_TRANSACTIONS_PATH = "/api/v0/equity/history/transactions"
_HISTORY_DIVIDENDS_PATH = "/api/v0/equity/history/dividends"
_HISTORY_ORDERS_PATH = "/api/v0/equity/history/orders"
_READ_ONLY_PATHS = frozenset({
    _ACCOUNT_SUMMARY_PATH,
    _POSITIONS_PATH,
    _HISTORY_TRANSACTIONS_PATH,
    _HISTORY_DIVIDENDS_PATH,
    _HISTORY_ORDERS_PATH,
})


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value if value is not None else 0))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


def _required_decimal(value: Any, field: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"Trading 212 payload has invalid {field}") from exc
    if not parsed.is_finite():
        raise ValueError(f"Trading 212 payload has invalid {field}")
    return parsed


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, float):
        return str(Decimal(str(value)))
    return value


def _stable_row_id(row: dict) -> str:
    # Provider IDs are bounded in our schema. A deterministic digest protects
    # the fallback path from oversized or nested response values.
    encoded = repr(sorted((str(key), repr(value)) for key, value in row.items())).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _bounded_reference(item: dict, field: str) -> str:
    """Return a normalized provider reference safe for a Transaction external ID."""
    reference = item.get("reference")
    if reference is None:
        return _stable_row_id(item)
    if not isinstance(reference, str):
        raise ValueError(f"Trading 212 payload has invalid {field}")
    normalized = reference.strip()
    if not normalized or len(normalized) > 200 or not normalized.isprintable():
        raise ValueError(f"Trading 212 payload has invalid {field}")
    return normalized


def _parse_date(value: Any) -> date:
    text = str(value or "").strip().replace("Z", "+00:00")
    try:
        return date.fromisoformat(text[:10])
    except ValueError as exc:
        raise ValueError("Trading 212 payload has invalid date") from exc


class Trading212Provider(BankProvider):
    """Trading 212's API-key connector; it never submits trading requests."""

    kind = "brokerage"

    @property
    def name(self) -> str:
        return "trading212"

    @property
    def flow_type(self) -> str:
        return "token"

    async def _client(self, credentials: dict | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=TRADING212_TIMEOUT)

    @staticmethod
    def _base_url(credentials: dict) -> str:
        environment = str((credentials or {}).get("environment") or "live").lower()
        return TRADING212_DEMO_BASE_URL if environment == "demo" else TRADING212_LIVE_BASE_URL

    @staticmethod
    def _api_key(credentials: dict) -> str:
        encrypted = (credentials or {}).get("api_key_enc")
        return decrypt(encrypted) or (credentials or {}).get("api_key") or ""

    @staticmethod
    def _api_secret(credentials: dict) -> str:
        encrypted = (credentials or {}).get("api_secret_enc")
        return decrypt(encrypted) or (credentials or {}).get("api_secret") or ""

    @classmethod
    def _auth_header(cls, credentials: dict) -> str:
        api_key = cls._api_key(credentials)
        api_secret = cls._api_secret(credentials)
        if not api_key or not api_secret:
            raise ValueError("Trading 212 API key and secret are required")
        token = base64.b64encode(f"{api_key}:{api_secret}".encode("utf-8")).decode("ascii")
        return f"Basic {token}"

    @staticmethod
    def _parse_token(raw: str) -> dict:
        parts = (raw or "").strip().split(":", 2)
        if len(parts) == 3 and parts[0].lower() in {"demo", "live"}:
            environment, api_key, api_secret = parts[0].lower(), parts[1].strip(), parts[2].strip()
        elif len(parts) == 2:
            environment, api_key, api_secret = "live", parts[0].strip(), parts[1].strip()
        else:
            raise ValueError(
                "Trading 212 credentials must be '<api_key>:<api_secret>' or "
                "'<demo|live>:<api_key>:<api_secret>'"
            )
        if not api_key or not api_secret:
            raise ValueError("Trading 212 API key and secret are required")
        return {
            "api_key_enc": encrypt(api_key),
            "api_secret_enc": encrypt(api_secret),
            "environment": environment,
        }

    async def _get_json(
        self, credentials: dict, path: str, params: Optional[dict] = None
    ) -> Any:
        if path not in _READ_ONLY_PATHS:
            raise ValueError(f"Trading 212 endpoint is not allowed: {path}")
        headers = {
            "Accept": "application/json",
            "Authorization": self._auth_header(credentials),
            "User-Agent": "Securo/0.1",
        }
        async with await self._client(credentials) as client:
            response = await client.get(
                f"{self._base_url(credentials)}{path}", params=params, headers=headers
            )
        if response.status_code in {401, 403}:
            raise ProviderUserActionRequired(
                "Trading 212 credentials require reconnecting", code="reconnect_required"
            )
        if response.status_code == 429:
            raise ProviderRateLimited("Trading 212 rate limit exceeded")
        response.raise_for_status()
        data = response.json()
        return {} if data is None else data

    async def _get_paginated(
        self, credentials: dict, path: str, params: Optional[dict] = None
    ) -> list[dict]:
        """Recover only the known stale T212 transaction-history cursor case."""
        items: list[dict] = []
        seen: set[str] = set()
        next_path: Optional[str] = path
        next_params = dict(params or {})
        last_page: list[dict] = []
        requested: set[tuple[str, tuple[tuple[str, str], ...]]] = set()

        def boundary(page: list[dict]) -> Optional[dict]:
            if path != _HISTORY_TRANSACTIONS_PATH or not page:
                return None
            last = page[-1]
            if not last.get("reference") or not last.get("dateTime"):
                return None
            return {**(params or {}), "cursor": str(last["reference"]), "time": str(last["dateTime"])}

        while next_path:
            request_key = (next_path, tuple(sorted((str(k), str(v)) for k, v in next_params.items())))
            if request_key in requested:
                raise RuntimeError("Trading 212 pagination repeated a page boundary")
            requested.add(request_key)
            try:
                data = await self._get_json(credentials, next_path, next_params)
            except httpx.HTTPStatusError as exc:
                error_type = ""
                if exc.response.status_code == 404:
                    try:
                        error_type = str(exc.response.json().get("type") or "")
                    except (TypeError, ValueError):
                        pass
                fallback = boundary(last_page)
                if (
                    exc.response.status_code != 404
                    or error_type != "/api-errors/entity-not-found"
                    or fallback is None
                ):
                    raise
                next_path, next_params = path, fallback
                continue

            page = data.get("items") if isinstance(data, dict) else data
            page = [row for row in page if isinstance(row, dict)] if isinstance(page, list) else []
            last_page = page
            for row in page:
                row_id = _stable_row_id(row)
                if row_id not in seen:
                    seen.add(row_id)
                    items.append(row)
            next_page = data.get("nextPagePath") if isinstance(data, dict) else None
            if not next_page:
                break
            if str(next_page).startswith("/"):
                next_path, _, query = str(next_page).partition("?")
                next_params = dict(httpx.QueryParams(query))
            else:
                next_path = path
                next_params = dict(httpx.QueryParams(str(next_page).lstrip("?")))
        return items

    async def get_history_transactions(self, credentials: dict, limit: int = 50) -> list[dict]:
        return await self._get_paginated(credentials, _HISTORY_TRANSACTIONS_PATH, {"limit": str(limit)})

    async def get_dividends(self, credentials: dict, limit: int = 50) -> list[dict]:
        return await self._get_paginated(credentials, _HISTORY_DIVIDENDS_PATH, {"limit": str(limit)})

    async def get_historical_orders(self, credentials: dict, limit: int = 50) -> list[dict]:
        return await self._get_paginated(credentials, _HISTORY_ORDERS_PATH, {"limit": str(limit)})

    async def get_account_summary(self, credentials: dict) -> dict:
        data = await self._get_json(credentials, _ACCOUNT_SUMMARY_PATH)
        if not isinstance(data, dict) or not str(data.get("id") or "").strip():
            raise ValueError("Trading 212 account summary did not include an account id")
        if not isinstance(data.get("cash"), dict):
            raise ValueError("Trading 212 account summary did not include cash data")
        return data

    async def get_positions(self, credentials: dict) -> list[dict]:
        data = await self._get_json(credentials, _POSITIONS_PATH)
        if isinstance(data, list):
            positions = data
        elif isinstance(data, dict) and isinstance(data.get("items"), list):
            positions = data["items"]
        else:
            raise ValueError("Trading 212 positions response has an unrecognized shape")

        if not all(isinstance(position, dict) for position in positions):
            raise ValueError("Trading 212 positions response contains a malformed position")
        for position in positions:
            instrument = position.get("instrument")
            ticker = position.get("ticker") or (
                instrument.get("ticker") if isinstance(instrument, dict) else None
            )
            if not ticker:
                raise ValueError("Trading 212 positions response contains a position without a ticker")
        return positions

    def get_oauth_url(self, *args, **kwargs):  # type: ignore[override]
        raise NotImplementedError("Trading 212 uses an API key token flow, not OAuth redirect")

    async def handle_oauth_callback(self, code: str) -> ConnectionData:
        credentials = self._parse_token(code)
        summary = await self.get_account_summary(credentials)
        account_id = str(summary.get("id") or "")
        if not account_id:
            raise ValueError("Trading 212 account summary did not include an account id")
        credentials["account_id"] = account_id
        return ConnectionData(
            external_id=account_id,
            institution_name="Trading 212",
            credentials=credentials,
            accounts=self._accounts_from_summary(summary),
        )

    @staticmethod
    def _accounts_from_summary(summary: dict) -> list[AccountData]:
        account_id = str(summary.get("id") or "").strip()
        cash = summary.get("cash")
        if not account_id or not isinstance(cash, dict):
            raise ValueError("Trading 212 account summary is malformed")
        metadata = {
            "trading212": {
                "accountId": account_id,
                "cash": _json_safe(cash),
                "investments": _json_safe(summary.get("investments") or {}),
                "totalValue": str(_decimal(summary.get("totalValue"))),
            }
        }
        return [
            AccountData(
                external_id=f"trading212:{account_id}:cash",
                name="Trading 212 Cash",
                type="investment",
                balance=sum(
                    (_decimal(cash.get(key)) for key in ("availableToTrade", "inPies", "reservedForOrders")),
                    Decimal("0"),
                ),
                currency=str(summary.get("currency") or "EUR"),
                metadata=metadata,
            )
        ]

    async def get_accounts(self, credentials: dict) -> list[AccountData]:
        return self._accounts_from_summary(await self.get_account_summary(credentials))

    async def get_transactions(
        self,
        credentials: dict,
        account_external_id: str,
        since: Optional[date] = None,
        payee_source: str = "auto",
    ) -> list[TransactionData]:
        rows = [self._map_history_transaction(item) for item in await self.get_history_transactions(credentials)]
        rows.extend(self._map_dividend(item) for item in await self.get_dividends(credentials))
        if since is not None:
            rows = [row for row in rows if row.date >= since]
        return rows

    @staticmethod
    def _map_history_transaction(item: dict) -> TransactionData:
        kind = str(item.get("type") or "").upper()
        direction = {
            "DEPOSIT": "credit",
            "WITHDRAWAL": "debit",
            "FEE": "debit",
        }
        amount_raw = _required_decimal(item.get("amount"), "transaction amount")
        if kind == "TRANSFER":
            direction[kind] = "credit" if amount_raw > 0 else "debit"
        if kind not in direction:
            raise ValueError(f"Trading 212 transaction type is unsupported: {kind}")
        reference = str(item.get("reference") or _stable_row_id(item))
        if len(reference) > 200:
            raise ValueError("Trading 212 transaction reference is too long")
        return TransactionData(
            external_id=f"t212:cash:{reference}",
            description=f"Trading 212 {kind.lower()}",
            amount=abs(amount_raw),
            date=_parse_date(item.get("dateTime")),
            type=direction[kind],
            currency=item.get("currency"),
            raw_data={"trading212": {"source": "history/transactions", "payload": _json_safe(item)}},
            is_ignored=kind == "TRANSFER",
        )

    @staticmethod
    def _map_dividend(item: dict) -> TransactionData:
        reference = _bounded_reference(item, "dividend reference")
        ticker = item.get("ticker") or ""
        return TransactionData(
            external_id=f"t212:dividend:{reference}",
            description=f"Trading 212 dividend{f' {ticker}' if ticker else ''}",
            amount=abs(_required_decimal(item.get("amount"), "dividend amount")),
            date=_parse_date(item.get("paidOn")),
            type="credit",
            currency=item.get("currency"),
            raw_data={"trading212": {"source": "history/dividends", "payload": _json_safe(item)}},
        )

    async def refresh_credentials(self, credentials: dict) -> dict:
        return credentials

    async def get_holdings(self, credentials: dict) -> list[HoldingData]:
        holdings: list[HoldingData] = []
        for position in await self.get_positions(credentials):
            instrument = position.get("instrument") if isinstance(position.get("instrument"), dict) else {}
            wallet = position.get("walletImpact") if isinstance(position.get("walletImpact"), dict) else {}
            ticker = position.get("ticker") or instrument.get("ticker")
            if not ticker:
                continue
            holdings.append(
                HoldingData(
                    external_id=(
                        f"trading212:position:{credentials['account_id']}:{ticker}"
                        if credentials.get("account_id")
                        else f"trading212:position:{ticker}"
                    ),
                    name=str(instrument.get("name") or ticker),
                    currency=str(wallet.get("currency") or position.get("currency") or "EUR"),
                    current_value=_decimal(wallet.get("currentValue")),
                    quantity=_decimal(position.get("quantity")),
                    unit_price=_decimal(position.get("currentPrice")),
                    purchase_price=_decimal(wallet.get("totalCost")),
                    isin=instrument.get("isin"),
                    ticker=str(ticker),
                    metadata={
                        "trading212": _json_safe(
                            {
                                **position,
                                "instrument": instrument,
                                "walletImpact": wallet,
                                "quantityInPies": _decimal(position.get("quantityInPies")),
                            }
                        )
                    },
                )
            )
        return holdings
