import asyncio
import logging
import time
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Optional
from urllib.parse import parse_qs, urlparse

import httpx

from app.core.config import get_settings
from app.providers.base import (
    AccountData,
    BankProvider,
    BillData,
    ConnectionData,
    ConnectTokenData,
    HoldingData,
    RefreshOutcome,
    TransactionData,
    mask_last4,
)
from app.providers.pluggy_constants import pluggy_icon_for_compe

logger = logging.getLogger(__name__)

# How long to wait for Pluggy to finish syncing with the bank before giving up
# and reading whatever Pluggy already has. Pluggy docs describe credential
# connectors completing in tens of seconds; we cap at 90s to keep manual sync
# requests bounded.
PLUGGY_REFRESH_TIMEOUT_SECONDS = 90
PLUGGY_REFRESH_POLL_INTERVAL = 3.0

# Item statuses we treat as terminal when polling /items/{id}.
# https://docs.pluggy.ai/docs/item-lifecycle
_PLUGGY_TERMINAL_STATUSES = {
    "UPDATED",
    "LOGIN_ERROR",
    "OUTDATED",
    "WAITING_USER_INPUT",
}

# Pluggy 400 ``codeDescription`` values that genuinely require the user to
# re-authenticate through the widget (MFA path). Every other 400 — items
# under MeuPluggy that can't be PATCHed, transient rate limits, etc. — is
# treated as a soft failure: we fall back to reading whatever Pluggy
# already has rather than punishing the user with a reconnect prompt.
_PLUGGY_REFRESH_USER_ACTION_CODES = {
    "MFA_PARAMERTER_WAS_ALREADY_USED_ERROR",
    "CONNECTOR_REQUIRED_PARAMETER_VALIDATION_ERROR",
}

PLUGGY_API_BASE = "https://api.pluggy.ai"


def _compe_from_transfer_number(transfer_number) -> Optional[str]:
    """Extract the 3-digit COMPE bank code from a Pluggy ``transferNumber``.

    Format is ``"<compe>/<branch>/<account>"`` (e.g. ``"260/0001/06809695-5"``).
    Returns the zero-padded code, or None when absent/unparseable.
    """
    if not isinstance(transfer_number, str) or "/" not in transfer_number:
        return None
    code = transfer_number.split("/", 1)[0].strip()
    return code.zfill(3) if code.isdigit() else None


def _resolve_connector_logo(connector: dict, accounts: list[dict]) -> Optional[str]:
    """Institution logo URL for a Pluggy connection, or None.

    Real connectors expose the bank logo directly in ``imageUrl`` (e.g.
    Nubank -> ``.../212.svg``). The demo "MeuPluggy" connector returns the
    generic ``sandbox.svg`` placeholder instead — in that case fall back to
    the real bank's icon resolved from an account's COMPE code. If neither is
    available, return None so the frontend shows the account-type icon.
    """
    image = connector.get("imageUrl")
    if image and not image.rstrip("/").endswith("/sandbox.svg"):
        return image
    for acc in accounts:
        compe = _compe_from_transfer_number((acc.get("bankData") or {}).get("transferNumber"))
        icon = pluggy_icon_for_compe(compe)
        if icon:
            return icon
    return None


def _parse_day(value) -> Optional[int]:
    """Extract day-of-month from a Pluggy ISO date string (yyyy-mm-dd or datetime)."""
    if not value:
        return None
    try:
        return int(str(value)[8:10])
    except (ValueError, IndexError):
        return None


def _decimal_or_none(value) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (ValueError, TypeError, InvalidOperation):
        return None


def _date_or_none(value) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


# Fields from Pluggy /investments we promote to HoldingData top-level.
# Everything else is preserved verbatim in `metadata` so we don't lose
# information we might want later (status, rates, issuer, institution,
# nested `metadata` for pensions, etc.) without tying our schema to Pluggy.
_HOLDING_PROMOTED_KEYS = {
    "id", "balance", "currencyCode", "quantity", "value",
    "amountOriginal", "dueDate", "isin", "issueDate",
}

# Pluggy `type` values where `issueDate` equals the user's purchase date.
# For EQUITY/MUTUAL_FUND/ETF, issueDate is when the fund/stock was created
# — potentially years before the user bought in. Using it as purchase_date
# would produce badly-wrong evolution charts, so we skip it for those.
_ISSUE_DATE_IS_PURCHASE_DATE = {"FIXED_INCOME", "COE"}


def _build_holding_data(inv: dict) -> HoldingData:
    """Map a Pluggy investment payload to HoldingData.

    `balance` is chosen over `amount` for current value: Pluggy documents
    balance as net of taxes/fees (what a user could actually withdraw),
    while amount is gross. The gross figure is kept in metadata for users
    who care about the distinction.
    """
    current_value = _decimal_or_none(inv.get("balance")) or Decimal("0")
    pluggy_status = (inv.get("status") or "").upper()
    pluggy_type = (inv.get("type") or "").upper()

    purchase_date: Optional[date] = None
    if pluggy_type in _ISSUE_DATE_IS_PURCHASE_DATE:
        purchase_date = _date_or_none(inv.get("issueDate"))

    metadata = {k: v for k, v in inv.items() if k not in _HOLDING_PROMOTED_KEYS}

    return HoldingData(
        external_id=str(inv["id"]),
        name=inv.get("name") or "Investment",
        currency=inv.get("currencyCode") or "BRL",
        current_value=current_value,
        quantity=_decimal_or_none(inv.get("quantity")),
        unit_price=_decimal_or_none(inv.get("value")),
        purchase_price=_decimal_or_none(inv.get("amountOriginal")),
        purchase_date=purchase_date,
        isin=inv.get("isin") or None,
        maturity_date=_date_or_none(inv.get("dueDate")),
        is_withdrawn=pluggy_status == "TOTAL_WITHDRAWAL",
        metadata=metadata or None,
    )


def _build_bill_data(raw: dict) -> Optional[BillData]:
    """Map a Pluggy bill payload to BillData.

    Returns None when required anchors (id, dueDate, totalAmount) are missing
    or unparseable — the caller drops those rows rather than failing the whole
    sync. Negative `totalAmount` is preserved (not abs'd): a negative bill
    means the bank owes the user money, and silently flipping the sign would
    hide that fact in reports.

    Provider-specific extras (financeCharges, payments, allowsInstallments,
    etc.) survive in `raw_data` and can be promoted to first-class columns
    later if/when the read path actually needs them.
    """
    bill_id = raw.get("id")
    due_date = _date_or_none(raw.get("dueDate"))
    total_amount = _decimal_or_none(raw.get("totalAmount"))
    if not bill_id or due_date is None or total_amount is None:
        return None

    return BillData(
        external_id=str(bill_id),
        due_date=due_date,
        total_amount=total_amount,
        currency=raw.get("totalAmountCurrencyCode") or "BRL",
        minimum_payment=_decimal_or_none(raw.get("minimumPaymentAmount")),
        raw_data=raw,
    )


def _build_account_data(acc: dict, type_mapper) -> AccountData:
    """Map a Pluggy account payload to AccountData, including creditData when present."""
    account_type = type_mapper(acc.get("type", ""))
    credit_data = acc.get("creditData") or {}

    credit_limit: Optional[Decimal] = None
    statement_close_day: Optional[int] = None
    payment_due_day: Optional[int] = None
    minimum_payment: Optional[Decimal] = None
    card_brand: Optional[str] = None
    card_level: Optional[str] = None

    if account_type == "credit_card" and credit_data:
        raw_limit = credit_data.get("creditLimit")
        if raw_limit is not None:
            credit_limit = Decimal(str(raw_limit))
        statement_close_day = _parse_day(credit_data.get("balanceCloseDate"))
        payment_due_day = _parse_day(credit_data.get("balanceDueDate"))
        raw_min = credit_data.get("minimumPayment")
        if raw_min is not None:
            minimum_payment = Decimal(str(raw_min))
        card_brand = credit_data.get("brand") or None
        card_level = credit_data.get("level") or None

    return AccountData(
        external_id=acc["id"],
        name=acc["name"],
        type=account_type,
        balance=Decimal(str(acc.get("balance", 0))),
        currency=acc.get("currencyCode", "USD"),
        credit_limit=credit_limit,
        statement_close_day=statement_close_day,
        payment_due_day=payment_due_day,
        minimum_payment=minimum_payment,
        card_brand=card_brand,
        card_level=card_level,
        # Brazil has no IBAN; Pluggy's `number` is the branch/account number
        # (or the card number for credit cards), which serves the same purpose.
        masked_number=mask_last4(acc.get("number")),
    )


class PluggyProvider(BankProvider):
    """Pluggy (MeuPluggy) open finance provider.

    Uses the Pluggy Connect Widget flow:
    1. Backend creates a connect token for the frontend widget
    2. Widget handles bank selection, login, and MFA
    3. Widget returns an Item ID which is used as the connection identifier
    """

    _api_key: Optional[str] = None
    _api_key_expires_at: float = 0

    @property
    def name(self) -> str:
        return "pluggy"

    @property
    def flow_type(self) -> str:
        return "widget"

    async def _ensure_api_key(self) -> str:
        """Get a valid API key, refreshing if expired or about to expire (<5min remaining)."""
        now = time.time()
        if self._api_key and (self._api_key_expires_at - now) > 300:
            return self._api_key

        settings = get_settings()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{PLUGGY_API_BASE}/auth",
                json={
                    "clientId": settings.pluggy_client_id,
                    "clientSecret": settings.pluggy_client_secret.get_secret_value(),
                },
            )
            resp.raise_for_status()
            data = resp.json()

        PluggyProvider._api_key = data["apiKey"]
        # Pluggy API keys last 2 hours
        PluggyProvider._api_key_expires_at = now + 7200
        return PluggyProvider._api_key

    async def _headers(self) -> dict:
        api_key = await self._ensure_api_key()
        return {
            "X-API-KEY": api_key,
            "Content-Type": "application/json",
        }

    async def create_connect_token(
        self, client_user_id: str, item_id: str | None = None
    ) -> ConnectTokenData:
        """Create a connect token for the Pluggy Connect Widget.

        When item_id is provided, the widget opens in update mode for re-authentication.
        """
        headers = await self._headers()
        body: dict = {"clientUserId": client_user_id}
        if item_id:
            body["itemId"] = item_id
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{PLUGGY_API_BASE}/connect_token",
                headers=headers,
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()
        return ConnectTokenData(access_token=data["accessToken"])

    async def get_oauth_url(
        self,
        redirect_uri: str,
        state: str,
        flow_params: Optional[dict] = None,
    ) -> str:
        raise NotImplementedError("Pluggy uses widget flow, not OAuth redirect")

    async def handle_oauth_callback(self, code: str) -> ConnectionData:
        """Handle widget callback. The 'code' parameter is the Pluggy Item ID."""
        item_id = code
        headers = await self._headers()

        async with httpx.AsyncClient(timeout=30.0) as client:
            # Fetch item details
            item_resp = await client.get(
                f"{PLUGGY_API_BASE}/items/{item_id}",
                headers=headers,
            )
            item_resp.raise_for_status()
            item_data = item_resp.json()

            # Fetch accounts for this item
            accounts_resp = await client.get(
                f"{PLUGGY_API_BASE}/accounts",
                headers=headers,
                params={"itemId": item_id},
            )
            accounts_resp.raise_for_status()
            accounts_data = accounts_resp.json()

        connector = item_data.get("connector", {})
        institution_name = connector.get("name", "Unknown Bank")

        raw_accounts = accounts_data.get("results", [])
        account_list = []
        for acc in raw_accounts:
            account_list.append(_build_account_data(acc, self._map_account_type))

        return ConnectionData(
            external_id=item_id,
            institution_name=institution_name,
            credentials={"item_id": item_id},
            accounts=account_list,
            logo_url=_resolve_connector_logo(connector, raw_accounts),
        )

    async def get_institution_logo(self, credentials: dict) -> Optional[str]:
        item_id = credentials["item_id"]
        headers = await self._headers()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{PLUGGY_API_BASE}/items/{item_id}", headers=headers
            )
            resp.raise_for_status()
            connector = resp.json().get("connector", {})
            # The connector logo may be the demo placeholder; fetch accounts so
            # the COMPE-code fallback can recover the real bank icon.
            accts_resp = await client.get(
                f"{PLUGGY_API_BASE}/accounts", headers=headers,
                params={"itemId": item_id},
            )
            accts_resp.raise_for_status()
            raw_accounts = accts_resp.json().get("results", [])
        return _resolve_connector_logo(connector, raw_accounts)

    async def get_accounts(self, credentials: dict) -> list[AccountData]:
        item_id = credentials["item_id"]
        headers = await self._headers()

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{PLUGGY_API_BASE}/accounts",
                headers=headers,
                params={"itemId": item_id},
            )
            resp.raise_for_status()
            data = resp.json()

        accounts = []
        for acc in data.get("results", []):
            accounts.append(_build_account_data(acc, self._map_account_type))
        return accounts

    async def get_transactions(
        self, credentials: dict, account_external_id: str,
        since: Optional[date] = None, payee_source: str = "auto",
    ) -> list[TransactionData]:
        headers = await self._headers()
        all_transactions: list[TransactionData] = []
        after: Optional[str] = None

        async with httpx.AsyncClient(timeout=30.0) as client:
            while True:
                # v2 cursor-based listing. Pluggy deprecated v1 GET
                # /transactions — it returns 410 ENDPOINT_DEPRECATED on newer
                # API keys (rolled out per key). /v2/transactions pages via an
                # opaque `after` cursor (returned in `next`) instead of
                # page/pageSize.
                params: dict = {"accountId": account_external_id}
                if since:
                    # Filter by Pluggy ingestion time, NOT transaction date.
                    # `dateFrom`/`dateTo` filter on `date` (when the txn
                    # happened), which silently drops transactions Pluggy
                    # ingests later but backdates — e.g. credit card bill
                    # payments dated to the bill close, or merchants that
                    # settle weeks late. `createdAtFrom` filters on Pluggy's
                    # row creation time, so any newly-ingested row is fetched
                    # regardless of date. v2 still supports it.
                    params["createdAtFrom"] = since.isoformat()
                if after:
                    params["after"] = after

                resp = await client.get(
                    f"{PLUGGY_API_BASE}/v2/transactions",
                    headers=headers,
                    params=params,
                )
                resp.raise_for_status()
                data = resp.json()

                results = data.get("results", [])
                if not results:
                    break

                for txn in results:
                    amount_raw = txn.get("amount", 0)
                    amount = Decimal(str(abs(amount_raw)))

                    # Use Pluggy's explicit type field when available
                    pluggy_type = txn.get("type", "").upper()
                    if pluggy_type == "DEBIT":
                        txn_type = "debit"
                    elif pluggy_type == "CREDIT":
                        txn_type = "credit"
                    else:
                        txn_type = "credit" if amount_raw >= 0 else "debit"

                    txn_date = date.fromisoformat(txn["date"][:10])

                    # Pending vs booked status
                    status = "pending" if txn.get("status") == "PENDING" else "posted"

                    # Smart payee extraction (merchant → payment data → None)
                    payee = self._extract_payee(txn, txn_type, payee_source)

                    # Bank-provided conversion for international transactions
                    amt_in_acct = txn.get("amountInAccountCurrency")
                    amount_in_account_currency = (
                        Decimal(str(abs(amt_in_acct))) if amt_in_acct is not None else None
                    )

                    # Installment metadata from creditCardMetadata (parcelamento).
                    # Pluggy reports installment_number, total, original amount and
                    # purchase date on each charge for CC connectors that support it.
                    cc_meta = txn.get("creditCardMetadata") or {}
                    inst_number = cc_meta.get("installmentNumber")
                    inst_total = cc_meta.get("totalInstallments")
                    inst_total_amount = cc_meta.get("totalAmount")
                    inst_purchase_date_raw = cc_meta.get("purchaseDate")
                    inst_purchase_date: Optional[date] = None
                    if inst_purchase_date_raw:
                        try:
                            inst_purchase_date = date.fromisoformat(str(inst_purchase_date_raw)[:10])
                        except ValueError:
                            inst_purchase_date = None
                    inst_total_amount_dec = (
                        Decimal(str(abs(inst_total_amount)))
                        if inst_total_amount is not None
                        else None
                    )
                    # Bill linkage: Pluggy stamps each charge with the id of
                    # the bill it lands in. The sync layer resolves this to a
                    # credit_card_bills FK; we just capture the string here.
                    bill_external_id_raw = cc_meta.get("billId")
                    bill_external_id = (
                        str(bill_external_id_raw) if bill_external_id_raw else None
                    )

                    all_transactions.append(
                        TransactionData(
                            external_id=txn["id"],
                            description=txn.get("description", ""),
                            amount=amount,
                            date=txn_date,
                            type=txn_type,
                            currency=txn.get("currencyCode"),
                            amount_in_account_currency=amount_in_account_currency,
                            pluggy_category=txn.get("category"),
                            status=status,
                            payee=payee,
                            raw_data=txn,
                            installment_number=inst_number if isinstance(inst_number, int) else None,
                            total_installments=inst_total if isinstance(inst_total, int) else None,
                            installment_total_amount=inst_total_amount_dec,
                            installment_purchase_date=inst_purchase_date,
                            bill_external_id=bill_external_id,
                        )
                    )

                next_after = self._extract_after(data.get("next"))
                if not next_after or next_after == after:
                    break
                after = next_after

        return all_transactions

    @staticmethod
    def _extract_after(next_value: Optional[str]) -> Optional[str]:
        """Pull the `after` cursor out of v2's `next` field.

        Pluggy returns `next` as a URL carrying the cursor
        (".../v2/transactions?accountId=...&after=<cursor>"), or null on the
        last page. Returns None when there's no further page so the caller
        stops — and never loops on a malformed value.
        """
        if not next_value:
            return None
        after_vals = parse_qs(urlparse(next_value).query).get("after")
        return after_vals[0] if after_vals else None

    async def refresh_credentials(self, credentials: dict) -> dict:
        # Pluggy manages API keys at the provider level, not per-connection
        return credentials

    async def trigger_refresh(self, credentials: dict) -> RefreshOutcome:
        """Trigger ``PATCH /items/{id}`` and poll the item until it leaves
        the ``UPDATING`` state.

        See https://docs.pluggy.ai/docs/item-lifecycle for the state machine.
        Pluggy's own auto-sync runs daily; this triggers an on-demand pull so
        Securo reads what the bank shows now, not what Pluggy last cached.
        """
        item_id = credentials.get("item_id") if credentials else None
        if not item_id:
            return "skipped"

        headers = await self._headers()
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                trigger_resp = await client.patch(
                    f"{PLUGGY_API_BASE}/items/{item_id}",
                    headers=headers,
                    json={},
                )
            except httpx.HTTPError:
                logger.warning("Pluggy refresh PATCH failed for item %s", item_id)
                return "failed"

            if trigger_resp.status_code == 400:
                # Pluggy returns 400 for several distinct cases. Only treat
                # the explicit MFA-related codes as "needs_user_action" — the
                # others (MeuPluggy-managed items that can't be PATCHed,
                # consecutive-failure backoffs, etc.) are transient or
                # outside the user's control. For those, fall through to a
                # read of cached data.
                body: dict = {}
                try:
                    body = trigger_resp.json()
                except ValueError:
                    pass
                code_desc = str(body.get("codeDescription") or "").upper()
                logger.info(
                    "Pluggy refresh rejected for item %s (code=%s): %s",
                    item_id,
                    code_desc or "<no code>",
                    trigger_resp.text[:200],
                )
                if code_desc in _PLUGGY_REFRESH_USER_ACTION_CODES:
                    return "needs_user_action"
                return "failed"
            if trigger_resp.status_code >= 400:
                logger.warning(
                    "Pluggy refresh returned %s for item %s: %s",
                    trigger_resp.status_code,
                    item_id,
                    trigger_resp.text[:200],
                )
                return "failed"

            deadline = time.monotonic() + PLUGGY_REFRESH_TIMEOUT_SECONDS
            while time.monotonic() < deadline:
                await asyncio.sleep(PLUGGY_REFRESH_POLL_INTERVAL)
                try:
                    status_resp = await client.get(
                        f"{PLUGGY_API_BASE}/items/{item_id}", headers=headers
                    )
                except httpx.HTTPError:
                    continue  # transient — retry until deadline
                if status_resp.status_code >= 400:
                    logger.warning(
                        "Pluggy item poll returned %s for %s",
                        status_resp.status_code,
                        item_id,
                    )
                    return "failed"

                item = status_resp.json() or {}
                status = (item.get("status") or "").upper()
                if status == "UPDATING":
                    continue
                if status not in _PLUGGY_TERMINAL_STATUSES:
                    # Unknown status — be conservative.
                    return "failed"
                if status == "UPDATED":
                    return "refreshed"
                if status == "WAITING_USER_INPUT":
                    return "needs_user_action"
                if status == "LOGIN_ERROR":
                    return "needs_user_action"
                # OUTDATED: previous sync failed but credentials were valid.
                # Surfacing this as a hard error would hide otherwise-readable
                # cached data, so treat as a soft failure and read what we have.
                return "failed"

            logger.info(
                "Pluggy refresh for item %s timed out after %ss; "
                "proceeding with cached data",
                item_id,
                PLUGGY_REFRESH_TIMEOUT_SECONDS,
            )
            return "failed"

    async def get_holdings(self, credentials: dict) -> list[HoldingData]:
        """Fetch investment holdings from Pluggy's /investments endpoint.

        /accounts only returns BANK/CREDIT types — brokerage positions,
        fixed income, funds, pensions, etc. live under /investments and
        come back with richer fields (quantity, unit price, profit,
        maturity). We normalize to HoldingData; the rest goes into
        `metadata` so downstream code doesn't leak Pluggy specifics.
        """
        item_id = credentials["item_id"]
        headers = await self._headers()
        holdings: list[HoldingData] = []
        page = 1

        async with httpx.AsyncClient(timeout=30.0) as client:
            while True:
                resp = await client.get(
                    f"{PLUGGY_API_BASE}/investments",
                    headers=headers,
                    params={"itemId": item_id, "pageSize": 500, "page": page},
                )
                resp.raise_for_status()
                data = resp.json()

                results = data.get("results", [])
                for inv in results:
                    holdings.append(_build_holding_data(inv))

                total_pages = data.get("totalPages", 1)
                if page >= total_pages or not results:
                    break
                page += 1

        return holdings

    async def get_bills(self, credentials: dict, account_external_id: str) -> list[BillData]:
        """Fetch credit-card bills from Pluggy /bills.

        Pluggy only exposes /bills on Regulado (Open Finance) connections.
        For non-regulated connectors the request returns 4xx — we let the
        error propagate so the sync layer can decide whether to fall back
        to locally-computed cycle math (compute_effective_date).
        """
        headers = await self._headers()
        bills: list[BillData] = []
        page = 1

        async with httpx.AsyncClient(timeout=30.0) as client:
            while True:
                resp = await client.get(
                    f"{PLUGGY_API_BASE}/bills",
                    headers=headers,
                    params={
                        "accountId": account_external_id,
                        "pageSize": 500,
                        "page": page,
                    },
                )
                resp.raise_for_status()
                data = resp.json()

                results = data.get("results", [])
                for raw in results:
                    bill = _build_bill_data(raw)
                    if bill is not None:
                        bills.append(bill)

                total_pages = data.get("totalPages", 1)
                if page >= total_pages or not results:
                    break
                page += 1

        return bills

    @staticmethod
    def _extract_payee(txn: dict, txn_type: str, payee_source: str = "auto") -> Optional[str]:
        """Extract payee name based on configured source."""
        if payee_source == "none":
            return None
        if payee_source == "description":
            return txn.get("description")
        if payee_source == "merchant":
            merchant = txn.get("merchant")
            if merchant:
                return merchant.get("name") or merchant.get("businessName")
            return None
        if payee_source == "payment_data":
            payment_data = txn.get("paymentData")
            if not payment_data:
                return None
            if txn_type == "debit":
                receiver = payment_data.get("receiver")
                if receiver:
                    return receiver.get("name") or (receiver.get("documentNumber") or {}).get("value")
            else:
                payer = payment_data.get("payer")
                if payer:
                    return payer.get("name") or (payer.get("documentNumber") or {}).get("value")
            return None

        # "auto" — original priority chain: merchant > payment_data > None
        merchant = txn.get("merchant")
        if merchant:
            name = merchant.get("name") or merchant.get("businessName")
            if name:
                return name

        payment_data = txn.get("paymentData")
        if payment_data:
            if txn_type == "debit":
                receiver = payment_data.get("receiver")
                if receiver:
                    name = receiver.get("name")
                    if name:
                        return name
                    doc = receiver.get("documentNumber")
                    if doc and doc.get("value"):
                        return doc["value"]
            else:
                payer = payment_data.get("payer")
                if payer:
                    name = payer.get("name")
                    if name:
                        return name
                    doc = payer.get("documentNumber")
                    if doc and doc.get("value"):
                        return doc["value"]

        return None

    @staticmethod
    def _map_account_type(pluggy_type: str) -> str:
        mapping = {
            "BANK": "checking",
            "CREDIT": "credit_card",
            "SAVINGS": "savings",
        }
        return mapping.get(pluggy_type.upper(), "checking")
