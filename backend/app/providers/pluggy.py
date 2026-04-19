import time
from datetime import date
from decimal import Decimal
from typing import Optional

import httpx

from app.core.config import get_settings
from app.providers.base import (
    AccountData,
    BankProvider,
    ConnectionData,
    ConnectTokenData,
    TransactionData,
)

PLUGGY_API_BASE = "https://api.pluggy.ai"


def _parse_day(value) -> Optional[int]:
    """Extract day-of-month from a Pluggy ISO date string (yyyy-mm-dd or datetime)."""
    if not value:
        return None
    try:
        return int(str(value)[8:10])
    except (ValueError, IndexError):
        return None


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
                    "clientSecret": settings.pluggy_client_secret,
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

    def get_oauth_url(self, redirect_uri: str, state: str) -> str:
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

        institution_name = item_data.get("connector", {}).get("name", "Unknown Bank")

        account_list = []
        for acc in accounts_data.get("results", []):
            account_list.append(_build_account_data(acc, self._map_account_type))

        return ConnectionData(
            external_id=item_id,
            institution_name=institution_name,
            credentials={"item_id": item_id},
            accounts=account_list,
        )

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
        page = 1

        async with httpx.AsyncClient(timeout=30.0) as client:
            while True:
                params: dict = {
                    "accountId": account_external_id,
                    "pageSize": 500,
                    "page": page,
                }
                if since:
                    # Filter by Pluggy ingestion time, NOT transaction date.
                    # `from`/`to` filter on `date` (when the txn happened),
                    # which silently drops transactions Pluggy ingests later
                    # but backdates — e.g. credit card bill payments dated
                    # to the bill close, or merchants that settle weeks late.
                    # `createdAtFrom` filters on Pluggy's row creation time,
                    # so any newly-ingested row is fetched regardless of date.
                    params["createdAtFrom"] = since.isoformat()

                resp = await client.get(
                    f"{PLUGGY_API_BASE}/transactions",
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
                        )
                    )

                total_pages = data.get("totalPages", 1)
                if page >= total_pages:
                    break
                page += 1

        return all_transactions

    async def refresh_credentials(self, credentials: dict) -> dict:
        # Pluggy manages API keys at the provider level, not per-connection
        return credentials

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
