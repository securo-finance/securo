"""Bank Leumi (Israel) through the israeli-banks sidecar.

Leumi offers no API to individuals (Israeli open banking is limited to
licensed providers), so the sidecar drives Leumi's own website with
israeli-bank-scrapers. It never sees a password: it opens Leumi's
"log in with the app" page, hands the QR code to the user through Securo, and
reads the accounts once the user has scanned it with the Leumi app.

Each read needs a fresh scan, so this provider has no unattended sync:
  * connect / reconnect = scan the QR; the sidecar keeps that read's result
    for a limited time (LEUMI_QR_RESULT_TTL_MINUTES, default 60);
  * syncs inside that window are served from the result;
  * after it, the connection is marked expired and the accounts page offers
    "Reconnect", which shows a new QR.
"""

import hashlib
import re
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Optional

import httpx

from app.core.config import get_settings
from app.providers.base import (
    AccountData,
    BankProvider,
    ConnectionData,
    ConnectTokenData,
    ProviderUserActionRequired,
    RefreshOutcome,
    SessionExpiredError,
    TransactionData,
    mask_last4,
)

_EXPIRED_MESSAGE = "Scan the Leumi QR code again to sync this connection."
# Leumi appends this explanation to every channel-fee row; it adds nothing.
_FEE_BOILERPLATE = "סכום החיוב מייצג"


def _hash(*parts: str, length: int = 24) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:length]


def _account_external_id(raw: dict) -> str:
    # The full account number never leaves the sidecar's memory as an id: the
    # external id is a hash, and only the last four digits are kept for display.
    return "leumi-" + _hash(str(raw.get("accountNumber", "")), str(raw.get("currency") or "ILS"))


def _to_decimal(value: object) -> Optional[Decimal]:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _last4(raw: dict) -> Optional[str]:
    # Foreign-currency accounts come back with the currency appended to the
    # number (e.g. "…/12 001 USD"); only the digits identify the account.
    return mask_last4(re.sub(r"\D", "", str(raw.get("accountNumber") or "")))


def _account_name(raw: dict) -> str:
    last4 = _last4(raw)
    suffix = f" ****{last4}" if last4 else ""
    if raw.get("savingsAccount"):
        return f"Leumi deposit{suffix}"
    currency = raw.get("currency") or "ILS"
    return f"Leumi{'' if currency == 'ILS' else ' ' + currency}{suffix}"


class LeumiQrProvider(BankProvider):
    def __init__(self) -> None:
        settings = get_settings()
        self._base_url = settings.israeli_banks_url.rstrip("/")
        self._token = settings.israeli_banks_token.get_secret_value()
        self._history_days = settings.israeli_banks_history_days

    @property
    def name(self) -> str:
        return "leumi"

    @property
    def flow_type(self) -> str:
        return "qr"

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url,
            headers={"Authorization": f"Bearer {self._token}"},
            timeout=30.0,
        )

    # --- connect: start a QR session, poll it, then claim the result ---------

    async def create_connect_token(
        self, client_user_id: str, item_id: str | None = None
    ) -> ConnectTokenData:
        start = (date.today() - timedelta(days=self._history_days)).isoformat()
        async with self._client() as client:
            resp = await client.post(
                "/sessions", json={"user_id": client_user_id, "start_date": start}
            )
        if resp.status_code == 409:
            raise ProviderUserActionRequired(
                "Another Leumi QR login is already in progress. Wait for it to "
                "finish or time out (3 minutes) and try again.",
                code="qr_busy",
            )
        resp.raise_for_status()
        return ConnectTokenData(access_token=resp.json()["id"])

    async def get_qr_session(self, session_id: str, client_user_id: str) -> dict:
        async with self._client() as client:
            resp = await client.get(f"/sessions/{session_id}", params={"user_id": client_user_id})
        if resp.status_code == 404:
            raise SessionExpiredError("This QR session has expired. Start again.")
        resp.raise_for_status()
        body = resp.json()
        return {"status": body.get("status"), "qr": body.get("qr"), "error": body.get("error")}

    async def handle_oauth_callback(self, code: str) -> ConnectionData:
        result = await self._result({"session_id": code})
        accounts = [self._build_account(raw) for raw in result.get("accounts", [])]
        if not accounts:
            raise ValueError("Leumi returned no accounts")
        # Stable across reconnects (new session, same customer), so the
        # connection keeps its accounts instead of duplicating them.
        primary = sorted(
            str(a.get("accountNumber", "")) for a in result["accounts"] if not a.get("savingsAccount")
        )
        return ConnectionData(
            external_id="leumi-" + _hash(*primary),
            institution_name="Bank Leumi",
            credentials={"session_id": code, "scraped_at": result.get("scraped_at")},
            accounts=accounts,
        )

    async def get_oauth_url(
        self, redirect_uri: str, state: str, flow_params: Optional[dict] = None
    ) -> str:
        raise NotImplementedError("Bank Leumi connects by QR code, not a redirect")

    # --- reads -----------------------------------------------------------------

    async def _result(self, credentials: dict) -> dict:
        session_id = (credentials or {}).get("session_id")
        if not session_id:
            raise SessionExpiredError(_EXPIRED_MESSAGE)
        async with self._client() as client:
            resp = await client.get(f"/sessions/{session_id}/result")
        if resp.status_code in (404, 409):
            raise SessionExpiredError(_EXPIRED_MESSAGE)
        resp.raise_for_status()
        return resp.json()

    async def trigger_refresh(self, credentials: dict | None) -> RefreshOutcome:
        try:
            await self._result(credentials or {})
        except SessionExpiredError:
            return "needs_user_action"
        return "skipped"

    async def refresh_credentials(self, credentials: dict) -> dict:
        return credentials

    @staticmethod
    def _build_account(raw: dict) -> AccountData:
        balance = _to_decimal(raw.get("balance")) or Decimal(0)
        return AccountData(
            external_id=_account_external_id(raw),
            name=_account_name(raw),
            type="savings" if raw.get("savingsAccount") else "checking",
            balance=balance,
            currency=raw.get("currency") or "ILS",
            masked_number=_last4(raw),
        )

    async def get_accounts(self, credentials: dict) -> list[AccountData]:
        result = await self._result(credentials)
        return [self._build_account(raw) for raw in result.get("accounts", [])]

    async def get_transactions(
        self,
        credentials: dict,
        account_external_id: str,
        since: Optional[date] = None,
        payee_source: str = "auto",
    ) -> list[TransactionData]:
        result = await self._result(credentials)
        raw_account = next(
            (a for a in result.get("accounts", []) if _account_external_id(a) == account_external_id),
            None,
        )
        if raw_account is None:
            return []
        return self.build_transactions(raw_account, since)

    @staticmethod
    def build_transactions(raw_account: dict, since: Optional[date] = None) -> list[TransactionData]:
        transactions: list[TransactionData] = []
        occurrences: Counter[str] = Counter()
        account_currency = raw_account.get("currency") or "ILS"
        for raw in raw_account.get("txns", []):
            # Leumi lists today's rows as pending and may still change them;
            # they are picked up on the next read once posted.
            if raw.get("status") == "pending":
                continue
            amount = _to_decimal(raw.get("amount"))
            try:
                txn_date = date.fromisoformat(str(raw.get("date")))
            except ValueError:
                continue
            if amount is None or (since and txn_date < since):
                continue
            description = (raw.get("description") or "").strip() or "Transaction"
            memo = (raw.get("memo") or "").strip()
            if memo and not memo.startswith(_FEE_BOILERPLATE):
                description = f"{description} | {memo}"
            # Leumi's reference number repeats (a standing order keeps its
            # number every month), so the id combines it with the date, amount
            # and text, plus a counter for genuinely identical rows.
            key = "|".join(
                [txn_date.isoformat(), str(amount), str(raw.get("identifier") or ""), description]
            )
            occurrences[key] += 1
            transactions.append(
                TransactionData(
                    external_id="leumi-" + _hash(key, str(occurrences[key])),
                    description=description[:500],
                    amount=amount.copy_abs(),
                    date=txn_date,
                    type="debit" if amount < 0 else "credit",
                    currency=raw.get("currency") or account_currency,
                    status="posted",
                    raw_data={"identifier": raw.get("identifier")},
                )
            )
        return transactions


