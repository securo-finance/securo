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
    default_oauth_redirect_uri,
    mask_last4,
)
from app.providers.psu import current_psu_context

logger = logging.getLogger(__name__)

JWT_ISSUER = "enablebanking.com"
JWT_AUDIENCE = "api.enablebanking.com"
JWT_LIFETIME_SECONDS = 3500  # under EB's 1h cap; refresh well before
JWT_CACHE_REFRESH_BEFORE = 600  # re-mint with 10 min buffer

MAX_VALID_UNTIL_DAYS = 179
DEFAULT_VALID_UNTIL_DAYS = MAX_VALID_UNTIL_DAYS
DEFAULT_PSU_TYPE = "personal"
# Days of history requested on a fresh fetch, counted inclusively (today is
# day 1). Many banks cap the window at exactly 90 days.
DEFAULT_HISTORY_DAYS = 90
# Shorter window retried when a bank rejects the default one as out of bounds.
FALLBACK_HISTORY_DAYS = 30
TRANSACTION_PAGE_LIMIT = 50  # safety cap
# Session states after which no data can be read until the user re-authorizes.
TERMINAL_SESSION_STATUSES = frozenset(
    {"EXPIRED", "REVOKED", "CLOSED", "CANCELLED", "INVALID"}
)


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


def _account_identifier(raw: dict) -> Optional[str]:
    """Pull the bank's own identifier for an account out of an EB details payload.

    EB reports it in three places, in descending order of usefulness: the IBAN
    on `account_id`, a non-IBAN scheme (BBAN, sort-code-and-account) on
    `account_id.other`, and the `all_account_ids` list. Banks outside SEPA only
    populate the latter two, so we fall through rather than assuming an IBAN.
    """
    account_id = raw.get("account_id") or {}
    if isinstance(account_id, dict):
        iban = account_id.get("iban")
        if iban:
            return str(iban)
        other = account_id.get("other") or {}
        if isinstance(other, dict) and other.get("identification"):
            return str(other["identification"])
    for entry in raw.get("all_account_ids") or []:
        if isinstance(entry, dict) and entry.get("identification"):
            return str(entry["identification"])
    return None


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


def _parse_iso_date(value: Any) -> Optional[date]:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except (ValueError, TypeError):
        return None


def _join_remittance(value: Any) -> str:
    if isinstance(value, list):
        return " ".join(str(v) for v in value if v).strip()
    return (value or "").strip() if isinstance(value, str) else ""


def _counterparty_name(raw: dict, party: str) -> str:
    party_obj = raw.get(party)
    name = party_obj.get("name") if isinstance(party_obj, dict) else None
    if not isinstance(name, str) or not name.strip():
        name = raw.get(f"{party}_name")
    return name.strip() if isinstance(name, str) else ""


def _counterparty_identity(raw: dict, party: str) -> str:
    """IBAN of the counterparty account, falling back to its name."""
    account = raw.get(f"{party}_account")
    iban = (
        account.get("iban")
        if isinstance(account, dict) and isinstance(account.get("iban"), str)
        else ""
    )
    return iban.strip() or _counterparty_name(raw, party)


def _txn_fingerprint(account_uid: str, raw: dict) -> str:
    """Stable id for a booked transaction.

    EB's `entry_reference` is unreliable across providers, so we hash the
    fields most likely to persist across re-fetches of the same booked txn.
    Pending → booked transitions deliberately produce a different id; the
    sync layer's pending↔posted twin matcher handles that.
    """
    amount = raw.get("transaction_amount") or {}
    creditor_identity = _counterparty_identity(raw, "creditor")
    debtor_identity = _counterparty_identity(raw, "debtor")
    parts = [
        str(account_uid),
        str(raw.get("booking_date") or ""),
        str(raw.get("value_date") or ""),
        str(raw.get("transaction_date") or ""),
        str(amount.get("amount") or "") if isinstance(amount, dict) else "",
        str(amount.get("currency") or "") if isinstance(amount, dict) else "",
        str(raw.get("credit_debit_indicator") or ""),
        _join_remittance(raw.get("remittance_information"))[:80],
        creditor_identity,
        debtor_identity,
    ]
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return digest[:32]


def _canonical_amount(amount_obj: Any) -> str:
    """Canonical string for an EB amount.

    "12", "12.0" and "12.00" are the same amount, so parse it as a Decimal
    to give every spelling the same external id.
    """
    if not isinstance(amount_obj, dict):
        return ""
    raw = amount_obj.get("amount")
    if raw is None or raw == "":
        return ""
    try:
        return format(Decimal(str(raw)).normalize(), "f")
    except InvalidOperation:
        return str(raw)


def _entry_reference_external_id(entry_ref: str, amount_obj: dict, raw: dict) -> str:
    """Disambiguate a non-empty ``entry_reference`` with transaction details.

    EB's `entry_reference` is not guaranteed unique per transaction: some
    institutions reuse it across a batch/settlement (multiple ledger lines
    clearing under one shared reference) or across follow-on charges against
    the same card authorization (see #753). Folding in the amount, the
    available date fields, the direction, the remittance text and the
    counterparties keeps a truly-unique reference stable across re-fetches
    while separating genuinely distinct transactions that happen to share
    one (e.g. a charge and its refund, or settlement lines for different
    counterparties with equal amounts).
    """
    parts = [
        entry_ref,
        _canonical_amount(amount_obj),
        str(amount_obj.get("currency") or "") if isinstance(amount_obj, dict) else "",
        str(raw.get("booking_date") or ""),
        str(raw.get("value_date") or ""),
        str(raw.get("transaction_date") or ""),
        str(raw.get("credit_debit_indicator") or "").upper(),
        _join_remittance(raw.get("remittance_information"))[:80],
        _counterparty_identity(raw, "creditor"),
        _counterparty_identity(raw, "debtor"),
    ]
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return digest[:32]


def _settlement_key(raw: dict) -> Optional[tuple[str, str, str, str]]:
    """Identity of one operation across its pending and booked spellings.

    A pending entry and the booked entry it settles into keep the reference,
    the amount, the currency and the direction; the booking date is what the
    settlement adds. Returns None when the reference is absent or is the "0"
    placeholder, which carries no identity of its own.
    """
    entry_ref = raw.get("entry_reference")
    entry_ref = entry_ref.strip() if isinstance(entry_ref, str) else ""
    if not entry_ref or entry_ref == "0":
        return None
    amount_obj = raw.get("transaction_amount")
    if not isinstance(amount_obj, dict):
        return None
    return (
        entry_ref,
        _canonical_amount(amount_obj),
        str(amount_obj.get("currency") or ""),
        str(raw.get("credit_debit_indicator") or "").upper(),
    )


def _extract_payee(raw: dict, indicator: str, source: str) -> Optional[str]:
    """Pick a payee name from EB transaction payload.

    DBIT (money out): creditor (the recipient).
    CRDT (money in): debtor (the sender).
    """
    if source == "none":
        return None
    creditor = _counterparty_name(raw, "creditor")
    debtor = _counterparty_name(raw, "debtor")
    if source == "description":
        return None  # let description carry the info
    if indicator == "DBIT":
        return creditor or debtor
    return debtor or creditor


def _history_start(today: date, days: int) -> date:
    """First day of a ``days``-long window ending today, both ends inclusive."""
    return today - timedelta(days=days - 1)


# PSU headers sent on every user-present data request when the user's request
# supplied them. Enable Banking's FAQ asks for the user's headers whenever the
# user triggered the fetch, and for none when the application fetches on its
# own schedule:
# https://enablebanking.com/docs/faq/#what-are-the-psu-headers-in-the-endpoints-for-fetching-account-details-balances-and-transactions-used-for
DEFAULT_PSU_HEADERS = ("Psu-Ip-Address", "Psu-User-Agent")


# Key in the connection credentials holding what /details said about each
# account, by uid: what a background sync needs to name an account without
# calling /details, which some banks only answer with the user present.
ACCOUNT_DETAILS_KEY = "account_details"


def _details_summary(raw: dict) -> dict[str, Optional[str]]:
    """The part of an account's details payload the sync uses.

    The masked number is kept rather than the identifier itself, so the cache
    holds nothing the account row does not already show.
    """
    name = raw.get("display_name") or raw.get("product") or raw.get("name")
    return {
        "name": name if isinstance(name, str) else None,
        "cash_account_type": raw.get("cash_account_type"),
        "currency": raw.get("currency"),
        "masked_number": mask_last4(_account_identifier(raw)),
    }


def _is_data_request(path: str) -> bool:
    """Endpoints EB forwards to the bank and that accept PSU headers."""
    return path.startswith("/accounts/")


def _select_psu_headers(
    available: dict[str, str], required: Optional[list[str]]
) -> dict[str, str]:
    """PSU headers to send with one data request.

    Psu-Ip-Address and Psu-User-Agent go out whenever the user's request had
    them. An ASPSP's `required_psu_headers` must be sent all together or not
    at all (EB answers anything in between with PSU_HEADER_NOT_PROVIDED), so
    when one of them is missing nothing is sent and the request is a plain
    background one.
    """
    by_name = {name.lower(): (name, value) for name, value in available.items()}
    selected = {name: available[name] for name in DEFAULT_PSU_HEADERS if name in available}
    for wanted in required or []:
        match = by_name.get(wanted.lower())
        if match is None:
            return {}
        selected[match[0]] = match[1]
    return selected


def _error_body(resp: httpx.Response) -> dict:
    try:
        body = resp.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


# EB's wording for an ASPSP_ERROR it has no further reason for.
_GENERIC_ASPSP_ERROR_MESSAGE = "error interacting with aspsp"


def _error_detail(body: dict) -> str:
    """The bank's own reason, when EB relays one in `detail`."""
    detail = body.get("detail")
    if isinstance(detail, dict):
        detail = detail.get("message")
    return detail.strip() if isinstance(detail, str) else ""


def _needs_present_user(resp: httpx.Response, requires_psu_headers: bool) -> bool:
    """True when a header-less data request failed the way a bank that wants
    the user present refuses one.

    Banks answer a background call they do not allow (an endpoint outside
    their background allowance, or one past its daily quota) with an error
    that names the missing PSU header, which counts whatever /aspsps said.
    They can also answer with EB's generic ASPSP_ERROR, which carries no
    reason at all; that only counts for an ASPSP that declares
    `required_psu_headers`. An ASPSP_ERROR that gives any other reason, in
    `message` or `detail`, is not a presence refusal.
    """
    if resp.status_code >= 500:
        return False
    body = _error_body(resp)
    error = body.get("error")
    if error == "PSU_HEADER_NOT_PROVIDED":
        return True
    raw_message = body.get("message")
    message = raw_message if isinstance(raw_message, str) else ""
    detail = _error_detail(body)
    text = f"{message} {detail}".lower()
    if "psu" in text and "header" in text:
        return True
    if resp.status_code == 400 and error == "ASPSP_ERROR":
        generic = message.strip().lower() in ("", _GENERIC_ASPSP_ERROR_MESSAGE)
        return requires_psu_headers and generic and not detail
    return False


def _is_wrong_period_error(resp: httpx.Response) -> bool:
    """True when the bank rejected the requested date range as out of bounds."""
    if resp.status_code != 422:
        return False
    try:
        body = resp.json()
    except ValueError:
        return False
    return isinstance(body, dict) and body.get("error") == "WRONG_TRANSACTIONS_PERIOD"


class EnableBankingProvider(BankProvider):
    """Enable Banking PSD2 connector."""

    # Cache the JWT across requests to amortize the RS256 signing cost.
    _cached_token: Optional[str] = None
    _cached_token_exp: float = 0.0
    _cached_private_key: Optional[str] = None

    def __init__(self) -> None:
        # The connected ASPSP's `required_psu_headers`, from the credentials
        # of the connection being synced. None while unknown.
        self._required_psu_headers: Optional[list[str]] = None
        self._institution_name = "The bank"
        # Accounts whose transactions this sync fetched only in part; the
        # sync then leaves last_sync_at alone so the rest is asked for again.
        self.incomplete_account_ids: set[str] = set()
        # External ids of accounts whose transaction pages stopped before the
        # last one (a repeated cursor or the page cap). What was read is still
        # imported, but it is not the whole feed, so the sync layer must not
        # take an entry missing from it as one the bank stopped listing.
        self.truncated_account_ids: set[str] = set()

    def _use_credentials(self, credentials: dict) -> None:
        aspsp = credentials.get("aspsp") if isinstance(credentials, dict) else None
        if not isinstance(aspsp, dict):
            return
        required = aspsp.get("required_psu_headers")
        if isinstance(required, list):
            self._required_psu_headers = [h for h in required if isinstance(h, str)]
        if isinstance(aspsp.get("name"), str) and aspsp["name"]:
            self._institution_name = aspsp["name"]

    @property
    def name(self) -> str:
        return "enable_banking"

    @property
    def flow_type(self) -> str:
        return "oauth"

    @property
    def redirect_uri(self) -> str:
        return (
            get_settings().enable_banking_oauth_redirect_uri
            or default_oauth_redirect_uri()
        )

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
        raw = settings.enable_banking_private_key.get_secret_value() or ""
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

    def _present_psu_headers(self) -> dict[str, str]:
        """The PSU headers a data request sends now; empty when it goes out
        as a background request, which is how the bank will treat it."""
        psu = current_psu_context()
        if psu is None:
            return {}
        return _select_psu_headers(psu.headers, self._required_psu_headers)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict] = None,
        json_body: Optional[dict] = None,
    ) -> dict:
        psu_headers: dict[str, str] = {}
        if _is_data_request(path):
            psu_headers = self._present_psu_headers()
            if current_psu_context() is not None:
                if not psu_headers:
                    logger.warning(
                        "Enable Banking %s %s: the user's request lacks a PSU header "
                        "the bank requires (%s); sending none",
                        method,
                        path,
                        ", ".join(self._required_psu_headers or []),
                    )
        async with self._client() as client:
            resp = await client.request(
                method, path, params=params, json=json_body, headers=psu_headers or None
            )
        if resp.status_code in (401, 410):
            raise SessionExpiredError(
                f"Enable Banking returned {resp.status_code} for {path}"
            )
        if resp.status_code == 429:
            # The bank (ASPSP) is throttling us — transient, not a broken
            # connection. Surface a distinct type so sync can skip-and-retry
            # instead of erroring the connection. This holds whatever the body
            # says: a bank whose daily background allowance has run out can
            # answer 429 naming the PSU header it wanted, and that is still a
            # limit that resets on its own, not a refusal.
            raise ProviderRateLimited(
                f"Enable Banking {method} {path} → 429: {resp.text[:200]}"
            )
        if (
            resp.status_code >= 400
            and _is_data_request(path)
            and not psu_headers
            and _needs_present_user(resp, bool(self._required_psu_headers))
        ):
            # Never answered by inventing headers: the bank wants the user,
            # so the user is asked to sync while present, which sends theirs.
            logger.warning(
                "Enable Banking %s %s → %s without PSU headers: %s",
                method,
                path,
                resp.status_code,
                resp.text[:300],
            )
            if current_psu_context() is not None:
                # The user did sync, but their request had no public IP to
                # pass on. Only the operator can fix that, so the hint is for
                # the log, not for the member reading the error.
                logger.warning(
                    "A user-triggered Enable Banking sync reached %s without the "
                    "user's PSU headers; check that TRUSTED_PROXY_HOPS matches the "
                    "reverse proxies in front of the backend",
                    self._institution_name,
                )
            raise ProviderUserActionRequired(
                f"{self._institution_name} only answers this request while you are "
                "present. Sync this connection from Securo to refresh it.",
                code="psu_present_required",
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
            if (maximum_consent_validity := item.get("maximum_consent_validity", None)) is not None:
                maximum_consent_validity = timedelta(seconds=maximum_consent_validity).days

            institutions.append(
                InstitutionData(
                    name=item.get("name") or "",
                    display_name=item.get("name") or "",
                    country=inst_country,
                    logo=item.get("logo"),
                    bic=item.get("bic"),
                    psu_types=list(item.get("psu_types") or []),
                    max_consent_days=maximum_consent_validity,
                )
            )
        institutions.sort(key=lambda i: (i.country, i.display_name.lower()))
        return InstitutionListData(
            countries=sorted(countries),
            institutions=institutions,
        )

    async def _lookup_required_psu_headers(
        self, name: Optional[str], country: Optional[str]
    ) -> Optional[list[str]]:
        """The ASPSP's `required_psu_headers` from GET /aspsps, or None if unknown.

        /aspsps is answered by Enable Banking itself, so this never spends the
        bank's request allowance. An ASPSP without the field requires none.
        """
        if not name:
            return None
        params = {"country": country.upper()} if country else None
        try:
            data = await self._request("GET", "/aspsps", params=params)
        except Exception as exc:
            logger.warning("Could not look up PSU headers required by %s: %s", name, exc)
            return None
        aspsps = data.get("aspsps")
        for item in aspsps if isinstance(aspsps, list) else []:
            if isinstance(item, dict) and item.get("name") == name:
                required = item.get("required_psu_headers")
                if required is None:
                    return []
                if not isinstance(required, list):
                    # Not the documented list of header names; guessing what
                    # it means could suppress headers the bank needs.
                    logger.warning(
                        "Ignoring malformed required_psu_headers for %s: %r", name, required
                    )
                    return None
                return [h for h in required if isinstance(h, str)]
        return None

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
        valid_until_days = min(valid_until_days, MAX_VALID_UNTIL_DAYS)
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
        stored_aspsp: dict[str, Any] = {
            "name": institution_name,
            "country": aspsp.get("country"),
        }
        required_psu_headers = await self._lookup_required_psu_headers(
            aspsp.get("name"), aspsp.get("country")
        )
        if required_psu_headers is not None:
            stored_aspsp["required_psu_headers"] = required_psu_headers
        self._use_credentials({"aspsp": stored_aspsp})

        accounts: list[AccountData] = []
        account_details: dict[str, dict] = {}
        for raw_acc in accounts_raw:
            if isinstance(raw_acc, str):
                continue  # restricted clients can return account IDs as strings
            # A balance refused for want of the user's headers (no public IP
            # behind a misconfigured proxy) must not cost the new session: the
            # authorization code is single-use, so failing here would make a
            # reconnect start over at the bank. The next sync reads the balance.
            accounts.append(
                await self._build_account(raw_acc, tolerate_presence_refusal=True)
            )
            uid = raw_acc.get("uid") or raw_acc.get("account_uid")
            if uid:
                account_details[uid] = _details_summary(raw_acc)

        encrypted_session = encrypt(session_id) or session_id
        credentials: dict[str, Any] = {
            "session_id_enc": encrypted_session,
            "valid_until": valid_until,
            "aspsp": stored_aspsp,
            ACCOUNT_DETAILS_KEY: account_details,
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

    async def _build_account(
        self,
        raw: dict,
        stable_external_id: Optional[str] = None,
        *,
        tolerate_presence_refusal: bool = False,
        require_balance: bool = False,
    ) -> AccountData:
        """An account from its details; a failed balance reads as zero unless
        `require_balance`, when the failure is raised to the caller instead."""
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
            if require_balance:
                raise
            logger.warning(
                "Failed to fetch balances for account %s: %s", uid, exc
            )
        except ProviderUserActionRequired as exc:
            if not tolerate_presence_refusal or exc.code != "psu_present_required":
                raise
            logger.warning(
                "Balances of account %s need the user's PSU headers: %s", uid, exc
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
            masked_number=mask_last4(_account_identifier(raw)),
            # From the session payload: `uid` is scoped to one session, this
            # hash is what lets a later session find the same account again.
            stable_external_id=stable_external_id,
        )

    async def _build_cached_account(
        self, uid: str, cached: dict, stable_external_id: Optional[str]
    ) -> AccountData:
        """An account named from cached details; only its balance is fetched."""
        account = await self._build_account(
            {
                "uid": uid,
                "currency": cached.get("currency"),
                "display_name": cached.get("name"),
                "cash_account_type": cached.get("cash_account_type"),
            },
            stable_external_id,
            require_balance=True,
        )
        masked = cached.get("masked_number")
        account.masked_number = masked if isinstance(masked, str) else None
        return account

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
        self._use_credentials(credentials)
        session_id = self._session_id(credentials)
        if not session_id:
            raise SessionExpiredError("Enable Banking session_id missing")
        data = await self._request("GET", f"/sessions/{session_id}")
        # The bank can end consent well before `valid_until`; EB still answers
        # 200 here but flags the session, and every account call then fails.
        # Raise so the connection is marked expired instead of "syncing" zero
        # accounts forever.
        status = (data.get("status") or "").upper()
        if status in TERMINAL_SESSION_STATUSES:
            raise SessionExpiredError(f"Enable Banking session is {status}")
        uids = self._account_uids(data)
        # EB keys each account by a uid scoped to this session; the
        # identification_hash is the id that survives a reauthorisation. It comes
        # in the same payload we just fetched, so carry it through; otherwise a
        # reconnect re-keys every account and the sync duplicates them all.
        stable_external_ids = {
            entry.get("uid"): entry.get("identification_hash")
            for entry in (data.get("accounts_data") or [])
            if isinstance(entry, dict) and entry.get("uid")
        }
        # A background sync names known accounts from the cached details:
        # banks cap unattended calls per endpoint (often 4 a day), and some do
        # not allow /details without the user at all. A user-present sync
        # reads them fresh, which also refreshes the cache.
        cached_details = credentials.get(ACCOUNT_DETAILS_KEY)
        if not isinstance(cached_details, dict):
            cached_details = {}
            credentials[ACCOUNT_DETAILS_KEY] = cached_details
        # Present means the requests carry the user's headers: a user-present
        # sync that cannot send them reaches the bank as a background one.
        user_present = bool(self._present_psu_headers())
        result: list[AccountData] = []
        transient_error: Optional[httpx.HTTPError] = None
        presence_refusal: Optional[ProviderUserActionRequired] = None
        for uid in uids:
            cached = cached_details.get(uid)
            try:
                if not user_present and isinstance(cached, dict) and cached.get("name"):
                    # The balance is then the only call for this account, so
                    # its failure is handled like a failed /details below
                    # rather than syncing a zero balance.
                    result.append(
                        await self._build_cached_account(
                            uid, cached, stable_external_ids.get(uid)
                        )
                    )
                    continue
                details = await self._request("GET", f"/accounts/{uid}/details")
                cached_details[uid] = _details_summary(details)
                result.append(
                    await self._build_account(details, stable_external_ids.get(uid))
                )
            except ProviderUserActionRequired as exc:
                # A bank refusing one account without the user present says
                # nothing about the others, so this account is skipped for the
                # run like a refused /details, and the rest still sync.
                if exc.code != "psu_present_required":
                    raise
                logger.warning("Account %s needs the user present: %s", uid, exc)
                presence_refusal = presence_refusal or exc
                continue
            except httpx.HTTPError as exc:
                # A *transient* per-account failure. Without details we can't
                # safely name/type the account, and a bare-uid AccountData would
                # overwrite the stored name with a placeholder. Skip this account
                # for this run (non-destructive: the existing row and its
                # transactions are left intact and the next sync retries) rather
                # than corrupt it. SessionExpiredError is deliberately NOT caught
                # here: a dead consent is global, never per-account.
                logger.warning("Failed to fetch account %s: %s", uid, exc)
                is_refusal = (
                    isinstance(exc, httpx.HTTPStatusError)
                    and exc.response.status_code < 500
                    and exc.response.status_code != 408
                )
                if not is_refusal:
                    transient_error = exc
                continue
        # Skipping is only safe while something still syncs. When the bank refuses
        # every account, an empty list would pass for a successful sync: the
        # connection stays "active", last_sync_at keeps moving and the balances
        # silently go stale. The next sync that reaches an account clears this.
        if uids and not result:
            # A 5xx, timeout or network failure says nothing about the accounts,
            # so it surfaces as the original error, like any other provider
            # failure, rather than as accounts_unavailable.
            if transient_error is not None:
                raise transient_error
            # Every account wanted the user (or was otherwise refused): say
            # what the user can do about it rather than the generic refusal.
            if presence_refusal is not None:
                raise presence_refusal
            raise ProviderUserActionRequired(
                f"The bank refused all {len(uids)} account(s) on this connection. "
                "Retry later, or reconnect if it keeps failing.",
                code="accounts_unavailable",
            )
        return result

    async def get_transactions(
        self,
        credentials: dict,
        account_external_id: str,
        since: Optional[date] = None,
        payee_source: str = "auto",
    ) -> list[TransactionData]:
        self._use_credentials(credentials)
        _ = self._session_id(credentials)  # surface expired credentials early
        # Enable Banking reads date_to as an inclusive UTC calendar day, so the
        # window ends on the UTC date regardless of the application calendar;
        # otherwise an application west of UTC would stop short of the day's
        # newest transactions until its own date caught up.
        today = datetime.now(timezone.utc).date()
        if since is not None:
            return await self._fetch_transactions_since(
                account_external_id, since, today, payee_source
            )
        try:
            return await self._fetch_transactions(
                account_external_id,
                _history_start(today, DEFAULT_HISTORY_DAYS),
                today,
                payee_source,
            )
        except httpx.HTTPStatusError as exc:
            if not _is_wrong_period_error(exc.response):
                raise
            logger.warning(
                "Enable Banking rejected a %d-day history window for account %s; "
                "retrying with %d days",
                DEFAULT_HISTORY_DAYS,
                account_external_id,
                FALLBACK_HISTORY_DAYS,
            )
            return await self._fetch_transactions(
                account_external_id,
                _history_start(today, FALLBACK_HISTORY_DAYS),
                today,
                payee_source,
            )

    async def _fetch_transactions_since(
        self,
        account_external_id: str,
        since: date,
        today: date,
        payee_source: str,
    ) -> list[TransactionData]:
        """The window from `since`, cut to 90 days only when the bank refuses it.

        A connection that has not synced for a while asks for more than 90
        days (the sync rewinds from last_sync_at). Many banks serve that, but
        PSD2 banks may refuse anything older than 90 days without the user
        present, either as WRONG_TRANSACTIONS_PERIOD or the way they refuse
        any background call they do not allow. Only then is the request
        repeated with the last 90 days.

        A cut made on a background request leaves the account marked
        incomplete, so the sync keeps last_sync_at where it was and a later
        sync, one with the user present, asks for the full window again. A
        cut the bank imposes with the user's headers is final: only fresh
        SCA could unlock older data, and a reconnect starts from 90 days
        anyway, so holding the cursor back would only repeat the refusal.
        """
        oldest_background = _history_start(today, DEFAULT_HISTORY_DAYS)
        # Whether the request carries the user's headers decides whether a
        # cut range can still be fetched later.
        refused_in_background = not self._present_psu_headers()
        try:
            return await self._fetch_transactions(
                account_external_id, since, today, payee_source
            )
        except (httpx.HTTPStatusError, ProviderUserActionRequired) as exc:
            if since >= oldest_background:
                raise
            if isinstance(exc, httpx.HTTPStatusError):
                if not _is_wrong_period_error(exc.response):
                    raise
            elif exc.code != "psu_present_required":
                raise
        transactions = await self._fetch_transactions(
            account_external_id, oldest_background, today, payee_source
        )
        if refused_in_background:
            self.incomplete_account_ids.add(account_external_id)
            logger.warning(
                "Enable Banking refused transactions of account %s from %s without "
                "the user present; fetched from %s and kept the earlier range for a "
                "sync with the user present",
                account_external_id,
                since.isoformat(),
                oldest_background.isoformat(),
            )
        else:
            logger.warning(
                "Enable Banking refused transactions of account %s from %s with the "
                "user present; fetched from %s, the earlier range needs a new consent",
                account_external_id,
                since.isoformat(),
                oldest_background.isoformat(),
            )
        return transactions

    async def _fetch_transactions(
        self,
        account_external_id: str,
        start: date,
        end: date,
        payee_source: str,
    ) -> list[TransactionData]:
        date_from = start.isoformat()
        date_to = end.isoformat()
        collected: list[tuple[TransactionData, Optional[tuple[str, str, str, str]]]] = []
        booked_keys: dict[tuple[str, str, str, str], int] = {}
        pending_keys: dict[tuple[str, str, str, str], int] = {}
        continuation_key: Optional[str] = None
        seen_continuation_keys: set[str] = set()
        seen_transaction_ids: set[str] = set()
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
                # A broken pagination cursor can make Enable Banking return a
                # page we have already consumed. Keep the result idempotent
                # even before the repeated cursor is detected below.
                if not parsed or parsed.external_id in seen_transaction_ids:
                    continue
                key = _settlement_key(raw_txn)
                if key is not None:
                    counts = pending_keys if parsed.status == "pending" else booked_keys
                    counts[key] = counts.get(key, 0) + 1
                collected.append((parsed, key))
                seen_transaction_ids.add(parsed.external_id)

            next_continuation_key = page.get("continuation_key") or None
            if not next_continuation_key:
                break
            if next_continuation_key in seen_continuation_keys:
                logger.warning(
                    "Enable Banking pagination loop detected for account %s "
                    "(repeated continuation key); stopping pagination",
                    account_external_id,
                )
                self.truncated_account_ids.add(account_external_id)
                break
            seen_continuation_keys.add(next_continuation_key)
            continuation_key = next_continuation_key
        else:
            logger.warning(
                "Enable Banking returned more than %d transaction pages for "
                "account %s; stopping pagination",
                TRANSACTION_PAGE_LIMIT,
                account_external_id,
            )
            self.truncated_account_ids.add(account_external_id)
        # While an operation settles, an institution can list it as booked and
        # still as pending in the same response. The two spellings differ (the
        # booked one carries the booking date), so they hash to different ids:
        # both would land, and the pending row would never clear, because the
        # id it already holds is in this batch and the re-key skips a sibling
        # that is still returned. Booked is the truth, so its pending twin is
        # dropped here and the sync re-keys the row it already holds. Only an
        # unambiguous pair counts: a reference shared by several entries on
        # either side is #753's case, where each entry is its own transaction.
        return [
            parsed
            for parsed, key in collected
            if key is None
            or parsed.status != "pending"
            or booked_keys.get(key) != 1
            or pending_keys.get(key) != 1
        ]

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
        if not isinstance(amount_obj, dict):
            return None
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
        txn_date = booking or value or _parse_iso_date(raw.get("transaction_date"))
        if not txn_date:
            return None
        additional_value = raw.get("additional_information")
        additional = additional_value.strip() if isinstance(additional_value, str) else ""
        description = _join_remittance(raw.get("remittance_information")) or additional
        if not description:
            creditor = _counterparty_name(raw, "creditor")
            debtor = _counterparty_name(raw, "debtor")
            if indicator == "DBIT":
                description = creditor or debtor or ""
            else:
                description = debtor or creditor or ""
        description = description.strip()[:500] or "Transaction"
        entry_ref = (raw.get("entry_reference") or "").strip()
        external_id = (
            _entry_reference_external_id(entry_ref, amount_obj, raw)
            if entry_ref and entry_ref != "0"
            else _txn_fingerprint(account_uid, raw)
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
        # Connections linked before the required headers were stored learn
        # them here, once; the sync saves the returned credentials.
        aspsp = credentials.get("aspsp")
        if isinstance(aspsp, dict) and "required_psu_headers" not in aspsp:
            required = await self._lookup_required_psu_headers(
                aspsp.get("name"), aspsp.get("country")
            )
            if required is not None:
                return {**credentials, "aspsp": {**aspsp, "required_psu_headers": required}}
        return credentials
