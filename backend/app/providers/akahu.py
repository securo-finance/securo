"""Connect Akahu Personal Apps using both App ID and User tokens.

Paste both tokens from https://my.akahu.nz/developers in either order.
Pending transactions lack stable IDs and cannot be reconciled by the sync layer.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional
from zoneinfo import ZoneInfo

import httpx

from app.agents.services.crypto import decrypt, encrypt
from app.providers.base import (
    AccountData,
    BankProvider,
    ConnectionData,
    ProviderRateLimited,
    ProviderUserActionRequired,
    RefreshOutcome,
    SessionExpiredError,
    TransactionData,
    mask_last4,
)

logger = logging.getLogger(__name__)

AKAHU_HTTP_TIMEOUT = 60.0
# Akahu returns New Zealand ledger timestamps in UTC. Convert them to the
# source ledger's civil date before Securo drops the time component.
AKAHU_LEDGER_TIMEZONE = ZoneInfo("Pacific/Auckland")

_HELP_URL = "https://my.akahu.nz/developers"

_TYPE_MAP = {
    "CHECKING": "checking",
    "SAVINGS": "savings",
    "CREDITCARD": "credit_card",
    "KIWISAVER": "investment",
    "INVESTMENT": "investment",
    "WALLET": "wallet",
}


def _parse_tokens(raw: str) -> tuple[str, str]:
    """Accept both tokens in either order, separated by whitespace."""
    app_token = user_token = ""
    for chunk in (raw or "").split():
        token = chunk.strip().strip(",;\"'")
        if token.startswith("app_token_"):
            app_token = token
        elif token.startswith("user_token_"):
            user_token = token
    if not app_token or not user_token:
        raise ValueError(
            "Paste both Akahu tokens: the App ID Token (app_token_…) and the "
            "User Token (user_token_…) from your Akahu developer page."
        )
    return app_token, user_token


def _require_object(value: Any, context: str) -> dict:
    if not isinstance(value, dict):
        raise RuntimeError(f"Akahu {context}: expected an object")
    return value


def _optional_object(value: Any, context: str) -> dict:
    return {} if value is None else _require_object(value, context)


def _items(payload: dict) -> list[dict]:
    items = payload.get("items")
    if not isinstance(items, list):
        raise RuntimeError("Akahu items: expected a list")
    return [_require_object(item, "item") for item in items]


def _to_decimal(value: Any) -> Optional[Decimal]:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _iso_to_date(value: Any) -> Optional[date]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.date()
    return parsed.astimezone(AKAHU_LEDGER_TIMEZONE).date()


class AkahuProvider(BankProvider):
    """Akahu personal-app connector."""

    @property
    def name(self) -> str:
        return "akahu"

    @property
    def flow_type(self) -> str:
        return "token"

    @staticmethod
    def _tokens(credentials: dict) -> tuple[str, str]:
        creds = credentials or {}
        app_token = decrypt(creds.get("app_token_enc") or "") or creds.get("app_token") or ""
        user_token = decrypt(creds.get("user_token_enc") or "") or creds.get("user_token") or ""
        if not app_token or not user_token:
            raise SessionExpiredError("Akahu tokens are missing")
        return app_token, user_token

    @staticmethod
    def _api_url() -> str:
        from app.core.config import get_settings

        return get_settings().akahu_api_url.rstrip("/")

    async def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=AKAHU_HTTP_TIMEOUT,
            headers={
                "Accept": "application/json",
                "User-Agent": "Securo/0.1 (+https://usesecuro.com)",
            },
        )

    async def _request(
        self,
        credentials: dict,
        method: str,
        path: str,
        params: Optional[dict] = None,
    ) -> dict:
        app_token, user_token = self._tokens(credentials)
        async with await self._client() as client:
            resp = await client.request(
                method,
                f"{self._api_url()}{path}",
                params=params,
                headers={
                    "Authorization": f"Bearer {user_token}",
                    "X-Akahu-Id": app_token,
                },
            )
        if resp.status_code in (401, 403):
            raise ProviderUserActionRequired(
                f"Akahu refused the request ({resp.status_code}). "
                "Check that both tokens are still active on your Akahu developer page.",
                code="credentials_invalid",
                help_url=_HELP_URL,
            )
        if resp.status_code == 429:
            raise ProviderRateLimited("Akahu is rate limiting requests")
        resp.raise_for_status()
        payload = _require_object(resp.json(), "response")
        if payload.get("success") is False:
            raise RuntimeError(f"Akahu error: {payload.get('message', 'unknown')}")
        return payload

    def get_oauth_url(self, *args, **kwargs):  # type: ignore[override]
        raise NotImplementedError("Akahu personal apps use paste-a-token flow, not OAuth redirect")

    async def handle_oauth_callback(self, code: str) -> ConnectionData:
        """Pass both Personal App tokens as whitespace-separated ``code``."""
        app_token, user_token = _parse_tokens(code)
        credentials: dict[str, Any] = {
            "app_token_enc": encrypt(app_token) or app_token,
            "user_token_enc": encrypt(user_token) or user_token,
            "connected_at": datetime.now(timezone.utc).isoformat(),
        }

        me = _optional_object(
            (await self._request(credentials, "GET", "/me")).get("item"), "identity"
        )
        accounts_payload = await self._request(credentials, "GET", "/accounts")
        accounts, institution_name, logo_url = self._parse_accounts(accounts_payload)

        external_id = str(me.get("_id") or "") or f"akahu-{app_token[-12:]}"
        return ConnectionData(
            external_id=external_id,
            institution_name=institution_name,
            credentials=credentials,
            accounts=accounts,
            logo_url=logo_url,
        )

    @staticmethod
    def _parse_accounts(payload: dict) -> tuple[list[AccountData], str, Optional[str]]:
        """One user token can span multiple banks, grouped under Akahu."""
        accounts: list[AccountData] = []
        connection_names: list[str] = []
        logo_url: Optional[str] = None
        for raw in _items(payload):
            account_id = str(raw.get("_id") or "")
            if not account_id:
                continue
            balance_obj = _optional_object(raw.get("balance"), "balance")
            balance = _to_decimal(balance_obj.get("current")) or Decimal("0")
            acc_type = _TYPE_MAP.get(str(raw.get("type") or "").upper(), "checking")
            if acc_type == "credit_card":
                # The sync layer stores connected credit cards positive-for-debt
                # (display negates); Akahu reports amounts owed as negative.
                balance = -balance
            connection = _optional_object(raw.get("connection"), "connection")
            if connection.get("name") and connection["name"] not in connection_names:
                connection_names.append(connection["name"])
            logo_url = logo_url or connection.get("logo")
            accounts.append(
                AccountData(
                    external_id=account_id,
                    name=raw.get("name") or "Account",
                    type=acc_type,
                    balance=balance,
                    currency=str(balance_obj.get("currency") or "NZD"),
                    credit_limit=_to_decimal(balance_obj.get("limit")),
                    masked_number=mask_last4(raw.get("formatted_account")),
                )
            )
        institution_name = connection_names[0] if len(connection_names) == 1 else "Akahu"
        return accounts, institution_name, logo_url

    async def get_accounts(self, credentials: dict) -> list[AccountData]:
        payload = await self._request(credentials, "GET", "/accounts")
        accounts, _, _ = self._parse_accounts(payload)
        return accounts

    async def get_institution_logo(self, credentials: dict) -> Optional[str]:
        payload = await self._request(credentials, "GET", "/accounts")
        _, _, logo_url = self._parse_accounts(payload)
        return logo_url

    async def get_transactions(
        self,
        credentials: dict,
        account_external_id: str,
        since: Optional[date] = None,
        payee_source: str = "auto",
    ) -> list[TransactionData]:
        params: dict[str, Any] = {"end": datetime.now(timezone.utc).isoformat()}
        if since is not None:
            midnight = datetime.combine(since, time.min, tzinfo=AKAHU_LEDGER_TIMEZONE)
            # Akahu's start is exclusive, so include the first ledger instant.
            start = midnight.astimezone(timezone.utc) - timedelta(milliseconds=1)
            params["start"] = start.isoformat()
        transactions: list[TransactionData] = []
        cursor: Optional[str] = None
        while True:
            if cursor:
                params["cursor"] = cursor
            payload = await self._request(
                credentials, "GET", f"/accounts/{account_external_id}/transactions", params=params
            )
            for raw in _items(payload):
                parsed = self._build_transaction(raw, payee_source)
                if parsed:
                    transactions.append(parsed)
            cursor = _optional_object(payload.get("cursor"), "cursor").get("next")
            if not cursor:
                break
        return transactions

    @staticmethod
    def _build_transaction(raw: dict, payee_source: str) -> Optional[TransactionData]:
        txn_id = str(raw.get("_id") or "")
        amount_raw = _to_decimal(raw.get("amount"))
        txn_date = _iso_to_date(raw.get("date"))
        if not txn_id or amount_raw is None or txn_date is None:
            return None
        merchant = _optional_object(raw.get("merchant"), "merchant").get("name")
        payee = None if payee_source == "none" else (merchant or None)
        return TransactionData(
            external_id=txn_id,
            description=(raw.get("description") or merchant or "Transaction").strip()[:500],
            amount=amount_raw.copy_abs(),
            date=txn_date,
            type="debit" if amount_raw < 0 else "credit",
            currency=None,
            status="posted",
            payee=payee,
            raw_data=raw,
        )

    async def refresh_credentials(self, credentials: dict) -> dict:
        self._tokens(credentials)
        return credentials

    async def trigger_refresh(self, credentials: dict | None) -> RefreshOutcome:
        """Request an asynchronous refresh. Immediate reads may still return cached data."""
        if not credentials:
            return "skipped"
        try:
            await self._request(credentials, "POST", "/refresh")
        except ProviderRateLimited:
            return "failed"
        return "refreshed"
