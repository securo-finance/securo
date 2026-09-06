"""GoCardless Bank Account Data provider for UK and European banks."""

from __future__ import annotations

import hashlib
import logging
import re
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

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
    ProviderUserActionRequired,
    SessionExpiredError,
    TransactionData,
    default_oauth_redirect_uri,
    mask_last4,
)

logger = logging.getLogger(__name__)

BASE_SCOPES = ["balances", "details", "transactions"]
DEFAULT_HISTORY_DAYS = 90
TOKEN_REFRESH_BEFORE = 600
REQUISITION_PAGE_LIMIT = 10
FALLBACK_ACCESS_VALID_FOR_DAYS = 90
FALLBACK_MAX_HISTORICAL_DAYS = 89

_BALANCE_PRIORITY = [
    "closingBooked",
    "expected",
    "forwardAvailable",
    "interimAvailable",
    "interimBooked",
    "nonInvoiced",
    "openingBooked",
]


def _map_cash_account_type(account_type: Optional[str]) -> str:
    if not account_type:
        return "checking"
    return {
        "CACC": "checking",
        "SVGS": "savings",
        "CARD": "credit_card",
    }.get(account_type.upper(), "checking")


def _parse_iso_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _parse_iso_datetime(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _join_remittance(value: Any) -> str:
    if isinstance(value, list):
        return " ".join(str(item) for item in value if item).strip()
    return (value or "").strip() if isinstance(value, str) else ""


def _txn_fingerprint(account_uid: str, raw: dict) -> str:
    amount = raw.get("transactionAmount") or {}
    parts = [
        account_uid,
        raw.get("_fingerprintBookingDate") or raw.get("bookingDate") or "",
        raw.get("valueDate") or "",
        str(amount.get("amount") or ""),
        str(amount.get("currency") or ""),
        _join_remittance(raw.get("remittanceInformationUnstructured"))[:80],
        _join_remittance(raw.get("remittanceInformationUnstructuredArray"))[:80],
        ((raw.get("creditorAccount") or {}).get("iban") or ""),
        ((raw.get("debtorAccount") or {}).get("iban") or ""),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]


def _extract_payee(raw: dict, txn_type: str, source: str) -> Optional[str]:
    if source in {"none", "description"}:
        return None
    creditor = raw.get("creditorName")
    debtor = raw.get("debtorName")
    if txn_type == "debit":
        return creditor or debtor
    return debtor or creditor


QuirkFn = Callable[[dict, str], Optional[dict]]


def _nationwide_quirks(raw: dict, status: str) -> Optional[dict]:
    if status == "pending" and (booking := _parse_iso_date(raw.get("bookingDate"))):
        clamped = min(booking, date.today())
        if clamped != booking:
            raw["_fingerprintBookingDate"] = raw["bookingDate"]
            raw["bookingDate"] = clamped.isoformat()

    transaction_id = raw.get("transactionId")
    if transaction_id and (
        re.match(r"^00(?:DEB|CRED)IT", str(transaction_id))
        or len(str(transaction_id)) not in {32, 40}
    ):
        raw.pop("transactionId", None)
    return raw


_BANK_QUIRKS: dict[str, QuirkFn] = {
    "NATIONWIDE_NAIAGB21": _nationwide_quirks,
}


class GoCardlessProvider(BankProvider):
    """GoCardless Bank Account Data connector."""

    _cached_token: Optional[str] = None
    _cached_token_exp: float = 0.0

    @property
    def name(self) -> str:
        return "gocardless"

    @property
    def flow_type(self) -> str:
        return "oauth"

    @property
    def redirect_uri(self) -> str:
        return get_settings().gocardless_oauth_redirect_uri or default_oauth_redirect_uri()

    @property
    def sync_stale_threshold(self) -> timedelta:
        return timedelta(hours=24)

    # ----- credentials -----

    def _client(self, token: Optional[str] = None) -> httpx.AsyncClient:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Securo/0.1 (+https://usesecuro.com)",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return httpx.AsyncClient(
            base_url=get_settings().gocardless_api_url.rstrip("/"),
            headers=headers,
            timeout=30.0,
        )

    async def _access_token(self) -> str:
        cls = type(self)
        now = time.time()
        if cls._cached_token and now < cls._cached_token_exp - TOKEN_REFRESH_BEFORE:
            return cls._cached_token

        settings = get_settings()
        payload = {
            "secret_id": settings.gocardless_secret_id,
            "secret_key": settings.gocardless_secret_key.get_secret_value(),
        }
        async with self._client() as client:
            response = await client.post("/token/new/", json=payload)
        if response.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"GoCardless POST /token/new/ → {response.status_code}: {response.text[:300]}",
                request=response.request,
                response=response,
            )
        body = response.json()
        access = body.get("access")
        if not access:
            raise RuntimeError("GoCardless /token/new/ response missing access token")
        cls._cached_token = access
        cls._cached_token_exp = now + float(body.get("access_expires") or 0)
        return access

    # ----- HTTP layer -----

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict] = None,
        json_body: Optional[dict] = None,
    ) -> Any:
        token = await self._access_token()
        async with self._client(token) as client:
            response = await client.request(method, path, params=params, json=json_body)
        text = response.text
        if response.status_code == 401 and re.search(
            r"end user agreement.*expired", text, re.IGNORECASE
        ):
            raise SessionExpiredError("GoCardless end user agreement expired")
        if response.status_code in {401, 410}:
            type(self)._cached_token = None
            type(self)._cached_token_exp = 0.0
            raise SessionExpiredError(f"GoCardless returned {response.status_code} for {path}")
        if response.status_code == 429:
            raise ProviderRateLimited(f"GoCardless {method} {path} → 429: {text[:200]}")
        if response.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"GoCardless {method} {path} → {response.status_code}: {text[:300]}",
                request=response.request,
                response=response,
            )
        return response.json()

    # ----- institution listing -----

    async def list_institutions(self, country: Optional[str] = None) -> InstitutionListData:
        params = {"country": country.lower()} if country else None
        raw_list = await self._request("GET", "/institutions/", params=params)
        institutions: list[InstitutionData] = []
        countries: set[str] = set()
        for item in raw_list:
            institution_id = item.get("id")
            display_name = item.get("name")
            if not institution_id or not display_name:
                logger.warning("Skipping GoCardless institution with missing id/name: %s", item)
                continue
            item_countries = [str(value).upper() for value in item.get("countries") or []]
            countries.update(item_countries)
            institution_country = country.upper() if country else (item_countries or [""])[0]
            max_consent = item.get("max_access_valid_for_days")
            max_history = item.get("transaction_total_days")
            institutions.append(
                InstitutionData(
                    name=institution_id,
                    display_name=display_name,
                    country=institution_country,
                    logo=item.get("logo"),
                    bic=item.get("bic"),
                    psu_types=[],
                    max_consent_days=int(max_consent) if max_consent is not None else None,
                    max_history_days=int(max_history) if max_history is not None else None,
                )
            )
        institutions.sort(
            key=lambda institution: (institution.country, institution.display_name.lower())
        )
        return InstitutionListData(countries=sorted(countries), institutions=institutions)

    # ----- OAuth authorization -----

    async def get_oauth_url(
        self,
        redirect_uri: str,
        state: str,
        flow_params: Optional[dict] = None,
    ) -> str:
        flow_params = flow_params or {}
        institution_id = (flow_params.get("institution_name") or "").strip()
        if not institution_id:
            raise ValueError("GoCardless requires flow_params with 'institution_name'")

        institution = await self._request("GET", f"/institutions/{institution_id}/")
        supported_features = institution.get("supported_features") or []
        max_history_days = (
            DEFAULT_HISTORY_DAYS
            if "separate_continuous_history_consent" in supported_features
            else int(institution.get("transaction_total_days") or DEFAULT_HISTORY_DAYS)
        )
        institution_max_valid = int(
            institution.get("max_access_valid_for_days") or FALLBACK_ACCESS_VALID_FOR_DAYS
        )
        requested_valid = int(flow_params.get("valid_until_days") or institution_max_valid)
        access_valid_days = min(requested_valid, institution_max_valid)
        agreement_payload = {
            "institution_id": institution_id,
            "max_historical_days": max_history_days,
            "access_valid_for_days": access_valid_days,
            "access_scope": BASE_SCOPES,
        }
        try:
            agreement = await self._request(
                "POST", "/agreements/enduser/", json_body=agreement_payload
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 400:
                raise
            fallback_payload = {
                **agreement_payload,
                "access_valid_for_days": FALLBACK_ACCESS_VALID_FOR_DAYS,
                "max_historical_days": FALLBACK_MAX_HISTORICAL_DAYS,
            }
            agreement = await self._request(
                "POST", "/agreements/enduser/", json_body=fallback_payload
            )

        requisition = await self._request(
            "POST",
            "/requisitions/",
            json_body={
                "redirect": redirect_uri,
                "institution_id": institution_id,
                "agreement": agreement["id"],
                "reference": state,
                "user_language": "en",
            },
        )
        link = requisition.get("link")
        if not link:
            raise RuntimeError("GoCardless /requisitions/ response missing link")
        return link

    async def reauth_url(
        self,
        credentials: dict,
        settings: dict,
        redirect_uri: str,
        state: str,
    ) -> str:
        stored = (settings or {}).get("flow_params") or {}
        if not stored.get("institution_name"):
            raise RuntimeError("Cannot reauth GoCardless connection without stored flow_params")
        return await self.get_oauth_url(redirect_uri, state, flow_params=stored)

    # ----- requisition exchange -----

    async def handle_oauth_callback(self, code: str) -> ConnectionData:
        requisition: Optional[dict] = None
        for page in range(REQUISITION_PAGE_LIMIT):
            body = await self._request(
                "GET",
                "/requisitions/",
                params={"limit": 100, "offset": page * 100},
            )
            results = body.get("results") or []
            requisition = next((item for item in results if item.get("reference") == code), None)
            if requisition:
                break
            if not body.get("next") and len(results) < 100:
                break
        if not requisition:
            raise RuntimeError("GoCardless requisition not found for reference")

        status = requisition.get("status") or ""
        if status != "LN":
            raise ProviderUserActionRequired(
                f"Bank authorization was not completed (requisition status {status}).",
                code="requisition_not_linked",
            )

        agreement = await self._request("GET", f"/agreements/enduser/{requisition['agreement']}/")
        access_valid_days = int(
            agreement.get("access_valid_for_days") or FALLBACK_ACCESS_VALID_FOR_DAYS
        )
        accepted = _parse_iso_datetime(agreement.get("accepted"))
        valid_until = (
            (accepted or datetime.now(timezone.utc)) + timedelta(days=access_valid_days)
        ).isoformat()

        institution_id = requisition["institution_id"]
        institution = await self._request("GET", f"/institutions/{institution_id}/")
        institution_name = institution.get("name") or "Bank"

        accounts: list[AccountData] = []
        for account_id in requisition.get("accounts") or []:
            account = await self._build_account(account_id)
            if account:
                accounts.append(account)

        requisition_id = requisition["id"]
        credentials = {
            "requisition_id_enc": encrypt(requisition_id) or requisition_id,
            "institution_id": institution_id,
            "institution_name": institution_name,
            "max_history_days": int(agreement.get("max_historical_days") or DEFAULT_HISTORY_DAYS),
            "valid_until": valid_until,
        }
        return ConnectionData(
            external_id=requisition_id,
            institution_name=institution_name,
            credentials=credentials,
            accounts=accounts,
            logo_url=institution.get("logo"),
        )

    async def _build_account(self, account_id: str) -> Optional[AccountData]:
        metadata = await self._request("GET", f"/accounts/{account_id}/")
        status = str(metadata.get("status") or "").upper()
        if status in {"SUSPENDED", "EXPIRED"}:
            logger.warning("Skipping %s account %s", status.lower(), account_id)
            return None

        try:
            details_response = await self._request("GET", f"/accounts/{account_id}/details/")
        except httpx.HTTPError as exc:
            logger.warning("Failed to fetch details for account %s: %s", account_id, exc)
            return None

        details = dict(details_response.get("account") or {})
        merged = {**details, **{key: value for key, value in metadata.items() if value}}
        if metadata.get("owner_name"):
            merged["ownerName"] = metadata["owner_name"]
        if metadata.get("cash_account_type"):
            merged["cashAccountType"] = metadata["cash_account_type"]
        if metadata.get("masked_pan"):
            merged["maskedPan"] = metadata["masked_pan"]

        balance = Decimal("0")
        currency = merged.get("currency") or "GBP"
        balance_response = await self._request(
            "GET", f"/accounts/{account_id}/balances/"
        )
        by_type = {
            item.get("balanceType"): item
            for item in balance_response.get("balances") or []
            if isinstance(item, dict)
        }
        picked = next(
            (by_type[key] for key in _BALANCE_PRIORITY if key in by_type), None
        )
        if picked:
            amount = picked.get("balanceAmount") or {}
            try:
                balance = Decimal(str(amount.get("amount", "0")))
            except InvalidOperation:
                logger.warning(
                    "Skipping account %s: unparseable balance amount %r",
                    account_id,
                    amount.get("amount"),
                )
                return None
            currency = amount.get("currency") or currency

        name = merged.get("name") or merged.get("product") or merged.get("ownerName") or "Account"
        return AccountData(
            external_id=account_id,
            name=name,
            type=_map_cash_account_type(merged.get("cashAccountType")),
            balance=balance,
            currency=currency,
            masked_number=mask_last4(merged.get("iban") or merged.get("maskedPan")),
        )

    # ----- account and transaction fetches -----

    def _requisition_id(self, credentials: dict) -> str:
        encrypted = credentials.get("requisition_id_enc")
        if encrypted:
            decoded = decrypt(encrypted)
            if decoded:
                return decoded
        return credentials.get("requisition_id") or ""

    async def get_accounts(self, credentials: dict) -> list[AccountData]:
        requisition_id = self._requisition_id(credentials)
        if not requisition_id:
            raise SessionExpiredError("GoCardless requisition_id missing")
        requisition = await self._request("GET", f"/requisitions/{requisition_id}/")
        if requisition.get("status") in {"EX", "SU", "RJ"}:
            raise SessionExpiredError("GoCardless requisition expired")

        accounts: list[AccountData] = []
        for account_id in requisition.get("accounts") or []:
            account = await self._build_account(account_id)
            if account:
                accounts.append(account)
        return accounts

    async def get_transactions(
        self,
        credentials: dict,
        account_external_id: str,
        since: Optional[date] = None,
        payee_source: str = "auto",
    ) -> list[TransactionData]:
        history_days = int(credentials.get("max_history_days") or DEFAULT_HISTORY_DAYS)
        date_from = (since or (date.today() - timedelta(days=history_days))).isoformat()
        page = await self._request(
            "GET",
            f"/accounts/{account_external_id}/transactions/",
            params={"date_from": date_from, "date_to": date.today().isoformat()},
        )
        transactions: list[TransactionData] = []
        body = page.get("transactions") or {}
        quirk = _BANK_QUIRKS.get(credentials.get("institution_id") or "")
        for raw, status in [
            *((item, "posted") for item in body.get("booked") or []),
            *((item, "pending") for item in body.get("pending") or []),
        ]:
            candidate = dict(raw)
            if quirk:
                candidate = quirk(candidate, status)
            if candidate is None:
                continue
            transaction = self._build_transaction(
                account_external_id, candidate, status, payee_source
            )
            if transaction:
                transactions.append(transaction)
        return transactions

    def _build_transaction(
        self,
        account_uid: str,
        raw: dict,
        status: str,
        payee_source: str,
    ) -> Optional[TransactionData]:
        amount_object = raw.get("transactionAmount") or {}
        try:
            amount = Decimal(str(amount_object.get("amount", "0")))
        except InvalidOperation:
            return None
        txn_type = "debit" if amount < 0 else "credit"
        transaction_date = _parse_iso_date(raw.get("bookingDate") or raw.get("valueDate"))
        if not transaction_date:
            return None
        description = (
            _join_remittance(raw.get("remittanceInformationUnstructured"))
            or _join_remittance(raw.get("remittanceInformationUnstructuredArray"))
            or _join_remittance(raw.get("remittanceInformationStructured"))
            or _join_remittance(raw.get("additionalInformation"))
        )
        description = description[:500] or "Transaction"
        if status == "posted":
            external_id = (
                raw.get("transactionId")
                or raw.get("entryReference")
                or raw.get("internalTransactionId")
                or _txn_fingerprint(account_uid, raw)
            )
        else:
            external_id = raw.get("transactionId") or _txn_fingerprint(account_uid, raw)
        raw.pop("_fingerprintBookingDate", None)
        return TransactionData(
            external_id=str(external_id),
            description=description,
            amount=amount.copy_abs(),
            date=transaction_date,
            type=txn_type,
            currency=amount_object.get("currency") or "GBP",
            status=status,
            payee=_extract_payee(raw, txn_type, payee_source),
            raw_data=raw,
        )

    # ----- credential lifecycle -----

    async def refresh_credentials(self, credentials: dict) -> dict:
        valid_until = credentials.get("valid_until")
        expires_at = _parse_iso_datetime(valid_until)
        if expires_at and expires_at <= datetime.now(timezone.utc):
            raise SessionExpiredError("GoCardless consent expired; user must re-authorize")
        return credentials

    async def get_institution_logo(self, credentials: dict) -> Optional[str]:
        institution_id = credentials.get("institution_id")
        if not institution_id:
            return None
        try:
            institution = await self._request("GET", f"/institutions/{institution_id}/")
        except (httpx.HTTPError, ProviderRateLimited, SessionExpiredError):
            return None
        return institution.get("logo")
