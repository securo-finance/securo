"""What a provider plugin's `sync` returns: records for the bank sync pipeline.

An integration that declares `provides` returns `data` next to `state`:

    {"state": {...}, "data": {"contract": 1, "accounts": [...], "holdings": [...]}}

The models below mirror the provider dataclasses in `app.providers.base`
field for field, so a payload becomes exactly what a native provider would
hand `connection_service.sync_connection`. Everything is checked before
anything is written: an invalid payload is a failed sync, and nothing of it
is kept.

`null` and `[]` differ. A list that is `null` wasn't fetched this run, and
what Securo already has is left alone; `[]` was fetched and is empty, which
lets the pipeline archive holdings that are gone. `data: null` means nothing
was fetched at all (a report still being generated): the plugin's `state` is
kept and the pipeline doesn't run.
"""
from __future__ import annotations

from datetime import date as _date
from decimal import ROUND_HALF_UP, Decimal
from collections.abc import Collection
from urllib.parse import urlsplit
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, ValidationError, model_validator

from app.plugins.manifest import KEY_PATTERN
from app.providers.base import (
    AccountData,
    BillData,
    HoldingData,
    HoldingTradeData,
    ReservedBalanceData,
    TransactionData,
)

CONTRACT_VERSION = 1
MAX_ACCOUNTS = 500
MAX_TRANSACTIONS = 20_000
MAX_BILLS = 2_000
MAX_RESERVED_BALANCES = 2_000
MAX_HOLDINGS = 5_000
MAX_TRADES = 20_000

#: What the author has to know past the cap for a kind whose reported list
#: replaces what is stored: paying the excess over two syncs would lose it.
_OVER_CAP_NOTES = {
    "reserved_balances": (
        ". A reported list is the account's whole set, so it can't be split across syncs;"
        " reserves left out of it are deleted"
    ),
    "holdings": (
        ". A reported list is the whole set, so it can't be split across syncs;"
        " holdings left out of it are archived"
    ),
}

AccountType = Literal["checking", "savings", "credit_card", "loan", "investment", "wallet"]
#: The asset types a holding may name; they pick its icon on the Assets page.
HoldingType = Literal["stock", "etf", "fund", "crypto", "investment"]
Currency = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
ExternalId = Annotated[str, Field(min_length=1, max_length=255)]


def _no_float(value: Any) -> Any:
    # Floats lose cents on the way; amounts travel as strings (or integers).
    if isinstance(value, float):
        raise ValueError("send amounts as strings, not floats")
    return value


# A decimal that fits the column it lands in: so many digits before the point,
# 8 after at most. The columns keep fewer decimals (2 for money, 6 for units
# and prices) and round the rest; a number too large for its column is
# refused here, before anything is written, instead of failing the write.

def _fits(whole: int, places: int):
    """Refuse a number that, rounded to the column's decimals as the database does, no longer fits."""

    def check(value: Decimal) -> Decimal:
        rounded = value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
        if abs(rounded) >= Decimal(10) ** whole:
            raise ValueError(f"too large: at most {whole} digits before the decimal point")
        return value

    return AfterValidator(check)


#: Money: Numeric(15, 2) columns (balances, transactions, bills, cost basis, fees).
Money = Annotated[
    Decimal,
    BeforeValidator(_no_float),
    Field(allow_inf_nan=False, max_digits=13 + 8, decimal_places=8),
    _fits(13, 2),
]
PositiveMoney = Annotated[Money, Field(ge=0)]
#: Units and holding values: Numeric(15, 6) columns (`assets.units`, `asset_values.amount`).
Units = Annotated[
    Decimal,
    BeforeValidator(_no_float),
    Field(allow_inf_nan=False, max_digits=9 + 8, decimal_places=8),
    _fits(9, 6),
]
#: Trade quantities and prices: Numeric(18, 6) columns.
TradeNumber = Annotated[
    Decimal,
    BeforeValidator(_no_float),
    Field(allow_inf_nan=False, max_digits=12 + 8, decimal_places=8, ge=0),
    _fits(12, 6),
]


class PayloadError(ValueError):
    """The plugin returned data Securo can't take. The message names the problem, never the values."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Transaction(_Model):
    external_id: ExternalId
    description: str = Field(min_length=1, max_length=500)
    amount: PositiveMoney
    """Always positive: `type` says which way the money went."""
    date: _date
    type: Literal["debit", "credit"]
    currency: Currency | None = None
    amount_in_account_currency: PositiveMoney | None = None
    provider_category: str | None = Field(default=None, max_length=100)
    status: Literal["posted", "pending"] = "posted"
    payee: str | None = Field(default=None, max_length=200)
    raw_data: dict[str, Any] | None = None
    installment_number: int | None = Field(default=None, ge=1)
    total_installments: int | None = Field(default=None, ge=1)
    installment_total_amount: PositiveMoney | None = None
    installment_purchase_date: _date | None = None
    bill_external_id: ExternalId | None = None

    def to_data(self) -> TransactionData:
        fields = self.model_dump(exclude={"provider_category"})
        return TransactionData(**fields, pluggy_category=self.provider_category)


#: Keys of a bill's `raw_data` the pipeline reads for a native provider (it
#: writes transactions from them). `raw_data` is kept for reference only, so
#: a plugin's records come from the contract's own fields and nowhere else.
_NATIVE_BILL_KEYS = frozenset({"financeCharges"})


class Bill(_Model):
    external_id: ExternalId
    due_date: _date
    total_amount: Money
    currency: Currency
    minimum_payment: PositiveMoney | None = None
    raw_data: dict[str, Any] | None = None

    def to_data(self) -> BillData:
        data = BillData(**self.model_dump())
        if data.raw_data and _NATIVE_BILL_KEYS & data.raw_data.keys():
            data.raw_data = {k: v for k, v in data.raw_data.items() if k not in _NATIVE_BILL_KEYS}
        return data


class ReservedBalance(_Model):
    """Funds an institution holds reserved inside an account (ADR 0022).

    Cofrinhos, "saldo reservado", judicial holds. Shown beside the account's
    balance and never added to it: the provider doesn't guarantee whether the
    balance already includes them. One record per currency; `(external_id,
    currency)` is the identity the sync upserts on.
    """

    external_id: ExternalId
    name: str | None = Field(default=None, max_length=255)
    currency: Currency
    amount: PositiveMoney

    def to_data(self) -> ReservedBalanceData:
        return ReservedBalanceData(**self.model_dump())


class Account(_Model):
    external_id: ExternalId
    name: str = Field(min_length=1, max_length=255)
    type: AccountType
    balance: Money
    currency: Currency
    credit_limit: PositiveMoney | None = None
    statement_close_day: int | None = Field(default=None, ge=1, le=31)
    payment_due_day: int | None = Field(default=None, ge=1, le=31)
    minimum_payment: PositiveMoney | None = None
    card_brand: str | None = Field(default=None, max_length=50)
    card_level: str | None = Field(default=None, max_length=50)
    masked_number: str | None = Field(default=None, max_length=4)
    """The last four characters only, never the full number."""
    shared_balance_group: str | None = Field(default=None, max_length=100)
    stable_external_id: ExternalId | None = None
    institution_external_id: ExternalId | None = None
    institution_name: str | None = Field(default=None, max_length=255)
    institution_logo: str | None = Field(default=None, pattern=KEY_PATTERN)
    """A key of the manifest's `logos`: Securo serves the file from the plugin's package."""
    institution_logo_url: str | None = Field(default=None, max_length=500, pattern=r"^https://[^\s]+$")
    """Or an image on a host the manifest declares in `network`, for a plugin that reaches many institutions."""
    auto_invested_balance: PositiveMoney = Decimal(0)
    reserved_balances: list[ReservedBalance] | None = None
    """Unreported reserves are `None`, which keeps what Securo stored; `[]` says there are none (ADR 0022)."""
    transactions: list[Transaction] | None = None
    bills: list[Bill] | None = None

    @model_validator(mode="after")
    def _unique(self) -> Account:
        _check_unique("transactions", self.transactions)
        _check_unique("bills", self.bills)
        _check_unique("reserved_balances", self.reserved_balances, ("external_id", "currency"))
        return self

    def to_data(self, logos: dict[str, str] | None = None) -> AccountData:
        """The provider dataclass; `logos` maps the manifest's logo keys to where Securo serves them."""
        data = AccountData(
            **self.model_dump(
                exclude={"transactions", "bills", "reserved_balances", "institution_logo"}
            )
        )
        if self.institution_logo:
            data.institution_logo_url = (logos or {}).get(self.institution_logo)
        if self.reserved_balances is not None:
            data.reserved_balances = [item.to_data() for item in self.reserved_balances]
        return data


class Trade(_Model):
    """One buy or sell of a holding. `quantity` is positive; `kind` gives the direction."""

    external_id: ExternalId
    kind: Literal["buy", "sell"]
    date: _date
    quantity: Annotated[TradeNumber, Field(gt=0)]
    price: TradeNumber
    """Per unit, in the asset's currency."""
    fee: PositiveMoney = Decimal(0)

    def to_data(self) -> HoldingTradeData:
        return HoldingTradeData(**self.model_dump())


class Holding(_Model):
    external_id: ExternalId
    name: str = Field(min_length=1, max_length=255)
    type: HoldingType | None = None
    currency: Currency
    current_value: Units
    quantity: Units | None = None
    unit_price: TradeNumber | None = None
    purchase_price: PositiveMoney | None = None
    purchase_date: _date | None = None
    isin: str | None = Field(default=None, pattern=r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
    ticker: str | None = Field(default=None, max_length=32)
    maturity_date: _date | None = None
    is_withdrawn: bool = False
    metadata: dict[str, Any] | None = None
    account_external_id: ExternalId | None = None
    account_name: str | None = Field(default=None, max_length=255)
    trades: list[Trade] | None = None

    @model_validator(mode="after")
    def _unique(self) -> Holding:
        _check_unique("trades", self.trades)
        return self

    def to_data(self) -> HoldingData:
        trades = [trade.to_data() for trade in self.trades] if self.trades is not None else None
        return HoldingData(**self.model_dump(exclude={"trades", "type"}), trades=trades, asset_type=self.type)


class Payload(_Model):
    contract: int
    accounts: list[Account] | None = None
    holdings: list[Holding] | None = None

    @model_validator(mode="after")
    def _checked(self) -> Payload:
        if self.contract != CONTRACT_VERSION:
            raise ValueError(f"contract {self.contract} isn't supported; this Securo takes contract {CONTRACT_VERSION}")
        accounts = self.accounts or []
        holdings = self.holdings or []
        _check_unique("accounts", accounts)
        _check_unique("holdings", holdings)
        counts = {
            "accounts": (len(accounts), MAX_ACCOUNTS),
            "transactions": (sum(len(a.transactions or []) for a in accounts), MAX_TRANSACTIONS),
            "bills": (sum(len(a.bills or []) for a in accounts), MAX_BILLS),
            "reserved_balances": (
                sum(len(a.reserved_balances or []) for a in accounts),
                MAX_RESERVED_BALANCES,
            ),
            "holdings": (len(holdings), MAX_HOLDINGS),
            "trades": (sum(len(h.trades or []) for h in holdings), MAX_TRADES),
        }
        for kind, (count, cap) in counts.items():
            if count > cap:
                raise ValueError(
                    f"{count} {kind} in one reply; the most is {cap}, and a reply over that is"
                    f" refused whole{_OVER_CAP_NOTES.get(kind, '')}"
                )
        return self

    def kinds(self) -> set[str]:
        """The kinds of record this payload carries (fetched, even if empty)."""
        found: set[str] = set()
        if self.accounts is not None:
            found.add("accounts")
            for account in self.accounts:
                if account.transactions is not None:
                    found.add("transactions")
                if account.bills is not None:
                    found.add("bills")
        if self.holdings is not None:
            found.add("holdings")
            if any(h.trades is not None for h in self.holdings):
                found.add("trades")
        return found


def _check_unique(kind: str, records: list | None, keys: tuple[str, ...] = ("external_id",)) -> None:
    seen = [tuple(getattr(record, key) for key in keys) for record in records or []]
    if len(seen) != len(set(seen)):
        raise ValueError(f"{kind}: {' and '.join(keys)} must be unique")


def parse_payload(
    data: Any, provides: list[str], logos: Collection[str] = (), hosts: Collection[str] = ()
) -> Payload | None:
    """The validated payload, or None when the plugin fetched nothing this run.

    Raises `PayloadError` when the data is malformed, too large, carries a
    kind of record the integration doesn't declare in `provides`, names a
    logo that isn't among `logos` (the keys of the manifest's), or points at
    a logo on a host outside `hosts` (the manifest's `network`): showing a
    logo makes every viewer's browser fetch it, so only declared hosts do.
    """
    if data is None:
        return None
    if not isinstance(data, dict):
        raise PayloadError("data must be an object")
    try:
        payload = Payload.model_validate(data)
    except ValidationError as exc:
        raise PayloadError(_describe(exc)) from None
    undeclared = payload.kinds() - set(provides)
    if undeclared:
        raise PayloadError(f"data has {', '.join(sorted(undeclared))}, which the integration doesn't provide")
    for index, account in enumerate(payload.accounts or []):
        if account.institution_logo is not None and account.institution_logo_url is not None:
            raise PayloadError(f"accounts.{index}: name a logo by institution_logo or institution_logo_url, not both")
        if account.institution_logo is not None and account.institution_logo not in logos:
            raise PayloadError(f"accounts.{index}.institution_logo: the manifest's logos have no such key")
        if account.institution_logo_url is not None and not _on_declared_host(account.institution_logo_url, hosts):
            raise PayloadError(f"accounts.{index}.institution_logo_url: its host isn't declared in the manifest's network")
    return payload


def _on_declared_host(url: str, hosts: Collection[str]) -> bool:
    """Whether an https URL points at one of the manifest's `network` hosts (`host` or `host:port`)."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        return False
    host = parts.hostname.lower()
    candidates = {host, f"{host}:443"} if port in (None, 443) else {f"{host}:{port}"}
    return not candidates.isdisjoint(hosts)


def _describe(exc: ValidationError) -> str:
    """Where each problem is and what it is, without the values the plugin sent."""
    problems = []
    for error in exc.errors(include_input=False, include_url=False)[:5]:
        where = ".".join(str(part) for part in error["loc"]) or "data"
        problems.append(f"{where}: {error['msg']}")
    more = exc.error_count() - len(problems)
    if more > 0:
        problems.append(f"and {more} more")
    return "invalid data from the plugin: " + "; ".join(problems)
