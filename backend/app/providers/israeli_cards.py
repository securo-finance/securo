"""Israeli credit-card companies (Max, Isracard) through the israeli-banks sidecar.

These companies offer no API to individuals and no QR login, so the sidecar
logs in with the user's details using israeli-bank-scrapers. The details are
typed into Securo's connect form, stored on the connection encrypted
(credential_crypto) and sent to the sidecar only for the duration of a read.
One read serves a whole sync: the sidecar reuses it for 10 minutes.
"""

import hashlib
import json
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Optional

import httpx

from app.core.config import Settings
from app.providers.base import (
    AccountData,
    BankProvider,
    ConnectionData,
    ProviderUserActionRequired,
    RefreshOutcome,
    SessionExpiredError,
    TransactionData,
)
from app.providers.credential_crypto import seal, unseal

# A read logs in, opens every card and pages through the history, and may
# first wait for a Leumi QR login in progress (the service runs one browser).
_READ_TIMEOUT = 600.0


def _hash(*parts: str, length: int = 24) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:length]


def _to_decimal(value: object) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _to_date(value: object) -> Optional[date]:
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


class IsraeliCardProvider(BankProvider):
    company: str = ""
    display_name: str = ""
    fields: tuple[str, ...] = ()
    # The field that identifies the customer (not a secret), for the
    # connection id: reconnecting the same login updates the same connection.
    identity_field: str = ""

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__(settings)
        self._base_url = self.settings.israeli_banks_url.rstrip("/")
        self._token = self.settings.israeli_banks_token.get_secret_value()
        self._history_days = self.settings.israeli_banks_history_days

    @property
    def name(self) -> str:
        return self.company

    @property
    def flow_type(self) -> str:
        return "credentials"

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url,
            headers={"Authorization": f"Bearer {self._token}"},
            timeout=_READ_TIMEOUT,
        )

    def _parse_login(self, code: str) -> dict[str, str]:
        try:
            raw = json.loads(code)
        except ValueError as exc:
            raise ValueError("Login details are malformed") from exc
        if not isinstance(raw, dict):
            raise ValueError("Login details are malformed")
        # Identifiers are trimmed; a password is sent exactly as typed, spaces
        # included, or a login that works on the company's site fails here.
        values = {
            f: str(raw.get(f) or "") if f == "password" else str(raw.get(f) or "").strip()
            for f in self.fields
        }
        missing = [f for f, v in values.items() if not v]
        if missing:
            raise ValueError(f"Missing: {', '.join(missing)}")
        return values

    def _login(self, credentials: dict) -> dict[str, str]:
        values = unseal((credentials or {}).get("sealed"))
        if not values:
            raise SessionExpiredError(f"Enter your {self.display_name} login again to sync.")
        return values

    async def _read(self, login: dict[str, str]) -> dict:
        start = (date.today() - timedelta(days=self._history_days)).isoformat()
        async with self._client() as client:
            resp = await client.post(
                "/scrape",
                json={"company": self.company, "credentials": login, "start_date": start},
            )
        if resp.status_code == 422:
            body = resp.json()
            error_type = body.get("error_type") or "GENERIC"
            if error_type in ("INVALID_PASSWORD", "CHANGE_PASSWORD", "ACCOUNT_BLOCKED"):
                messages = {
                    "INVALID_PASSWORD": f"{self.display_name} rejected the login details.",
                    "CHANGE_PASSWORD": f"{self.display_name} requires a password change. Change it on their site, then reconnect.",
                    "ACCOUNT_BLOCKED": f"{self.display_name} reports the account as blocked.",
                }
                raise ProviderUserActionRequired(messages[error_type], code="credentials_invalid")
            raise RuntimeError(f"{self.display_name} read failed: {body.get('error') or error_type}")
        resp.raise_for_status()
        return resp.json()

    # --- connect -----------------------------------------------------------

    async def handle_oauth_callback(self, code: str) -> ConnectionData:
        """``code`` carries the login details typed into the connect form."""
        login = self._parse_login(code)
        result = await self._read(login)
        accounts = [self._build_account(raw) for raw in result.get("accounts", [])]
        return ConnectionData(
            external_id=f"{self.company}-" + _hash(self.company, login[self.identity_field]),
            institution_name=self.display_name,
            credentials={"sealed": seal(login)},
            accounts=accounts,
        )

    async def get_oauth_url(
        self, redirect_uri: str, state: str, flow_params: Optional[dict] = None
    ) -> str:
        raise NotImplementedError(f"{self.display_name} connects with login details, not a redirect")

    async def refresh_credentials(self, credentials: dict) -> dict:
        return credentials

    async def trigger_refresh(self, credentials: dict | None) -> RefreshOutcome:
        return "skipped"

    # --- reads -------------------------------------------------------------

    def _account_external_id(self, raw: dict) -> str:
        return f"{self.company}-" + _hash(self.company, str(raw.get("accountNumber", "")))

    def _build_account(self, raw: dict) -> AccountData:
        digits = "".join(ch for ch in str(raw.get("accountNumber") or "") if ch.isdigit())
        last4 = digits[-4:] if len(digits) >= 4 else None
        # The library reports what is owed as a negative number (limit minus
        # available credit, negated). Connected cards store debt positive, like
        # every other provider's, so the balance sites can negate it once.
        balance = -(_to_decimal(raw.get("balance")) or Decimal(0))
        return AccountData(
            external_id=self._account_external_id(raw),
            name=f"{self.display_name} ****{last4}" if last4 else self.display_name,
            type="credit_card",
            balance=balance,
            currency=raw.get("currency") or "ILS",
            credit_limit=_to_decimal(raw.get("creditLimit")),
            masked_number=last4,
        )

    async def get_accounts(self, credentials: dict) -> list[AccountData]:
        result = await self._read(self._login(credentials))
        return [self._build_account(raw) for raw in result.get("accounts", [])]

    async def get_transactions(
        self,
        credentials: dict,
        account_external_id: str,
        since: Optional[date] = None,
        payee_source: str = "auto",
    ) -> list[TransactionData]:
        result = await self._read(self._login(credentials))
        raw_account = next(
            (a for a in result.get("accounts", []) if self._account_external_id(a) == account_external_id),
            None,
        )
        return self.build_transactions(raw_account, since) if raw_account else []

    @staticmethod
    def _fallback_key(raw: dict, identifier: Optional[str]) -> str:
        installments = raw.get("installments") or {}
        if identifier:
            return f"id|{identifier}|{installments.get('number') or ''}"
        txn_date = raw.get("date") or ""
        description = (raw.get("description") or "").strip() or "Transaction"
        memo = (raw.get("memo") or "").strip()
        return "|".join([str(txn_date), str(raw.get("amount")), description, memo])

    @staticmethod
    def build_transactions(raw_account: dict, since: Optional[date] = None) -> list[TransactionData]:
        transactions: list[TransactionData] = []
        account_currency = raw_account.get("currency") or "ILS"
        raw_txns = raw_account.get("txns", [])

        # The ordinal that disambiguates two otherwise-identical charges
        # (same date, amount, description, no identifier) must come from the
        # sidecar's full history, counted here before the `since` filtering
        # below. Counting only the rows a particular sync's `since` keeps
        # would shift the ordinal as an earlier occurrence ages out of the
        # window, handing a later sync's row the id already used for a
        # different occurrence. Pending rows are excluded from the count,
        # not just skipped at emission: a pending row is never itself
        # emitted, and it can vanish between syncs once it posts or drops,
        # which must not shift a posted row's ordinal.
        seen: dict[str, int] = {}
        ordinals: list[int] = []
        identifiers: list[Optional[str]] = []
        for raw in raw_txns:
            identifier = raw.get("identifier")
            if identifier is not None and str(identifier).split("_")[0] in ("", "undefined", "null"):
                identifier = None
            identifiers.append(identifier)
            if raw.get("status") == "pending":
                ordinals.append(0)  # never used: the emission loop skips this row
                continue
            key = IsraeliCardProvider._fallback_key(raw, identifier)
            seen[key] = seen.get(key, 0) + 1
            ordinals.append(seen[key])

        for raw, identifier, ordinal in zip(raw_txns, identifiers, ordinals):
            # Pending charges may still change amount or disappear; they are
            # picked up once the company posts them.
            if raw.get("status") == "pending":
                continue
            amount = _to_decimal(raw.get("amount"))
            txn_date = _to_date(raw.get("date"))
            if amount is None or txn_date is None:
                continue
            # `since` rewinds a fortnight before the last sync. A charge keeps
            # its purchase date when it posts (after being pending above, or
            # as a later installment), so the cutoff also looks at the billing
            # date, which is what moves into the window.
            billed = _to_date(raw.get("processedDate")) or txn_date
            if since and max(txn_date, billed) < since:
                continue
            description = (raw.get("description") or "").strip() or "Transaction"
            memo = (raw.get("memo") or "").strip()
            installments = raw.get("installments") or {}
            key = IsraeliCardProvider._fallback_key(raw, identifier)
            original_currency = raw.get("originalCurrency")
            # A foreign charge not yet converted can carry a zero ILS amount;
            # its direction then comes from the original amount.
            foreign = bool(original_currency) and original_currency != "ILS"
            original_amount = _to_decimal(raw.get("originalAmount"))
            # The charged amount is only the account-currency figure when the
            # company billed it in that currency: Isracard bills foreign
            # Mastercard charges in dollars (paid from the bank's USD account),
            # so a charge in a third currency can come back as a USD amount.
            charged_currency = raw.get("currency") or account_currency
            charged_in_account_currency = charged_currency == account_currency
            transactions.append(
                TransactionData(
                    external_id="card-" + _hash(key, str(ordinal)),
                    description=(f"{description} | {memo}" if memo else description)[:500],
                    amount=(original_amount if foreign and original_amount is not None else amount).copy_abs(),
                    # Zero means the company has not converted the charge yet:
                    # leave it unset so local FX applies, not a zero expense.
                    amount_in_account_currency=(
                        amount.copy_abs() if foreign and charged_in_account_currency and amount else None
                    ),
                    currency=original_currency if foreign else (raw.get("currency") or "ILS"),
                    date=txn_date,
                    type="debit" if (amount or original_amount or 0) < 0 else "credit",
                    status="posted",
                    installment_number=installments.get("number"),
                    total_installments=installments.get("total"),
                    raw_data={"identifier": identifier, "processed_date": raw.get("processedDate")},
                )
            )
        return transactions


class MaxProvider(IsraeliCardProvider):
    company = "max"
    display_name = "Max"
    fields = ("username", "password")
    identity_field = "username"


class IsracardProvider(IsraeliCardProvider):
    company = "isracard"
    display_name = "Isracard"
    fields = ("id", "card6Digits", "password")
    identity_field = "id"
