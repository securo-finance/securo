"""A provider plugin's payload, served to the bank sync pipeline as a provider.

`connection_service.sync_connection` reads from a `BankProvider`. For a
plugin, the fetching already happened in the runner: this adapter hands the
pipeline slices of the validated payload, so a plugin's records go through
exactly the code a native provider's do.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from app.plugins.contract import Payload
from app.providers.base import (
    AccountData,
    BankProvider,
    BillData,
    ConnectionData,
    HoldingData,
    TransactionData,
)

#: A bank connection whose provider starts with this is synced by a plugin.
PLUGIN_PROVIDER_PREFIX = "plugin:"


def provider_name(plugin_id: str) -> str:
    return f"{PLUGIN_PROVIDER_PREFIX}{plugin_id}"


def is_plugin_provider(name: str | None) -> bool:
    return bool(name) and str(name).startswith(PLUGIN_PROVIDER_PREFIX)


class PluginPayloadProvider(BankProvider):
    """Serves one payload. Credentials stay sealed with the plugin connection."""

    def __init__(self, name: str, payload: Payload) -> None:
        self._name = name
        self._accounts = payload.accounts or []
        self._by_id = {account.external_id: account for account in self._accounts}
        self._holdings = payload.holdings
        # Holdings that weren't fetched this run are left alone by the pipeline.
        self.holdings_fetched = payload.holdings is not None
        # Accounts whose transactions weren't fetched this run: the pipeline
        # treats them as a partial feed (no phantom cleanup, `last_sync_at`
        # stays), as for a native fetch cut short.
        self.incomplete_account_ids: set[str] = {
            account.external_id for account in self._accounts if account.transactions is None
        }

    @property
    def name(self) -> str:
        return self._name

    async def get_oauth_url(self, redirect_uri: str, state: str, flow_params: Optional[dict] = None) -> str:
        raise NotImplementedError("a plugin connection signs in through its plugin")

    async def handle_oauth_callback(self, code: str) -> ConnectionData:
        raise NotImplementedError("a plugin connection signs in through its plugin")

    async def refresh_credentials(self, credentials: dict) -> dict:
        return credentials

    async def get_institution_logo(self, credentials: dict) -> Optional[str]:
        # The connection shows the institution its accounts name, as native ones do.
        return next((a.institution_logo_url for a in self._accounts if a.institution_logo_url), None)

    async def get_accounts(self, credentials: dict) -> list[AccountData]:
        return [account.to_data() for account in self._accounts]

    async def get_transactions(
        self,
        credentials: dict,
        account_external_id: str,
        since: Optional[date] = None,
        payee_source: str = "auto",
    ) -> list[TransactionData]:
        # The plugin already chose its window; `since` was passed to it.
        account = self._by_id.get(account_external_id)
        if account is None or account.transactions is None:
            return []
        transactions = [t.to_data() for t in account.transactions]
        if payee_source == "none":
            for transaction in transactions:
                transaction.payee = None
        return transactions

    async def get_bills(self, credentials: dict, account_external_id: str) -> list[BillData]:
        account = self._by_id.get(account_external_id)
        return [b.to_data() for b in account.bills or []] if account else []

    async def get_holdings(self, credentials: dict) -> list[HoldingData]:
        return [holding.to_data() for holding in self._holdings or []]
