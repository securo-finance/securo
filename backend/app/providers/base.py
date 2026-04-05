from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional


@dataclass
class AccountData:
    external_id: str
    name: str
    type: str  # checking, savings, credit_card
    balance: Decimal
    currency: str


@dataclass
class TransactionData:
    external_id: str
    description: str
    amount: Decimal
    date: date
    type: str  # debit, credit
    currency: Optional[str] = None  # ISO currency code (e.g. BRL, USD)
    amount_in_account_currency: Optional[Decimal] = None  # Bank-provided conversion for intl txns
    pluggy_category: Optional[str] = None
    status: str = "posted"  # posted, pending
    payee: Optional[str] = None
    bill_id: Optional[str] = None
    raw_data: Optional[dict] = None


@dataclass
class ConnectionData:
    external_id: str
    institution_name: str
    credentials: dict
    accounts: list[AccountData]


@dataclass
class ConnectTokenData:
    access_token: str


@dataclass
class BillData:
    id: str
    due_date: date
    total_amount: Decimal
    currency_code: str


class FxRateProvider(ABC):
    """Abstract interface for FX rate providers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique provider identifier (e.g. 'openexchangerates')."""
        ...

    @abstractmethod
    async def fetch_latest(self) -> dict[str, Decimal]:
        """Return {currency_code: rate_vs_USD} for latest rates."""
        ...

    @abstractmethod
    async def fetch_historical(self, target_date: date) -> dict[str, Decimal]:
        """Return rates for a specific date."""
        ...


class BankProvider(ABC):
    """Abstract interface for open finance integrations.

    Implement this for each provider (Pluggy, Belvo, etc.)
    to enable bank account syncing via OAuth or widget flow.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique provider identifier (e.g. 'pluggy', 'belvo')."""
        ...

    @property
    def flow_type(self) -> str:
        """Connection flow type: 'oauth' for redirect-based, 'widget' for embedded widget."""
        return "oauth"

    async def create_connect_token(
        self, client_user_id: str, item_id: str | None = None
    ) -> ConnectTokenData:
        """Create a connect token for widget-based flows. Override in widget providers."""
        raise NotImplementedError(f"{self.name} does not support widget connect tokens")

    @abstractmethod
    def get_oauth_url(self, redirect_uri: str, state: str) -> str:
        """Generate OAuth URL for user to authorize."""
        ...

    @abstractmethod
    async def handle_oauth_callback(self, code: str) -> ConnectionData:
        """Exchange OAuth code for access token and fetch initial data."""
        ...

    @abstractmethod
    async def get_accounts(self, credentials: dict) -> list[AccountData]:
        """Fetch accounts for a connection."""
        ...

    @abstractmethod
    async def get_bills(self, credentials: dict, account_external_id: str) -> list[BillData]:
        """Fetch credit-card bills for an account."""
        ...

    @abstractmethod
    async def get_transactions(
        self, credentials: dict, account_external_id: str,
        since: Optional[date] = None, payee_source: str = "auto",
    ) -> list[TransactionData]:
        """Fetch transactions for an account."""
        ...

    @abstractmethod
    async def refresh_credentials(self, credentials: dict) -> dict:
        """Refresh access token if needed."""
        ...
