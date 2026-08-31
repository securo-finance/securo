"""Wealth Reader AIS provider — European banks via https://www.wealthreader.com.

Auth model (BYO api_key):
  1. GET /entities/ lists institutions (``code`` is the stable id).
  2. OAuth redirect to oauth.wealthreader.com/oauth2/ with a PKCE challenge
     that is *not* RFC 7636: nonce/state/code_verifier are
     bin2hex(randomAlnum(41)), challenge_code is SHA-256 hex of the already-hex
     verifier. See www.wealthreader.com/docs/en/oauth-backend/.
  3. Callback returns ``code`` (+ ``state`` if echoed, else ``nonce``).
     POST /token/ exchanges them for the bank JSON and ``statistics.token``.
  4. Later refreshes: POST api.wealthreader.com/entities/ with
     api_key + code + token.

PKCE material is stashed in Redis (or an in-process fallback) keyed by the
Securo OAuth ``state`` *and* the WR ``nonce``, so the callback works whether
Wealth Reader echoes ``state`` or only ``nonce``.
"""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
import string
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Optional
from urllib.parse import urlencode

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

ALNUM = string.ascii_letters + string.digits
PKCE_ALNUM_LEN = 41
PKCE_TTL_SECONDS = 600
PKCE_KEY_PREFIX = "wr_pkce:"

# In-process fallback when Redis is unavailable (unit tests).
_pending_pkce: dict[str, dict[str, str]] = {}

# Wealth Reader error codes that mean "ask the user to log in again".
# Do not retry an invalid password in a tight loop.
AUTH_ERROR_CODES = {1010, 1020, 2010, 2020}

SUBTYPE_MAP = {
    "checking": "checking",
    "current": "checking",
    "savings": "savings",
    "credit_card": "credit_card",
    "card": "credit_card",
}


def _random_alnum(n: int) -> str:
    return "".join(secrets.choice(ALNUM) for _ in range(n))


def _hex_alnum(n: int) -> str:
    return _random_alnum(n).encode("ascii").hex()


def generate_pkce(entity_code: str) -> dict[str, str]:
    """Build the Wealth Reader challenge. Must match demo/oauth/index.php."""
    nonce = _hex_alnum(PKCE_ALNUM_LEN)
    wr_state = _hex_alnum(PKCE_ALNUM_LEN)
    code_verifier = _hex_alnum(PKCE_ALNUM_LEN)
    challenge = hashlib.sha256(code_verifier.encode("ascii")).hexdigest()
    wr_conf = {
        "operation_id": "op_" + secrets.token_hex(8),
        "entities_to_display": [entity_code] if entity_code else [],
        "wait_full_response": True,
    }
    return {
        "nonce": nonce,
        "wr_state": wr_state,
        "code_verifier": code_verifier,
        "challenge": challenge,
        "wr_conf": json.dumps(wr_conf, separators=(",", ":")).encode("utf-8").hex(),
    }


def _map_subtype(value: Optional[str]) -> str:
    if not value:
        return "checking"
    return SUBTYPE_MAP.get(value.lower(), "checking")


def _decimal(value: Any) -> Optional[Decimal]:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        return None


def _parse_iso_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _account_balance(raw: dict) -> Optional[Decimal]:
    balances = raw.get("balances") or {}
    if isinstance(balances, dict):
        for key in ("available", "current"):
            parsed = _decimal(balances.get(key))
            if parsed is not None:
                return parsed
    return None


async def _store_pkce(keys: list[str], payload: dict[str, str]) -> None:
    stored = False
    try:
        from app.core.redis import get_redis

        redis = await get_redis()
        blob = json.dumps(payload)
        for key in keys:
            await redis.set(f"{PKCE_KEY_PREFIX}{key}", blob, ex=PKCE_TTL_SECONDS)
        stored = True
    except Exception:
        logger.debug("Wealth Reader PKCE Redis store failed; using process memory")
    if not stored:
        for key in keys:
            _pending_pkce[key] = payload


async def _load_pkce(*keys: Optional[str]) -> Optional[dict[str, str]]:
    for key in keys:
        if not key:
            continue
        try:
            from app.core.redis import get_redis

            redis = await get_redis()
            raw = await redis.getdel(f"{PKCE_KEY_PREFIX}{key}")
            if raw:
                return json.loads(raw)
        except Exception:
            logger.debug("Wealth Reader PKCE Redis load failed; trying memory")
        if key in _pending_pkce:
            return _pending_pkce.pop(key)
    return None


class WealthreaderProvider(BankProvider):
    """Wealth Reader AIS connector."""

    @property
    def name(self) -> str:
        return "wealthreader"

    @property
    def flow_type(self) -> str:
        return "oauth"

    @property
    def redirect_uri(self) -> str:
        return (
            get_settings().wealthreader_oauth_redirect_uri
            or default_oauth_redirect_uri()
        )

    def _api_base(self) -> str:
        return get_settings().wealthreader_api_url.rstrip("/")

    def _oauth_base(self) -> str:
        return get_settings().wealthreader_oauth_url.rstrip("/")

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=60.0,
            headers={
                "Accept": "application/json",
                "User-Agent": "Securo/0.1 (+https://usesecuro.com)",
            },
        )

    async def _interpret(self, resp: httpx.Response, path: str) -> dict:
        if resp.status_code == 429:
            raise ProviderRateLimited(f"Wealth Reader {path} → 429")
        if resp.status_code in (401, 410):
            raise SessionExpiredError(f"Wealth Reader returned {resp.status_code} for {path}")
        if resp.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"Wealth Reader {path} → {resp.status_code}: {resp.text[:300]}",
                request=resp.request,
                response=resp,
            )
        data = resp.json()
        if data.get("success") is False:
            error = data.get("error") or {}
            code = int(error.get("code") or 0)
            message = error.get("message") or "success=false"
            if code in AUTH_ERROR_CODES:
                raise SessionExpiredError(f"Wealth Reader error {code}: {message}")
            raise RuntimeError(f"Wealth Reader error {code}: {message}")
        return data

    async def list_institutions(
        self, country: Optional[str] = None
    ) -> InstitutionListData:
        params: dict[str, str] = {}
        if country:
            params["country_code"] = country.upper()
        async with self._client() as client:
            resp = await client.get(f"{self._api_base()}/entities/", params=params or None)
            data = await self._interpret(resp, "GET /entities/")
        raw_list = data.get("payload") or data.get("entities") or []
        institutions: list[InstitutionData] = []
        countries: set[str] = set()
        for item in raw_list:
            if not isinstance(item, dict):
                continue
            code = item.get("code") or ""
            if not code:
                continue
            inst_country = (item.get("country_code") or item.get("country") or "").upper()
            if inst_country:
                countries.add(inst_country)
            institutions.append(
                InstitutionData(
                    name=code,
                    display_name=item.get("name") or code,
                    country=inst_country,
                    logo=item.get("logo"),
                )
            )
        institutions.sort(key=lambda i: (i.country, i.display_name.lower()))
        return InstitutionListData(countries=sorted(countries), institutions=institutions)

    async def get_oauth_url(
        self,
        redirect_uri: str,
        state: str,
        flow_params: Optional[dict] = None,
    ) -> str:
        flow_params = flow_params or {}
        entity_code = (
            flow_params.get("institution_name")
            or flow_params.get("code")
            or ""
        ).strip()
        if not entity_code:
            raise ValueError(
                "Wealth Reader requires flow_params with 'institution_name' "
                "(the institution code from GET /entities/)"
            )
        challenge = generate_pkce(entity_code)
        await _store_pkce(
            [state, challenge["nonce"]],
            {
                "code_verifier": challenge["code_verifier"],
                "nonce": challenge["nonce"],
                "entity_code": entity_code,
                "redirect_uri": redirect_uri,
            },
        )
        # Callback may echo only nonce. Alias Securo's oauth_state so
        # consume_state(nonce) still finds workspace/user/provider.
        try:
            from app.services import oauth_state

            await oauth_state.alias_state(state, challenge["nonce"])
        except Exception:
            logger.debug("Wealth Reader could not alias OAuth state to nonce")
        query = urlencode(
            {
                "challenge_code": challenge["challenge"],
                "code_challenge_method": "S256",
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "state": state,
                "nonce": challenge["nonce"],
                "wr_conf": challenge["wr_conf"],
            }
        )
        return f"{self._oauth_base()}/oauth2/?{query}"

    async def reauth_url(
        self,
        credentials: dict,
        settings: dict,
        redirect_uri: str,
        state: str,
    ) -> str:
        stored = (settings or {}).get("flow_params") or {}
        entity = stored.get("institution_name") or self._entity_code(credentials)
        if not entity:
            raise RuntimeError(
                "Cannot reauth Wealth Reader connection without stored institution code"
            )
        return await self.get_oauth_url(
            redirect_uri, state, flow_params={"institution_name": entity}
        )

    async def handle_oauth_callback(self, code: str, state: Optional[str] = None) -> ConnectionData:
        challenge = await _load_pkce(state)
        if not challenge:
            raise RuntimeError(
                "Wealth Reader PKCE challenge missing or expired. Restart the bank link."
            )
        form = {
            "grant_type": "authorization_code",
            "redirect_uri": challenge["redirect_uri"],
            "code": code,
            "code_verifier": challenge["code_verifier"],
        }
        async with self._client() as client:
            resp = await client.post(
                f"{self._oauth_base()}/token/",
                data=form,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            data = await self._interpret(resp, "POST /token/")
        return self._connection_from_response(data, challenge.get("entity_code") or "")

    def _connection_from_response(self, data: dict, fallback_code: str) -> ConnectionData:
        stats = data.get("statistics") or {}
        token = stats.get("token") or ""
        if not token:
            raise RuntimeError(
                "Wealth Reader token exchange returned no statistics.token — "
                "register the redirect URL with tokenize=1"
            )
        entity_code = stats.get("code") or fallback_code
        payload = data.get("payload") or {}
        accounts = [
            acc
            for raw in payload.get("accounts") or []
            if isinstance(raw, dict)
            for acc in [_account_data(raw)]
            if acc is not None
        ]
        if not accounts:
            raise SessionExpiredError("Wealth Reader returned no accounts")
        encrypted_token = encrypt(token) or token
        return ConnectionData(
            external_id=entity_code or token,
            institution_name=entity_code or "Wealth Reader",
            credentials={
                "token_enc": encrypted_token,
                "code": entity_code,
            },
            accounts=accounts,
        )

    def _token(self, credentials: dict) -> str:
        enc = credentials.get("token_enc")
        if enc:
            decoded = decrypt(enc)
            if decoded:
                return decoded
        return credentials.get("token") or ""

    def _entity_code(self, credentials: dict) -> str:
        return credentials.get("code") or ""

    async def _fetch(self, credentials: dict, since: Optional[date] = None) -> dict:
        token = self._token(credentials)
        code = self._entity_code(credentials)
        if not token or not code:
            raise SessionExpiredError("Wealth Reader token or institution code missing")
        form = {
            "api_key": get_settings().wealthreader_api_key,
            "code": code,
            "token": token,
            "product_types": "accounts",
        }
        if since:
            form["date_from"] = since.isoformat()
        async with self._client() as client:
            resp = await client.post(
                f"{self._api_base()}/entities/",
                data=form,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            return await self._interpret(resp, "POST /entities/")

    async def get_accounts(self, credentials: dict) -> list[AccountData]:
        data = await self._fetch(credentials)
        payload = data.get("payload") or {}
        return [
            acc
            for raw in payload.get("accounts") or []
            if isinstance(raw, dict)
            for acc in [_account_data(raw)]
            if acc is not None
        ]

    async def get_transactions(
        self,
        credentials: dict,
        account_external_id: str,
        since: Optional[date] = None,
        payee_source: str = "auto",
    ) -> list[TransactionData]:
        data = await self._fetch(credentials, since=since)
        payload = data.get("payload") or {}
        for raw in payload.get("accounts") or []:
            if not isinstance(raw, dict):
                continue
            if raw.get("uuid") != account_external_id and raw.get("code") != account_external_id:
                continue
            return [
                tx
                for item in raw.get("transactions") or []
                if isinstance(item, dict)
                for tx in [_transaction_data(item, raw.get("currency") or "EUR", payee_source)]
                if tx is not None
            ]
        return []

    async def refresh_credentials(self, credentials: dict) -> dict:
        # Token is long-lived until password/2FA change; a failed fetch raises
        # SessionExpiredError and the UI starts reauth_url.
        return credentials

    async def get_institution_logo(self, credentials: dict) -> Optional[str]:
        code = self._entity_code(credentials)
        if not code:
            return None
        institutions = await self.list_institutions()
        for inst in institutions.institutions:
            if inst.name == code:
                return inst.logo
        return None


def _account_data(raw: dict) -> Optional[AccountData]:
    balance = _account_balance(raw)
    if balance is None:
        logger.warning("Skipping Wealth Reader account without a numeric balance: %s", raw.get("uuid"))
        return None
    external_id = raw.get("uuid") or raw.get("code") or ""
    if not external_id:
        logger.warning("Skipping Wealth Reader account without uuid or code")
        return None
    return AccountData(
        external_id=external_id,
        name=raw.get("name") or "Account",
        type=_map_subtype(raw.get("subtype")),
        balance=balance,
        currency=raw.get("currency") or "EUR",
        masked_number=mask_last4(raw.get("code")),
    )


def _transaction_data(raw: dict, fallback_currency: str, payee_source: str) -> Optional[TransactionData]:
    amount = _decimal(raw.get("amount"))
    if amount is None:
        logger.warning("Skipping Wealth Reader transaction without a numeric amount: %s", raw.get("uuid"))
        return None
    txn_type = "credit" if amount >= 0 else "debit"
    description = (raw.get("description") or "").strip()
    transfer = raw.get("transfer_details") or {}
    counterpart = (transfer.get("sender_receiver") or "").strip() if isinstance(transfer, dict) else ""
    payee = None
    if payee_source != "none":
        payee = description or counterpart or None
    booked = _parse_iso_date(raw.get("operation_date")) or _parse_iso_date(raw.get("value_date"))
    if booked is None:
        booked = date.today()
    return TransactionData(
        external_id=raw.get("uuid") or "",
        description=description or counterpart or (raw.get("uuid") or ""),
        amount=amount.copy_abs(),
        date=booked,
        type=txn_type,
        currency=raw.get("currency") or fallback_currency,
        payee=payee,
        raw_data=raw,
    )
