"""TrueLayer Data API provider.

TrueLayer uses a standard OAuth authorization-code flow. Securo stores the
resulting access/refresh tokens encrypted, refreshes access tokens before sync,
and maps the Data API account/balance/transaction payloads into the shared
BankProvider dataclasses.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional
from urllib.parse import urlencode, urlparse

import httpx

from app.agents.services.crypto import decrypt, encrypt
from app.core.config import get_settings
from app.providers.base import (
    AccountData,
    BankProvider,
    ConnectionData,
    InstitutionData,
    InstitutionListData,
    ProviderRateLimited,
    SessionExpiredError,
    TransactionData,
    default_oauth_redirect_uri,
    mask_last4,
)

logger = logging.getLogger(__name__)

DEFAULT_HISTORY_DAYS = 90
TOKEN_REFRESH_BUFFER_SECONDS = 300
# Every scope here is load-bearing, and nothing else is requested: `accounts`
# and `cards` for the two list endpoints, `balance` and `transactions` for the
# per-resource reads, `offline_access` for the refresh token without which the
# connection dies at the first token expiry. Notably absent is `info`, which
# returns the account holder's name, date of birth, address, phone and email:
# nothing here reads it, and asking for identity data we never use would put it
# on the consent screen of every bank a user connects. Anything added to this
# list must have an endpoint call to justify it.
TRUELAYER_SCOPES = "accounts balance transactions cards offline_access"

# TrueLayer answers `endpoint_not_supported` when the connected bank does not
# expose a resource at all: a card-only issuer (Barclaycard, Amex) has no
# /accounts, and a bank with no cards has no /cards.
ENDPOINT_NOT_SUPPORTED = (404, 501)
# Amex refuses the balance of a supplementary card with access_denied for the
# life of the connection, so a 403 on a *balance* means "not readable", not
# "session dead" — 401 stays fatal and triggers reauth.
BALANCE_UNAVAILABLE = (403, *ENDPOINT_NOT_SUPPORTED)

# TrueLayer labels providers with lowercase codes that are mostly ISO 3166-1
# alpha-2 but not always: it says "uk" where the standard says "GB". The
# institution picker speaks ISO, so translate on the way in and out.
_ISO_BY_TRUELAYER_COUNTRY = {"uk": "GB"}
_TRUELAYER_COUNTRY_BY_ISO = {v: k for k, v in _ISO_BY_TRUELAYER_COUNTRY.items()}


def _iso_country(value: Any) -> str:
    code = str(value or "").strip().lower()
    if not code:
        return ""
    return _ISO_BY_TRUELAYER_COUNTRY.get(code, code.upper())


def _truelayer_country(value: Any) -> str:
    code = str(value or "").strip().upper()
    if not code:
        return ""
    return _TRUELAYER_COUNTRY_BY_ISO.get(code, code.lower())


def _parse_datetime(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _parse_date(value: Any) -> Optional[date]:
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            parsed = _parse_datetime(value)
            return parsed.date() if parsed else None
    return None


def _day_of_month(value: Any) -> Optional[int]:
    """Day-of-month from a TrueLayer date, for the CC cycle fields.

    Securo stores the statement/due anchors as a day number and re-derives the
    cycle each month (see credit_card_service.get_cycle_dates).
    """
    parsed = _parse_date(value)
    return parsed.day if parsed else None


def _to_decimal(value: Any) -> Optional[Decimal]:
    if value is None or value == "":
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    # JSON has no NaN/Infinity, but Python's decoder accepts them anyway and
    # Decimal parses both without complaining. Neither is a usable amount, and
    # a Decimal NaN is actively dangerous: unlike a float NaN it *raises*
    # InvalidOperation on comparison, so one would abort a whole sync inside
    # _build_transaction rather than just spoiling its own row. Treat them as
    # unreadable, exactly like a value that failed to convert.
    return parsed if parsed.is_finite() else None


def _resource_id(raw: dict) -> str:
    """Account/card identifier.

    The Cards API keys each card by `account_id`, exactly like the Accounts
    API — there is no `card_id` field — so both share this lookup.
    """
    for key in ("account_id", "card_id", "id"):
        value = raw.get(key)
        if value:
            return str(value)
    return ""


def _balance_amount(balance: dict, *, fallback_to_available: bool = True) -> Decimal:
    """Pick the balance TrueLayer reports for an account or card.

    Presence, not truthiness: a genuine zero `current` must not fall through to
    `available`. Cards pass fallback_to_available=False because a card's
    `available` is available *credit* — the inverse of what it owes — so
    reading it as a balance would report a paid-off card as deeply in debt.
    """
    keys = ("current", "available") if fallback_to_available else ("current",)
    for key in keys:
        if balance.get(key) is not None:
            amount = _to_decimal(balance[key])
            if amount is not None:
                return amount
    return Decimal("0")


def _account_type(raw_type: Any) -> str:
    value = str(raw_type or "").upper()
    if "SAVING" in value:
        return "savings"
    if "CARD" in value:
        return "credit_card"
    return "checking"


def _description(raw: dict) -> str:
    for key in ("description", "merchant_name", "transaction_type"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "Transaction"


def _transaction_id(account_external_id: str, raw: dict) -> str:
    for key in ("transaction_id", "id", "provider_transaction_id"):
        value = raw.get(key)
        if value:
            return str(value)
    parts = [
        account_external_id,
        str(raw.get("timestamp") or raw.get("booking_date") or raw.get("date") or ""),
        str(raw.get("amount") or ""),
        str(raw.get("currency") or ""),
        _description(raw),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]


def _error_detail(response: httpx.Response) -> str:
    """Short, single-line excerpt of a provider error body for log messages."""
    try:
        body = response.text
    except Exception:  # pragma: no cover - body already released
        return ""
    body = " ".join(body.split())
    if not body:
        return ""
    return f" - {body[:300]}"


def _token_value(credentials: dict, key: str) -> str:
    enc = (credentials or {}).get(f"{key}_enc")
    if enc:
        decoded = decrypt(enc)
        if decoded:
            return decoded
    return (credentials or {}).get(key) or ""


def _json_object(response: httpx.Response, context: str) -> dict:
    """Decode a response body that must be a JSON object.

    TrueLayer answers the token and Data API endpoints with a mapping. Anything
    else — an array, a bare string, a truncated body substituted by a proxy —
    would otherwise sail past the status checks and only fail later on an
    attribute lookup, far from the cause. Treat it like any other bad response
    so the sync aborts instead of persisting whatever a half-parsed payload
    happens to yield.

    Note this is deliberately not used for the public provider catalogue, which
    is legitimately a JSON array.
    """
    try:
        payload = response.json()
    except ValueError:  # includes json.JSONDecodeError
        payload = None
    if not isinstance(payload, dict):
        raise httpx.HTTPStatusError(
            f"TrueLayer {context} returned a non-object payload",
            request=response.request,
            response=response,
        )
    return payload


def _https_endpoint(value: Any, setting: str) -> str:
    """Normalise a configured TrueLayer endpoint, rejecting anything but HTTPS.

    The client secret is posted to the auth host and every Data API call
    carries a bearer token, so a plaintext endpoint would put both on the
    wire. Both settings ship as https:// and TrueLayer serves nothing over
    http — including its sandbox — so a downgrade can only be a deployment
    mistake, and it must fail loudly rather than leak credentials.
    """
    url = str(value or "").strip().rstrip("/")
    if not url:
        raise ValueError(f"{setting} is not configured")
    if urlparse(url).scheme != "https":
        raise ValueError(f"{setting} must be an https:// URL")
    return url


def _credentials_from_token_payload(data: dict) -> dict[str, Any]:
    access_token = data.get("access_token") or ""
    refresh_token = data.get("refresh_token") or ""
    expires_in = int(data.get("expires_in") or 3600)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    credentials: dict[str, Any] = {
        "access_token_enc": encrypt(access_token) or access_token,
        "expires_at": expires_at.isoformat(),
        "token_type": data.get("token_type") or "Bearer",
    }
    # Record a scope only when the response reported one. OAuth2 makes it
    # optional and a refresh grant routinely omits it, since the scope has not
    # changed — so writing None unconditionally would erase the scope captured
    # at authorization the first time refresh_credentials merges this over the
    # stored credentials. Keep what the bank told us it granted; it is the only
    # record of that, and it is what a "why is this account missing?" question
    # gets answered from.
    if (scope := data.get("scope")) is not None:
        credentials["scope"] = scope
    if refresh_token:
        credentials["refresh_token_enc"] = encrypt(refresh_token) or refresh_token
    return credentials


class TrueLayerProvider(BankProvider):
    """TrueLayer open-banking connector."""

    @property
    def name(self) -> str:
        return "truelayer"

    @property
    def flow_type(self) -> str:
        return "oauth"

    @property
    def redirect_uri(self) -> str:
        return get_settings().truelayer_redirect_uri or default_oauth_redirect_uri()

    def _auth_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=_https_endpoint(
                get_settings().truelayer_auth_url, "TRUELAYER_AUTH_URL"
            ),
            timeout=30.0,
        )

    def _api_client(self, access_token: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=_https_endpoint(
                get_settings().truelayer_api_url, "TRUELAYER_API_URL"
            ),
            headers={
                "Authorization": " ".join(("Bearer", access_token)),
                "Accept": "application/json",
                "User-Agent": "Securo/0.1 (+https://usesecuro.com)",
            },
            timeout=30.0,
        )

    async def _exchange_token(self, payload: dict[str, str]) -> dict:
        settings = get_settings()
        body = {
            "client_id": settings.truelayer_client_id,
            "client_secret": settings.truelayer_client_secret.get_secret_value(),
            **payload,
        }
        async with self._auth_client() as client:
            response = await client.post("/connect/token", data=body)
        if response.status_code in (400, 401):
            raise SessionExpiredError(
                "TrueLayer authorization expired or was rejected"
                f"{_error_detail(response)}"
            )
        if response.status_code >= 400:
            raise httpx.HTTPStatusError(
                "TrueLayer token exchange failed: "
                f"{response.status_code}{_error_detail(response)}",
                request=response.request,
                response=response,
            )
        return _json_object(response, "token exchange")

    async def _request(
        self,
        credentials: dict,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        tolerate: tuple[int, ...] = (),
    ) -> Optional[dict]:
        """GET/POST the Data API.

        `tolerate` lists statuses that mean "this resource does not exist for
        this bank" rather than "the call failed"; they return None so the
        caller can tell an absent resource from an empty one. Never tolerate
        401, and never tolerate 5xx or transport errors — a transient failure
        must propagate so the sync aborts instead of persisting a wrong value.
        """
        access_token = _token_value(credentials, "access_token")
        if not access_token:
            raise SessionExpiredError("TrueLayer access token missing")
        async with self._api_client(access_token) as client:
            response = await client.request(method, path, params=params)
        if response.status_code in tolerate:
            logger.warning(
                "TrueLayer %s %s unavailable for this connection: %s%s",
                method,
                path,
                response.status_code,
                _error_detail(response),
            )
            return None
        if response.status_code in (401, 403):
            raise SessionExpiredError("TrueLayer session expired")
        if response.status_code == 429:
            raise ProviderRateLimited(f"TrueLayer {method} {path} returned 429")
        if response.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"TrueLayer {method} {path} failed: "
                f"{response.status_code}{_error_detail(response)}",
                request=response.request,
                response=response,
            )
        return _json_object(response, f"{method} {path}")

    async def _fetch_providers(self, country: Optional[str] = None) -> list[dict]:
        """Read TrueLayer's public provider catalogue.

        Unauthenticated, but scoped to `clientId` so the list matches the banks
        this deployment is actually enabled for. Deliberately not filtered by
        `scopes`: that parameter keeps only providers supporting *every* scope
        asked for, which would hide card-only issuers such as Amex and
        Barclaycard — the very ones this provider goes out of its way to sync.
        """
        settings = get_settings()
        params: dict[str, str] = {}
        if settings.truelayer_client_id:
            params["clientId"] = settings.truelayer_client_id
        if country and (mapped := _truelayer_country(country)):
            params["country"] = mapped
        async with self._auth_client() as client:
            response = await client.get("/api/providers", params=params or None)
        if response.status_code >= 400:
            raise httpx.HTTPStatusError(
                "TrueLayer provider listing failed: "
                f"{response.status_code}{_error_detail(response)}",
                request=response.request,
                response=response,
            )
        payload = response.json()
        if not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, dict)]

    async def list_institutions(
        self, country: Optional[str] = None
    ) -> InstitutionListData:
        institutions: list[InstitutionData] = []
        countries: set[str] = set()
        for item in await self._fetch_providers(country):
            # provider_id is what the authorization link's `providers` filter
            # expects, so it is the canonical name we must round-trip.
            provider_id = str(item.get("provider_id") or "").strip()
            if not provider_id:
                continue
            iso = _iso_country(item.get("country"))
            if iso:
                countries.add(iso)
            institutions.append(
                InstitutionData(
                    name=provider_id,
                    display_name=str(item.get("display_name") or provider_id),
                    country=iso,
                    logo=item.get("logo_url"),
                )
            )
        institutions.sort(key=lambda i: (i.country, i.display_name.lower()))
        return InstitutionListData(
            countries=sorted(countries), institutions=institutions
        )

    async def get_oauth_url(
        self,
        redirect_uri: str,
        state: str,
        flow_params: Optional[dict] = None,
    ) -> str:
        settings = get_settings()
        flow = flow_params or {}
        # The institution picker posts the chosen bank as `institution_name`;
        # `providers` stays accepted so a caller can pass a raw TrueLayer filter.
        institution = str(flow.get("providers") or flow.get("institution_name") or "")
        scope = str(flow.get("scope") or TRUELAYER_SCOPES)
        params: dict[str, str] = {
            "response_type": "code",
            "client_id": settings.truelayer_client_id,
            "redirect_uri": redirect_uri,
            "scope": scope,
            "state": state,
        }
        if institution:
            params["providers"] = institution
        auth_url = _https_endpoint(settings.truelayer_auth_url, "TRUELAYER_AUTH_URL")
        return f"{auth_url}/?{urlencode(params)}"

    async def reauth_url(
        self,
        credentials: dict,
        settings: dict,
        redirect_uri: str,
        state: str,
    ) -> str:
        return await self.get_oauth_url(
            redirect_uri,
            state,
            flow_params=(settings or {}).get("flow_params") or {},
        )

    async def handle_oauth_callback(self, code: str) -> ConnectionData:
        token_data = await self._exchange_token(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.redirect_uri,
            }
        )
        credentials = _credentials_from_token_payload(token_data)
        accounts = await self.get_accounts(credentials)
        first = accounts[0] if accounts else None
        institution_name = first.institution_name if first and first.institution_name else "TrueLayer"
        external_id = (
            (first.institution_external_id if first else None)
            or (first.external_id if first else None)
            or hashlib.sha256(code.encode("utf-8")).hexdigest()[:32]
        )
        return ConnectionData(
            external_id=external_id,
            institution_name=institution_name,
            credentials=credentials,
            accounts=accounts,
            logo_url=first.institution_logo_url if first else None,
        )

    async def get_accounts(self, credentials: dict) -> list[AccountData]:
        # Either list endpoint may be absent — card-only issuers have no
        # /accounts, banks without cards have no /cards — but a connection
        # exposing neither is a misconfiguration (wrong TRUELAYER_API_URL,
        # missing scopes) that must not look like a bank with no accounts.
        data = await self._request(
            credentials, "GET", "/accounts", tolerate=ENDPOINT_NOT_SUPPORTED
        )
        cards = await self._request(
            credentials, "GET", "/cards", tolerate=ENDPOINT_NOT_SUPPORTED
        )
        if data is None and cards is None:
            raise RuntimeError(
                "TrueLayer exposes neither /accounts nor /cards for this "
                "connection - check TRUELAYER_API_URL and the granted scopes"
            )
        result: list[AccountData] = []
        for raw in (data or {}).get("results") or []:
            if isinstance(raw, dict) and _resource_id(raw):
                result.append(await self._build_account(credentials, raw))
        for raw in (cards or {}).get("results") or []:
            if isinstance(raw, dict) and _resource_id(raw):
                result.append(await self._build_card(credentials, raw))
        return result

    async def _build_account(self, credentials: dict, raw: dict) -> AccountData:
        account_id = _resource_id(raw)
        balance_raw = await self._balance(credentials, f"/accounts/{account_id}/balance")
        currency = raw.get("currency") or balance_raw.get("currency") or "GBP"
        provider = raw.get("provider") or {}
        account_number = raw.get("account_number") or {}
        return AccountData(
            external_id=account_id,
            name=raw.get("display_name") or raw.get("account_type") or "Account",
            type=_account_type(raw.get("account_type")),
            balance=_balance_amount(balance_raw),
            currency=currency,
            masked_number=mask_last4(
                account_number.get("iban")
                or account_number.get("number")
                or account_number.get("swift_bic")
            ),
            institution_external_id=provider.get("provider_id"),
            institution_name=provider.get("display_name"),
            institution_logo_url=provider.get("logo_uri"),
        )

    async def _build_card(self, credentials: dict, raw: dict) -> AccountData:
        card_id = _resource_id(raw)
        balance_raw = await self._balance(credentials, f"/cards/{card_id}/balance")
        provider = raw.get("provider") or {}
        return AccountData(
            external_id=f"card:{card_id}",
            name=raw.get("display_name") or "Credit card",
            type="credit_card",
            balance=_balance_amount(balance_raw, fallback_to_available=False),
            currency=raw.get("currency") or balance_raw.get("currency") or "GBP",
            masked_number=mask_last4(raw.get("partial_card_number")),
            credit_limit=_to_decimal(balance_raw.get("credit_limit")),
            # `payment_due` is documented as the minimum due by the due date,
            # so it maps to minimum_payment rather than the statement total.
            statement_close_day=_day_of_month(balance_raw.get("last_statement_date")),
            payment_due_day=_day_of_month(balance_raw.get("payment_due_date")),
            minimum_payment=_to_decimal(balance_raw.get("payment_due")),
            # TrueLayer reports the network (VISA/MASTERCARD/AMEX). It has no
            # equivalent of Pluggy's card tier, so card_level stays unset.
            card_brand=raw.get("card_network") or None,
            institution_external_id=provider.get("provider_id"),
            institution_name=provider.get("display_name"),
            institution_logo_url=provider.get("logo_uri"),
        )

    async def _balance(self, credentials: dict, path: str) -> dict:
        # A balance the bank will never serve (Amex supplementary cards, or a
        # provider without the endpoint) degrades to zero. A timeout or 5xx
        # does NOT: sync writes account.balance unconditionally, so swallowing
        # a transient failure would overwrite a real balance with zero.
        data = await self._request(
            credentials, "GET", path, tolerate=BALANCE_UNAVAILABLE
        )
        if data is None:
            return {}
        balances = data.get("results") or []
        return balances[0] if balances and isinstance(balances[0], dict) else {}

    async def get_transactions(
        self,
        credentials: dict,
        account_external_id: str,
        since: Optional[date] = None,
        payee_source: str = "auto",
    ) -> list[TransactionData]:
        is_card = account_external_id.startswith("card:")
        resource_id = account_external_id.removeprefix("card:")
        path = f"/cards/{resource_id}/transactions" if is_card else f"/accounts/{resource_id}/transactions"
        date_from = since or (date.today() - timedelta(days=DEFAULT_HISTORY_DAYS))
        params = {"from": date_from.isoformat(), "to": date.today().isoformat()}
        data = await self._request(credentials, "GET", path, params=params) or {}
        return [
            txn
            for raw in data.get("results") or []
            if isinstance(raw, dict)
            for txn in [self._build_transaction(account_external_id, raw, payee_source)]
            if txn is not None
        ]

    def _build_transaction(
        self,
        account_external_id: str,
        raw: dict,
        payee_source: str,
    ) -> Optional[TransactionData]:
        amount = _to_decimal(raw.get("amount"))
        if amount is None:
            return None
        txn_date = (
            _parse_date(raw.get("timestamp"))
            or _parse_date(raw.get("booking_date"))
            or _parse_date(raw.get("date"))
        )
        if txn_date is None:
            return None
        status = str(raw.get("status") or "").lower()
        return TransactionData(
            external_id=_transaction_id(account_external_id, raw),
            description=_description(raw),
            amount=amount.copy_abs(),
            date=txn_date,
            type="debit" if amount < 0 else "credit",
            currency=raw.get("currency"),
            pluggy_category=raw.get("transaction_category")
            or raw.get("provider_transaction_category"),
            status="pending" if status == "pending" else "posted",
            payee=None if payee_source in {"none", "description"} else raw.get("merchant_name"),
            raw_data=raw,
        )

    async def refresh_credentials(self, credentials: dict) -> dict:
        expires_at = _parse_datetime((credentials or {}).get("expires_at"))
        if expires_at and (
            expires_at - datetime.now(timezone.utc)
        ).total_seconds() > TOKEN_REFRESH_BUFFER_SECONDS:
            return credentials
        refresh_token = _token_value(credentials, "refresh_token")
        if not refresh_token:
            raise SessionExpiredError("TrueLayer refresh token missing")
        token_data = await self._exchange_token(
            {"grant_type": "refresh_token", "refresh_token": refresh_token}
        )
        refreshed = dict(credentials or {})
        refreshed.update(_credentials_from_token_payload(token_data))
        if "refresh_token_enc" not in refreshed and refresh_token:
            refreshed["refresh_token_enc"] = encrypt(refresh_token) or refresh_token
        return refreshed
