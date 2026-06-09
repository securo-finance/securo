"""Enable Banking (PSD2) provider — European banks via open banking.

Auth model: RS256 JWT signed with the operator's application private key,
`kid` header set to the application ID. No client secret, no refresh token.
Sessions are valid for the consent window the user grants at their bank
(typically 90–180 days); after that, re-authorization is required.

The flow requires the user to pick a country and bank before the
authorization URL can be generated, so `get_oauth_url` takes
`flow_params={"country", "institution_name"}`.
"""
from __future__ import annotations

import hashlib
import logging
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Optional

import httpx
from jose import jwt

from app.agents.services.crypto import decrypt, encrypt
from app.core.config import get_settings
from app.providers.base import (
    AccountData,
    BankProvider,
    ConnectionData,
    InstitutionData,
    InstitutionListData,
    ProviderRateLimited,
    ProviderUserActionRequired,
    SessionExpiredError,
    TransactionData,
)

logger = logging.getLogger(__name__)

JWT_ISSUER = "enablebanking.com"
JWT_AUDIENCE = "api.enablebanking.com"
JWT_LIFETIME_SECONDS = 3500  # under EB's 1h cap; refresh well before
JWT_CACHE_REFRESH_BEFORE = 600  # re-mint with 10 min buffer

DEFAULT_VALID_UNTIL_DAYS = 180
DEFAULT_PSU_TYPE = "personal"
DEFAULT_HISTORY_DAYS = 90
TRANSACTION_PAGE_LIMIT = 50  # safety cap


def _map_cash_account_type(eb_type: Optional[str]) -> str:
    """Map EB cash_account_type (ISO 20022) to Securo internal type."""
    if not eb_type:
        return "checking"
    mapping = {
        "CACC": "checking",  # current
        "SVGS": "savings",
        "CARD": "credit_card",
        "CASH": "checking",
        "LOAN": "checking",  # we don't model loan accounts yet
        "OTHR": "checking",
    }
    return mapping.get(eb_type.upper(), "checking")


def _pick_balance(balances: list[dict]) -> Optional[dict]:
    """Pick the most useful balance from EB's list (prefer closing booked)."""
    if not balances:
        return None
    priority = ["CLBD", "ITAV", "CLAV", "XPCD", "OPBD", "OPAV"]
    by_type = {b.get("balance_type") or b.get("type"): b for b in balances if isinstance(b, dict)}
    for key in priority:
        if key in by_type:
            return by_type[key]
    return balances[0] if isinstance(balances[0], dict) else None


def _balance_decimal(balance: Optional[dict]) -> Decimal:
    if not balance:
        return Decimal("0")
    amount = balance.get("balance_amount") or balance.get("amount") or {}
    try:
        return Decimal(str(amount.get("amount", "0")))
    except (InvalidOperation, AttributeError):
        return Decimal("0")


def _balance_currency(balance: Optional[dict], fallback: str) -> str:
    if not balance:
        return fallback
    amount = balance.get("balance_amount") or balance.get("amount") or {}
    return amount.get("currency") or fallback


def _parse_iso_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _join_remittance(value: Any) -> str:
    if isinstance(value, list):
        return " ".join(str(v) for v in value if v).strip()
    return (value or "").strip() if isinstance(value, str) else ""


def _txn_fingerprint(account_uid: str, raw: dict) -> str:
    """Stable id for a booked transaction.

    EB's `entry_reference` is unreliable across providers, so we hash the
    fields most likely to persist across re-fetches of the same booked txn.
    Pending → booked transitions deliberately produce a different id; the
    sync layer's pending↔posted twin matcher handles that.
    """
    amount = raw.get("transaction_amount") or {}
    parts = [
        account_uid,
        raw.get("booking_date") or "",
        raw.get("value_date") or "",
        str(amount.get("amount") or ""),
        str(amount.get("currency") or ""),
        raw.get("credit_debit_indicator") or "",
        _join_remittance(raw.get("remittance_information"))[:80],
        ((raw.get("creditor_account") or {}).get("iban") or ""),
        ((raw.get("debtor_account") or {}).get("iban") or ""),
    ]
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return digest[:32]


def _extract_payee(raw: dict, indicator: str, source: str) -> Optional[str]:
    """Pick a payee name from EB transaction payload.

    DBIT (money out): creditor (the recipient).
    CRDT (money in): debtor (the sender).
    """
    if source == "none":
        return None
    creditor = (raw.get("creditor") or {}).get("name") or raw.get("creditor_name")
    debtor = (raw.get("debtor") or {}).get("name") or raw.get("debtor_name")
    if source == "description":
        return None  # let description carry the info
    if indicator == "DBIT":
        return creditor or debtor
    return debtor or creditor


class EnableBankingProvider(BankProvider):
    """Enable Banking PSD2 connector."""

    # Cache the JWT across requests to amortize the RS256 signing cost.
    _cached_token: Optional[str] = None
    _cached_token_exp: float = 0.0
    _cached_private_key: Optional[str] = None

    @property
    def name(self) -> str:
        return "enable_banking"

    @property
    def flow_type(self) -> str:
        return "oauth"

    @property
    def redirect_uri(self) -> str:
        return get_settings().enable_banking_oauth_redirect_uri

    # ----- credentials -----

    @classmethod
    def _load_private_key(cls) -> str:
        if cls._cached_private_key:
            return cls._cached_private_key
        settings = get_settings()
        key_file = (settings.enable_banking_private_key_file or "").strip()
        if key_file:
            cls._cached_private_key = Path(key_file).read_text(encoding="utf-8")
            return cls._cached_private_key
        raw = settings.enable_banking_private_key or ""
        if "\\n" in raw and "\n" not in raw:
            raw = raw.replace("\\n", "\n")
        cls._cached_private_key = raw
        return cls._cached_private_key

    @classmethod
    def _jwt_token(cls) -> str:
        now = time.time()
        if cls._cached_token and now < cls._cached_token_exp - JWT_CACHE_REFRESH_BEFORE:
            return cls._cached_token
        settings = get_settings()
        app_id = settings.enable_banking_app_id
        if not app_id:
            raise RuntimeError("ENABLE_BANKING_APP_ID is not configured")
        private_key = cls._load_private_key()
        if not private_key:
            raise RuntimeError("Enable Banking private key is not configured")
        issued_at = int(now)
        exp = issued_at + JWT_LIFETIME_SECONDS
        claims = {
            "iss": JWT_ISSUER,
            "aud": JWT_AUDIENCE,
            "iat": issued_at,
            "exp": exp,
        }
        token = jwt.encode(
            claims,
            private_key,
            algorithm="RS256",
            headers={"kid": app_id, "typ": "JWT"},
        )
        cls._cached_token = token
        cls._cached_token_exp = exp
        return token

    # ----- HTTP layer -----

    def _client(self) -> httpx.AsyncClient:
        settings = get_settings()
        return httpx.AsyncClient(
            base_url=settings.enable_banking_api_url.rstrip("/"),
            headers={
                "Authorization": f"Bearer {self._jwt_token()}",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "Securo/0.1 (+https://usesecuro.com)",
            },
            timeout=30.0,
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict] = None,
        json_body: Optional[dict] = None,
    ) -> dict:
        async with self._client() as client:
            resp = await client.request(method, path, params=params, json=json_body)
        if resp.status_code in (401, 410):
            raise SessionExpiredError(
                f"Enable Banking returned {resp.status_code} for {path}"
            )
        if resp.status_code == 429:
            # The bank (ASPSP) is throttling us — transient, not a broken
            # connection. Surface a distinct type so sync can skip-and-retry
            # instead of erroring the connection.
            raise ProviderRateLimited(
                f"Enable Banking {method} {path} → 429: {resp.text[:200]}"
            )
        if resp.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"Enable Banking {method} {path} → {resp.status_code}: {resp.text[:300]}",
                request=resp.request,
                response=resp,
            )
        return resp.json()

    # ----- institution listing -----

    async def list_institutions(
        self, country: Optional[str] = None
    ) -> InstitutionListData:
        params: dict[str, Any] = {}
        if country:
            params["country"] = country.upper()
        data = await self._request("GET", "/aspsps", params=params or None)
        raw_list = data.get("aspsps") or []
        institutions: list[InstitutionData] = []
        countries: set[str] = set()
        for item in raw_list:
            inst_country = (item.get("country") or "").upper()
            if inst_country:
                countries.add(inst_country)
            institutions.append(
                InstitutionData(
                    name=item.get("name") or "",
                    display_name=item.get("name") or "",
                    country=inst_country,
                    logo=item.get("logo"),
                    bic=item.get("bic"),
                    psu_types=list(item.get("psu_types") or []),
                    max_consent_days=item.get("maximum_consent_validity"),
                )
            )
        institutions.sort(key=lambda i: (i.country, i.display_name.lower()))
        return InstitutionListData(
            countries=sorted(countries),
            institutions=institutions,
        )

    # ----- OAuth (authorization) -----

    def _build_auth_payload(
        self,
        *,
        country: str,
        institution_name: str,
        redirect_uri: str,
        state: str,
        psu_type: str,
        valid_until_days: int,
    ) -> dict:
        valid_until_dt = datetime.now(timezone.utc) + timedelta(days=valid_until_days)
        # EB wants RFC3339 with a trailing 'Z' for UTC.
        valid_until = valid_until_dt.replace(microsecond=0).isoformat().replace(
            "+00:00", "Z"
        )
        return {
            "access": {"valid_until": valid_until},
            "aspsp": {"name": institution_name, "country": country.upper()},
            "state": state,
            "redirect_url": redirect_uri,
            "psu_type": psu_type,
        }

    async def get_oauth_url(
        self,
        redirect_uri: str,
        state: str,
        flow_params: Optional[dict] = None,
    ) -> str:
        flow_params = flow_params or {}
        country = (flow_params.get("country") or "").strip().upper()
        institution_name = (flow_params.get("institution_name") or "").strip()
        if not country or not institution_name:
            raise ValueError(
                "Enable Banking requires flow_params with 'country' and 'institution_name'"
            )
        psu_type = (flow_params.get("psu_type") or DEFAULT_PSU_TYPE).strip()
        valid_until_days = int(
            flow_params.get("valid_until_days") or DEFAULT_VALID_UNTIL_DAYS
        )
        payload = self._build_auth_payload(
            country=country,
            institution_name=institution_name,
            redirect_uri=redirect_uri,
            state=state,
            psu_type=psu_type,
            valid_until_days=valid_until_days,
        )
        data = await self._request("POST", "/auth", json_body=payload)
        url = data.get("url")
        if not url:
            raise RuntimeError("Enable Banking /auth did not return a redirect URL")
        return url

    async def reauth_url(
        self,
        credentials: dict,
        settings: dict,
        redirect_uri: str,
        state: str,
    ) -> str:
        stored = (settings or {}).get("flow_params") or {}
        if not stored.get("country") or not stored.get("institution_name"):
            raise RuntimeError(
                "Cannot reauth Enable Banking connection without stored flow_params"
            )
        return await self.get_oauth_url(redirect_uri, state, flow_params=stored)

    # ----- session exchange -----

    async def handle_oauth_callback(self, code: str) -> ConnectionData:
        data = await self._request("POST", "/sessions", json_body={"code": code})
        session_id = data.get("session_id")
        if not session_id:
            raise RuntimeError("Enable Banking /sessions response missing session_id")

        accounts_raw = data.get("accounts")
        if not accounts_raw:
            raise ProviderUserActionRequired(
                "Enable Banking returned no accounts. In restricted mode you must "
                "pre-link accounts in the Enable Banking portal before connecting.",
                code="no_accounts_linked",
                help_url="https://enablebanking.com/",
            )

        aspsp = data.get("aspsp") or {}
        institution_name = aspsp.get("name") or "Bank"
        access = data.get("access") or {}
        valid_until = access.get("valid_until")

        accounts: list[AccountData] = []
        for raw_acc in accounts_raw:
            if isinstance(raw_acc, str):
                continue  # restricted clients can return account IDs as strings
            accounts.append(await self._build_account(raw_acc))

        encrypted_session = encrypt(session_id) or session_id
        credentials: dict[str, Any] = {
            "session_id_enc": encrypted_session,
            "valid_until": valid_until,
            "aspsp": {
                "name": institution_name,
                "country": aspsp.get("country"),
            },
        }
        return ConnectionData(
            external_id=session_id,
            institution_name=institution_name,
            credentials=credentials,
            accounts=accounts,
            # The session payload sometimes carries the ASPSP logo; when it
            # doesn't, the sync layer backfills it via get_institution_logo.
            logo_url=aspsp.get("logo"),
        )

    async def get_institution_logo(self, credentials: dict) -> Optional[str]:
        aspsp = credentials.get("aspsp") or {}
        name = aspsp.get("name")
        country = aspsp.get("country")
        if not name:
            return None
        institutions = await self.list_institutions(country)
        for inst in institutions.institutions:
            if inst.name == name:
                return inst.logo
        return None

    async def _build_account(self, raw: dict) -> AccountData:
        uid = raw.get("uid") or raw.get("account_uid") or ""
        currency = raw.get("currency") or "EUR"
        # EB doesn't include balances in the session payload; fetch separately.
        balance = Decimal("0")
        try:
            bal_resp = await self._request("GET", f"/accounts/{uid}/balances")
            picked = _pick_balance(bal_resp.get("balances") or [])
            balance = _balance_decimal(picked)
            currency = _balance_currency(picked, currency)
        except (httpx.HTTPError, SessionExpiredError) as exc:
            logger.warning(
                "Failed to fetch balances for account %s: %s", uid, exc
            )
        name = (
            raw.get("display_name")
            or raw.get("product")
            or raw.get("name")
            or "Account"
        )
        return AccountData(
            external_id=uid,
            name=name,
            type=_map_cash_account_type(raw.get("cash_account_type")),
            balance=balance,
            currency=currency,
        )

    # ----- account / transaction fetches -----

    def _session_id(self, credentials: dict) -> str:
        enc = credentials.get("session_id_enc")
        if enc:
            decoded = decrypt(enc)
            if decoded:
                return decoded
        # Backward compat: plaintext during dev.
        return credentials.get("session_id") or ""

    @staticmethod
    def _account_uids(session_data: dict) -> list[str]:
        """Extract account uids from a GET /sessions payload.

        EB exposes the uids in two shapes and neither carries the account
        name/type: `accounts` is a list of uid *strings*, while
        `accounts_data` is a list of objects keyed by `uid` (plus identity
        hashes only). The human-readable name/type/currency live behind
        /accounts/{uid}/details, fetched per account below.
        """
        uids: list[str] = []
        for entry in session_data.get("accounts_data") or []:
            if isinstance(entry, dict):
                uid = entry.get("uid") or entry.get("account_uid")
                if uid:
                    uids.append(uid)
        if uids:
            return uids
        # Fallback: `accounts` is a plain list of uid strings.
        return [a for a in (session_data.get("accounts") or []) if isinstance(a, str)]

    async def get_accounts(self, credentials: dict) -> list[AccountData]:
        session_id = self._session_id(credentials)
        if not session_id:
            raise SessionExpiredError("Enable Banking session_id missing")
        data = await self._request("GET", f"/sessions/{session_id}")
        result: list[AccountData] = []
        for uid in self._account_uids(data):
            try:
                details = await self._request("GET", f"/accounts/{uid}/details")
            except (httpx.HTTPError, SessionExpiredError) as exc:
                # Without details we can't safely name/type the account, and a
                # bare-uid AccountData would overwrite the stored name with a
                # placeholder. Skip this account for this run (non-destructive:
                # the existing row and its transactions are left intact and the
                # next sync retries) rather than corrupt it.
                logger.warning("Failed to fetch details for account %s: %s", uid, exc)
                continue
            result.append(await self._build_account(details))
        return result

    async def get_transactions(
        self,
        credentials: dict,
        account_external_id: str,
        since: Optional[date] = None,
        payee_source: str = "auto",
    ) -> list[TransactionData]:
        _ = self._session_id(credentials)  # surface expired credentials early
        date_from = (since or (date.today() - timedelta(days=DEFAULT_HISTORY_DAYS))).isoformat()
        date_to = date.today().isoformat()
        transactions: list[TransactionData] = []
        continuation_key: Optional[str] = None
        for _ in range(TRANSACTION_PAGE_LIMIT):
            params: dict[str, Any] = {"date_from": date_from, "date_to": date_to}
            if continuation_key:
                params["continuation_key"] = continuation_key
            page = await self._request(
                "GET",
                f"/accounts/{account_external_id}/transactions",
                params=params,
            )
            for raw_txn, status in self._iter_transactions(page):
                parsed = self._build_transaction(
                    account_external_id, raw_txn, status, payee_source
                )
                if parsed:
                    transactions.append(parsed)
            continuation_key = page.get("continuation_key") or None
            if not continuation_key:
                break
        return transactions

    @staticmethod
    def _iter_transactions(page: dict):
        """Yield (raw_txn, status) tuples handling both nested and flat shapes."""
        body = page.get("transactions")
        if isinstance(body, dict):
            for raw in body.get("booked") or []:
                yield raw, "posted"
            for raw in body.get("pending") or []:
                yield raw, "pending"
        elif isinstance(body, list):
            for raw in body:
                raw_status = (raw.get("status") or "").upper()
                yield raw, "pending" if raw_status in {"PDNG", "PENDING"} else "posted"

    def _build_transaction(
        self,
        account_uid: str,
        raw: dict,
        status: str,
        payee_source: str,
    ) -> Optional[TransactionData]:
        amount_obj = raw.get("transaction_amount") or {}
        try:
            amount = Decimal(str(amount_obj.get("amount", "0")))
        except InvalidOperation:
            return None
        indicator = (raw.get("credit_debit_indicator") or "").upper()
        txn_type = "debit" if indicator == "DBIT" else "credit"
        amount = amount.copy_abs()
        currency = amount_obj.get("currency") or "EUR"
        booking = _parse_iso_date(raw.get("booking_date"))
        value = _parse_iso_date(raw.get("value_date"))
        txn_date = booking or value
        if not txn_date:
            return None
        description = _join_remittance(raw.get("remittance_information")) or (
            raw.get("additional_information") or ""
        )
        description = description.strip()[:500] or "Transaction"
        external_id = (raw.get("entry_reference") or "").strip() or _txn_fingerprint(
            account_uid, raw
        )
        return TransactionData(
            external_id=external_id,
            description=description,
            amount=amount,
            date=txn_date,
            type=txn_type,
            currency=currency,
            status=status,
            payee=_extract_payee(raw, indicator, payee_source),
            raw_data=raw,
        )

    # ----- credential lifecycle -----

    async def refresh_credentials(self, credentials: dict) -> dict:
        valid_until = credentials.get("valid_until")
        if valid_until:
            try:
                # Accept both RFC3339 with 'Z' and offset forms.
                normalized = valid_until.replace("Z", "+00:00")
                expires_at = datetime.fromisoformat(normalized)
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
                if expires_at <= datetime.now(timezone.utc):
                    raise SessionExpiredError(
                        "Enable Banking session expired; user must re-authorize"
                    )
            except ValueError:
                pass
        return credentials
