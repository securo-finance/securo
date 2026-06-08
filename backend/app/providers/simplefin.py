"""SimpleFIN Bridge provider.

SimpleFIN (https://www.simplefin.org) is a read-only financial interchange
protocol. The user gets a Setup Token from a SimpleFIN server (typically the
SimpleFIN Bridge at https://bridge.simplefin.org), pastes it into Securo,
and Securo claims an Access URL that embeds Basic Auth credentials. From then
on, ``GET {access_url}/accounts?version=2`` returns accounts + transactions
+ holdings in a single JSON document.

The protocol is intentionally simple: there's no OAuth dance, no widget, no
on-demand refresh, no MFA. Every read goes to the bridge, which pulls from
the bank on its own schedule. Rate limits (24 req/day per access token for
the all-accounts endpoint, plus per-account quotas) are documented at
https://beta-bridge.simplefin.org/info/developers — we don't try to enforce
them client-side; the bridge surfaces warnings in the ``errlist`` response.

All SimpleFIN-specific shapes (Setup Token base64, ``errlist`` codes, 90-day
window, ``balance-date`` epoch seconds) are contained in this module.
"""
from __future__ import annotations

import base64
import binascii
import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

import httpx

from app.agents.services.crypto import decrypt, encrypt
from app.providers.favicon import favicon_url_for
from app.providers.base import (
    AccountData,
    BankProvider,
    ConnectionData,
    HoldingData,
    ProviderUserActionRequired,
    SessionExpiredError,
    TransactionData,
)

logger = logging.getLogger(__name__)

# SimpleFIN constrains each ``/accounts`` call to a 90-day window. For the
# initial sync (since=None) we walk backwards in chunks of this size; for
# follow-up syncs the sync layer's typical 30-90 day window fits in one call.
SIMPLEFIN_MAX_WINDOW_DAYS = 90
SIMPLEFIN_DEFAULT_HISTORY_DAYS = 90
SIMPLEFIN_INITIAL_HISTORY_DAYS = 365  # ~1 year backfill on first connect
SIMPLEFIN_HTTP_TIMEOUT = 60.0

# Error codes that signal the user must re-authorize via the Bridge (the
# stored Access URL is no longer valid). Everything else under con.* / act.*
# is treated as transient and surfaces as a warning, not a hard failure.
_REAUTH_ERROR_CODES = frozenset({"gen.auth", "con.auth"})


def _decode_setup_token(raw: str) -> str:
    """Decode a SimpleFIN Setup Token (Base64-encoded URL).

    The token comes straight from the Bridge's setup page; users paste it in
    with surrounding whitespace and sometimes line breaks, so we normalize.
    """
    cleaned = "".join(raw.split())
    if not cleaned:
        raise ValueError("SimpleFIN setup token is empty")
    # Re-pad to a multiple of 4; SimpleFIN tokens are technically already
    # padded but pasted versions sometimes lose the trailing '='.
    padding = (-len(cleaned)) % 4
    cleaned = cleaned + ("=" * padding)
    try:
        decoded = base64.b64decode(cleaned, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise ValueError("SimpleFIN setup token is not valid base64") from exc
    if not decoded.startswith(("http://", "https://")):
        raise ValueError("SimpleFIN setup token did not decode to a URL")
    return decoded


def _epoch_to_date(value: Any) -> Optional[date]:
    if value is None or value == "":
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).date()
    except (ValueError, TypeError, OSError):
        return None


def _to_decimal(value: Any) -> Optional[Decimal]:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _surface_errors(errlist: list[dict], context: str) -> None:
    """Raise a typed exception for auth errors; log everything else.

    SimpleFIN's spec says: *Always show those errors to your end users.* For
    the codes the user can act on (re-auth), we raise so the API layer can
    flip the connection status. For the rest we log and continue — the
    response usually still has usable data alongside the warnings.
    """
    if not errlist:
        return
    reauth = [e for e in errlist if (e.get("code") or "").lower() in _REAUTH_ERROR_CODES]
    if reauth:
        first = reauth[0]
        raise ProviderUserActionRequired(
            first.get("msg") or first.get("message") or "Reauthorize at SimpleFIN Bridge",
            code="credentials_invalid",
            help_url="https://bridge.simplefin.org/",
        )
    for entry in errlist:
        logger.warning(
            "SimpleFIN %s warning code=%s msg=%s",
            context,
            entry.get("code"),
            entry.get("msg") or entry.get("message"),
        )


class SimpleFinProvider(BankProvider):
    """SimpleFIN Bridge connector."""

    @property
    def name(self) -> str:
        return "simplefin"

    @property
    def flow_type(self) -> str:
        return "token"

    # ----- credentials / access URL handling --------------------------------

    @staticmethod
    def _access_url(credentials: dict) -> str:
        enc = (credentials or {}).get("access_url_enc")
        if enc:
            decoded = decrypt(enc)
            if decoded:
                return decoded
        # Backward compat / dev: also accept plaintext.
        return (credentials or {}).get("access_url") or ""

    async def _client(self, credentials: dict | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=SIMPLEFIN_HTTP_TIMEOUT,
            headers={
                "Accept": "application/json",
                "User-Agent": "Securo/0.1 (+https://usesecuro.com)",
            },
        )

    async def _fetch_accounts(
        self,
        credentials: dict,
        *,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        account_id: Optional[str] = None,
        pending: bool = True,
    ) -> dict:
        access_url = self._access_url(credentials)
        if not access_url:
            raise SessionExpiredError("SimpleFIN access URL is missing")
        params: dict[str, Any] = {"version": "2"}
        if pending:
            params["pending"] = "1"
        if start_date:
            params["start-date"] = str(int(datetime.combine(
                start_date, datetime.min.time(), tzinfo=timezone.utc,
            ).timestamp()))
        if end_date:
            params["end-date"] = str(int(datetime.combine(
                end_date, datetime.min.time(), tzinfo=timezone.utc,
            ).timestamp()))
        if account_id:
            params["account"] = account_id
        async with await self._client(credentials) as client:
            resp = await client.get(f"{access_url}/accounts", params=params)
        if resp.status_code in (401, 403):
            raise ProviderUserActionRequired(
                f"SimpleFIN refused the request ({resp.status_code})",
                code="credentials_invalid",
                help_url="https://bridge.simplefin.org/",
            )
        resp.raise_for_status()
        return resp.json() or {}

    # ----- connection flow ---------------------------------------------------

    def get_oauth_url(self, *args, **kwargs):  # type: ignore[override]
        raise NotImplementedError("SimpleFIN uses paste-a-token flow, not OAuth redirect")

    async def handle_oauth_callback(self, code: str) -> ConnectionData:
        """Claim a SimpleFIN Access URL from a Setup Token.

        The endpoint name is a leftover from the OAuth flow but the contract
        — "given an opaque code, produce a ConnectionData" — applies cleanly
        to token-paste providers. The Setup Token is a single-use base64
        string from a SimpleFIN server; claiming it yields an Access URL that
        embeds Basic Auth credentials for subsequent reads.
        """
        claim_url = _decode_setup_token(code)
        async with await self._client() as client:
            try:
                claim_resp = await client.post(
                    claim_url, headers={"Content-Length": "0"}
                )
            except httpx.HTTPError as exc:
                raise RuntimeError(
                    f"SimpleFIN claim request failed: {exc}"
                ) from exc
        if claim_resp.status_code == 403:
            # The bridge returns 403 when a setup token is reused.
            raise ProviderUserActionRequired(
                "SimpleFIN setup token has already been used or expired. "
                "Generate a fresh token from the SimpleFIN Bridge.",
                code="setup_token_used",
                help_url="https://bridge.simplefin.org/",
            )
        if claim_resp.status_code >= 400:
            raise RuntimeError(
                f"SimpleFIN claim returned {claim_resp.status_code}: "
                f"{claim_resp.text[:200]}"
            )
        access_url = (claim_resp.text or "").strip().strip('"')
        if not access_url.startswith(("http://", "https://")):
            raise RuntimeError("SimpleFIN claim did not return a URL")

        credentials: dict[str, Any] = {
            "access_url_enc": encrypt(access_url) or access_url,
            "claimed_at": datetime.now(timezone.utc).isoformat(),
        }

        # Pull the account list once so we can return an institution name and
        # the initial AccountData list. Filter out transactions here — they're
        # fetched per-account by ``get_transactions``.
        payload = await self._fetch_accounts(credentials, pending=False)
        _surface_errors(payload.get("errlist") or [], context="claim")
        institution_name, accounts = self._parse_accounts(payload)
        return ConnectionData(
            external_id=self._stable_external_id(payload, claim_url),
            institution_name=institution_name,
            credentials=credentials,
            accounts=accounts,
            # SimpleFIN has no logo image, but its org metadata carries the
            # bank's website — derive a favicon from it (same approach as
            # asset logos).
            logo_url=favicon_url_for(self._org_website(payload)),
        )

    @staticmethod
    def _org_website(payload: dict) -> Optional[str]:
        """Best-effort bank website from a SimpleFIN /accounts payload.

        Checks the connection metadata first, then each account's ``org``
        (the SimpleFIN spec attaches ``url``/``domain`` to the institution
        org). Returns None when nothing usable is present.
        """
        for src in (payload.get("connections") or []):
            url = src.get("url") or src.get("domain")
            if url:
                return url
        for acc in (payload.get("accounts") or []):
            org = acc.get("org") or {}
            url = org.get("url") or org.get("domain")
            if url:
                return url
        return None

    async def get_institution_logo(self, credentials: dict) -> Optional[str]:
        payload = await self._fetch_accounts(credentials, pending=False)
        return favicon_url_for(self._org_website(payload))

    @staticmethod
    def _stable_external_id(payload: dict, fallback: str) -> str:
        """Produce a stable external id for this connection.

        Prefer the first connection's ``conn_id`` (per the SimpleFIN spec each
        Access URL is one Connection at most), else the first account's
        ``conn_id``, else fall back to the claim URL hash. Falling back to the
        claim URL keeps the id deterministic across re-claims of the same
        setup token.
        """
        conns = payload.get("connections") or []
        if conns and conns[0].get("conn_id"):
            return str(conns[0]["conn_id"])
        for acc in payload.get("accounts") or []:
            if acc.get("conn_id"):
                return str(acc["conn_id"])
        # Hash the claim URL so we don't leak it.
        import hashlib

        return "simplefin-" + hashlib.sha256(fallback.encode("utf-8")).hexdigest()[:24]

    # ----- account / transaction reads --------------------------------------

    @staticmethod
    def _parse_accounts(payload: dict) -> tuple[str, list[AccountData]]:
        connections = payload.get("connections") or []
        institution_name = (
            connections[0].get("name") if connections else "SimpleFIN Connection"
        )
        accounts: list[AccountData] = []
        for raw in payload.get("accounts") or []:
            balance = _to_decimal(raw.get("balance")) or Decimal("0")
            currency = raw.get("currency") or "USD"
            account_id = str(raw.get("id") or "")
            if not account_id:
                continue
            name = raw.get("name") or "Account"
            accounts.append(
                AccountData(
                    external_id=account_id,
                    name=name,
                    type="checking",  # SimpleFIN doesn't expose an account type
                    balance=balance,
                    currency=currency,
                )
            )
        return institution_name or "SimpleFIN Connection", accounts

    async def get_accounts(self, credentials: dict) -> list[AccountData]:
        payload = await self._fetch_accounts(credentials, pending=False)
        _surface_errors(payload.get("errlist") or [], context="get_accounts")
        _, accounts = self._parse_accounts(payload)
        return accounts

    async def get_transactions(
        self,
        credentials: dict,
        account_external_id: str,
        since: Optional[date] = None,
        payee_source: str = "auto",
    ) -> list[TransactionData]:
        end_date = date.today()
        if since is None:
            start_date = end_date - timedelta(days=SIMPLEFIN_INITIAL_HISTORY_DAYS)
        else:
            start_date = since
        # Walk the request window in 90-day chunks (SimpleFIN's per-call cap).
        transactions: list[TransactionData] = []
        seen_ids: set[str] = set()
        cursor = start_date
        while cursor <= end_date:
            chunk_end = min(cursor + timedelta(days=SIMPLEFIN_MAX_WINDOW_DAYS), end_date)
            payload = await self._fetch_accounts(
                credentials,
                start_date=cursor,
                end_date=chunk_end + timedelta(days=1),
                account_id=account_external_id,
                pending=True,
            )
            _surface_errors(payload.get("errlist") or [], context="get_transactions")
            for raw_acc in payload.get("accounts") or []:
                if str(raw_acc.get("id") or "") != account_external_id:
                    continue
                for raw_txn in raw_acc.get("transactions") or []:
                    parsed = self._build_transaction(raw_txn, payee_source)
                    if parsed and parsed.external_id not in seen_ids:
                        seen_ids.add(parsed.external_id)
                        transactions.append(parsed)
            cursor = chunk_end + timedelta(days=1)
        return transactions

    @staticmethod
    def _build_transaction(raw: dict, payee_source: str) -> Optional[TransactionData]:
        txn_id = str(raw.get("id") or "")
        if not txn_id:
            return None
        amount_raw = _to_decimal(raw.get("amount"))
        if amount_raw is None:
            return None
        txn_type = "debit" if amount_raw < 0 else "credit"
        amount = amount_raw.copy_abs()
        posted = _epoch_to_date(raw.get("posted"))
        transacted = _epoch_to_date(raw.get("transacted_at"))
        txn_date = posted or transacted
        if not txn_date:
            return None
        description = (
            raw.get("description")
            or raw.get("payee")
            or raw.get("memo")
            or "Transaction"
        ).strip()[:500]
        status = "pending" if raw.get("pending") else "posted"
        payee: Optional[str]
        if payee_source == "none":
            payee = None
        else:
            payee = (raw.get("payee") or "").strip() or None
        return TransactionData(
            external_id=txn_id,
            description=description,
            amount=amount,
            date=txn_date,
            type=txn_type,
            currency=raw.get("currency"),
            status=status,
            payee=payee,
            raw_data=raw,
        )

    async def get_holdings(self, credentials: dict) -> list[HoldingData]:
        payload = await self._fetch_accounts(credentials, pending=False)
        _surface_errors(payload.get("errlist") or [], context="get_holdings")
        holdings: list[HoldingData] = []
        for raw_acc in payload.get("accounts") or []:
            acc_currency = raw_acc.get("currency") or "USD"
            for raw in raw_acc.get("holdings") or []:
                holding_id = str(raw.get("id") or "")
                if not holding_id:
                    continue
                market_value = _to_decimal(raw.get("market_value"))
                if market_value is None:
                    continue
                holdings.append(
                    HoldingData(
                        external_id=holding_id,
                        name=raw.get("description") or raw.get("symbol") or holding_id,
                        currency=raw.get("currency") or acc_currency,
                        current_value=market_value,
                        quantity=_to_decimal(raw.get("shares")),
                        unit_price=_to_decimal(raw.get("market_value")) / _to_decimal(
                            raw.get("shares")
                        ) if _to_decimal(raw.get("shares")) else None,
                        purchase_price=_to_decimal(raw.get("purchase_price")),
                        purchase_date=_epoch_to_date(raw.get("created")),
                        isin=raw.get("isin"),
                        metadata={
                            "symbol": raw.get("symbol"),
                            "cost_basis": str(raw.get("cost_basis"))
                            if raw.get("cost_basis") is not None
                            else None,
                        },
                    )
                )
        return holdings

    async def refresh_credentials(self, credentials: dict) -> dict:
        if not self._access_url(credentials):
            raise SessionExpiredError("SimpleFIN access URL missing")
        return credentials
