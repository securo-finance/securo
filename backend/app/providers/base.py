import hashlib
import hmac
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Literal, Optional

from app.core.config import Settings


# Outcome of asking a provider to pull fresh data from the underlying institution
# before we read accounts/transactions.
#
# - "refreshed":         provider successfully synced; safe to read.
# - "skipped":           provider has no on-demand refresh (default for most providers).
# - "needs_user_action": provider needs the user to act (MFA, expired credentials).
#                        Caller should mark the connection as needing reconnection
#                        and skip the read pass.
# - "failed":            transient error (timeout, rate limit). Caller may proceed
#                        with a stale read; we don't punish the user for it.
RefreshOutcome = Literal["refreshed", "skipped", "needs_user_action", "failed"]


def default_oauth_redirect_uri() -> str:
    """The OAuth callback URL implied by where the frontend is served.

    Providers fall back to this when their own ``*_OAUTH_REDIRECT_URI`` is
    unset, so a deployment on a custom port or domain works without setting one
    variable per provider. Deriving it here rather than in Compose also keeps
    the compose files free of nested ``${VAR:-${OTHER:-x}}`` interpolation,
    which podman-compose cannot parse (containers/podman-compose#1064).
    """
    from app.core.config import get_settings

    return f"{get_settings().frontend_url.rstrip('/')}/oauth/callback"


def mask_last4(value: Optional[str]) -> Optional[str]:
    """Reduce a bank-assigned account identifier to its last four characters.

    Providers expose different identifiers for the same purpose: an IBAN
    (Enable Banking), a branch/account number (Pluggy), a card number. They all
    serve one job here, telling two same-named accounts apart, so we normalize
    them to a single shape and deliberately keep only the tail. Storing the full
    identifier would put a payable bank reference in the database for no gain.

    Separators (spaces, dashes) are stripped first, since IBANs are commonly
    formatted in groups of four. Returns None when the source is missing or too
    short to mask, so callers never render a partial identifier.
    """
    if not value:
        return None
    cleaned = "".join(ch for ch in str(value) if ch.isalnum())
    if len(cleaned) < 4:
        return None
    return cleaned[-4:]


_UNSPECIFIED_SUB_TAG = "0" * 16


def _fingerprint_key_tag(secret: str) -> str:
    """Short 8-hex identifier for the SECRET_KEY that keyed a v2 fingerprint."""
    return hmac.new(
        secret.encode("utf-8"),
        b"account-fingerprint-kid:v2",
        hashlib.sha256,
    ).hexdigest()[:8]


def _compute_account_fingerprint(
    secret: str,
    provider_clean: str,
    inst_clean: str,
    cleaned: str,
    sub_inst_clean: Optional[str] = None,
) -> str:
    """Compute a 64-hex key-tagged fingerprint: ``key_tag(8) + base(40) + sub(16)``.

    The 40-hex (160-bit) ``base`` HMAC binds ``(provider, institution, account)``
    while the trailing 16-hex (64-bit) ``sub`` HMAC binds an optional
    sub-institution (e.g. a Pluggy COMPE code within a multi-institution
    connector), or ``"0" * 16`` when absent. That layout lets two feeds with
    different known sub-institutions conflict while a feed that omits optional
    sub-institution metadata still matches one that carries it.
    """
    base_msg = (
        f"account-fingerprint:v2:{provider_clean}:{inst_clean}:{cleaned}"
    ).encode("utf-8")
    base_digest = hmac.new(secret.encode("utf-8"), base_msg, hashlib.sha256).hexdigest()[:40]
    if sub_inst_clean:
        sub_msg = (
            f"account-fingerprint-sub:v2:{provider_clean}:{inst_clean}:{sub_inst_clean}:{cleaned}"
        ).encode("utf-8")
        sub_digest = hmac.new(secret.encode("utf-8"), sub_msg, hashlib.sha256).hexdigest()[:16]
        if sub_digest == _UNSPECIFIED_SUB_TAG:
            sub_digest = f"{sub_digest[:-1]}1"
    else:
        sub_digest = _UNSPECIFIED_SUB_TAG
    return f"{_fingerprint_key_tag(secret)}{base_digest}{sub_digest}"


def fingerprint_uses_current_key(fingerprint: Optional[str]) -> bool:
    """True when ``fingerprint`` carries the current SECRET_KEY's 8-hex key tag."""
    if not fingerprint or len(fingerprint) != 64:
        return False
    from app.core.config import get_settings

    secret = get_settings().secret_key.get_secret_value()
    if not secret:
        return False
    return hmac.compare_digest(fingerprint[:8], _fingerprint_key_tag(secret))


def fingerprint_uses_fallback_key(fingerprint: Optional[str]) -> bool:
    """True when ``fingerprint`` carries the 8-hex key tag of a configured fallback key."""
    if not fingerprint or len(fingerprint) != 64:
        return False
    from app.core.config import get_settings

    settings = get_settings()
    current = settings.secret_key.get_secret_value()
    tag = fingerprint[:8]
    for fb in dict.fromkeys(settings.secret_key_fallbacks):
        if fb and fb != current and hmac.compare_digest(tag, _fingerprint_key_tag(fb)):
            return True
    return False


def _native_fingerprints_compatible(fp_a: str, fp_b: str) -> bool:
    """True when two 64-hex key-tagged fingerprints refer to the same account.

    Matches exact digests, or matching key-tag + 160-bit base HMAC when at
    least one side has an unspecified sub-institution tag (``"0" * 16``). Two
    different non-zero sub-institution tags (e.g. distinct COMPE codes under
    one Pluggy connector) never match.
    """
    if hmac.compare_digest(fp_a, fp_b):
        return True
    if len(fp_a) != 64 or len(fp_b) != 64:
        return False
    if not hmac.compare_digest(fp_a[:48], fp_b[:48]):
        return False
    return fp_a[48:] == _UNSPECIFIED_SUB_TAG or fp_b[48:] == _UNSPECIFIED_SUB_TAG


def fingerprints_match(
    stored_fp: Optional[str],
    incoming_fp: Optional[str],
    *,
    fallback_fps: Sequence[str] = (),
) -> bool:
    """True when ``stored_fp`` matches ``incoming_fp`` or any fallback-key digest."""
    if not stored_fp or (not incoming_fp and not fallback_fps):
        return False
    for candidate in (incoming_fp, *fallback_fps):
        if candidate and _native_fingerprints_compatible(stored_fp, candidate):
            return True
    return False


def prefer_fingerprint(
    stored_fp: Optional[str],
    incoming_fp: Optional[str],
    *,
    fallback_fps: Sequence[str] = (),
) -> Optional[str]:
    """Choose the fingerprint to persist, upgrading rotated keys without dropping a
    known sub-institution tag when an incoming feed omits optional metadata."""
    if not incoming_fp:
        return stored_fp
    if not stored_fp:
        return incoming_fp
    if (
        len(stored_fp) == 64
        and len(incoming_fp) == 64
        and stored_fp[48:] != _UNSPECIFIED_SUB_TAG
        and incoming_fp[48:] == _UNSPECIFIED_SUB_TAG
        and (
            fingerprint_uses_current_key(stored_fp)
            or fingerprint_uses_fallback_key(stored_fp)
        )
        and any(
            len(cand) == 64
            and hmac.compare_digest(stored_fp[:48], cand[:48])
            and cand[48:] == _UNSPECIFIED_SUB_TAG
            for cand in (incoming_fp, *fallback_fps)
        )
    ):
        return stored_fp
    return incoming_fp


def fingerprints_conflict(
    stored_fp: Optional[str],
    incoming_fp: Optional[str],
    *,
    fallback_fps: Sequence[str] = (),
) -> bool:
    """True when two fingerprints prove distinct underlying accounts.

    Returns False when either side is absent, when ``stored_fp`` matches
    ``incoming_fp`` (or any fallback-key digest), or when ``incoming_fp``
    carries the current SECRET_KEY's key tag while ``stored_fp`` carries an
    *unknown* old key tag (meaning SECRET_KEY rotated without retaining the old
    key in ``secret_key_fallbacks``). Fingerprints tagged with either the
    current key or a known fallback key that fail to match still conflict.
    """
    if not stored_fp or (not incoming_fp and not fallback_fps):
        return False
    if fingerprints_match(stored_fp, incoming_fp, fallback_fps=fallback_fps):
        return False
    known_key_tags = {
        fp[:8]
        for fp in (incoming_fp, *fallback_fps)
        if fp and len(fp) == 64
    }
    if (
        (incoming_fp and fingerprint_uses_current_key(incoming_fp))
        or (not incoming_fp and bool(fallback_fps))
    ) and not (len(stored_fp) == 64 and stored_fp[:8] in known_key_tags):
        return False
    return True


def hash_account_identifier_with_fallbacks(
    provider: str,
    raw_id: Optional[str],
    *,
    institution: Optional[str] = None,
    sub_institution: Optional[str] = None,
    min_length: int = 6,
) -> tuple[Optional[str], tuple[str, ...]]:
    """Compute the current keyed HMAC-SHA256 fingerprint plus fallback-key digests.

    Used to match the same underlying account across re-authorizations,
    ``SECRET_KEY`` rotations, or two co-owners' separate bank connections (joint
    accounts) without persisting raw IBANs or account numbers.

    ``institution`` (Pluggy connector, Enable Banking ASPSP + country) is
    required and bound into the HMAC so non-globally-unique branch/account
    numbers or BBANs at different banks can never collide. Optional
    ``sub_institution`` (e.g. Pluggy COMPE code) is bound into the trailing
    sub-institution tag so distinct institutions under one connector conflict
    while a feed omitting optional COMPE metadata still matches one that has it.
    Returns ``(None, ())`` for missing institution or missing, masked (including
    grouped masks like ``XX XX 1234``), trivial, or too-short inputs so
    unrelated accounts or credit cards sharing a 4-digit tail are never merged.
    """
    if not raw_id or not isinstance(raw_id, str):
        return None, ()
    if not institution or not isinstance(institution, str):
        return None, ()
    inst_clean = " ".join(institution.strip().lower().split())
    if not inst_clean:
        return None, ()
    sub_clean: Optional[str] = None
    if isinstance(sub_institution, str):
        cleaned_sub = " ".join(sub_institution.strip().lower().split())
        sub_clean = cleaned_sub or None
    stripped = raw_id.strip()
    if "*" in stripped or "•" in stripped:
        return None, ()
    cleaned = "".join(ch for ch in stripped if ch.isalnum()).upper()
    if "XXXX" in cleaned or len(cleaned) < min_length or len(set(cleaned)) <= 1:
        return None, ()
    from app.core.config import get_settings

    settings = get_settings()
    secret = settings.secret_key.get_secret_value()
    if not secret:
        return None, ()
    provider_clean = provider.strip().lower()
    primary = _compute_account_fingerprint(
        secret, provider_clean, inst_clean, cleaned, sub_clean
    )
    fallbacks = tuple(
        _compute_account_fingerprint(
            fb, provider_clean, inst_clean, cleaned, sub_clean
        )
        for fb in dict.fromkeys(settings.secret_key_fallbacks)
        if fb and fb != secret
    )
    return primary, fallbacks


def hash_account_identifier(
    provider: str,
    raw_id: Optional[str],
    *,
    institution: Optional[str] = None,
    sub_institution: Optional[str] = None,
    min_length: int = 6,
) -> Optional[str]:
    """Keyed HMAC-SHA256 fingerprint of a bank-assigned account identifier."""
    primary, _ = hash_account_identifier_with_fallbacks(
        provider,
        raw_id,
        institution=institution,
        sub_institution=sub_institution,
        min_length=min_length,
    )
    return primary


def hash_plugin_account_fingerprint(
    provider: str, plugin_fingerprint: Optional[str]
) -> Optional[str]:
    """Server-HMAC a plugin-supplied account fingerprint with ``secret_key`` and ``provider``.

    Plugins run without access to ``SECRET_KEY`` and emit an unkeyed 64-hex
    digest. Re-HMACing on the host binds the fingerprint to this instance and
    plugin provider so a plugin cannot forge a native or cross-plugin match.
    """
    if not plugin_fingerprint or not isinstance(plugin_fingerprint, str):
        return None
    cleaned = plugin_fingerprint.strip().lower()
    if not cleaned:
        return None
    from app.core.config import get_settings

    secret = get_settings().secret_key.get_secret_value()
    if not secret:
        return None
    msg = f"plugin-account-fingerprint:v1:{provider.strip().lower()}:{cleaned}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()


@dataclass
class ReservedBalanceData:
    """Funds an institution holds reserved/earmarked inside an account.

    Mercado Pago cofrinhos, "saldo reservado", caixinhas. Providers report
    these beside the account balance without guaranteeing whether they are
    included, so they are stored and shown without changing totals (ADR 0022).
    `external_id` is the provider's identification, stable across syncs and
    scoped to its account. One instance per currency: `amount` sums the
    reservation's remuneration bands in that currency.
    """

    external_id: str
    name: Optional[str]
    currency: str
    amount: Decimal


@dataclass
class AccountData:
    # The provider's identifier for this account. It is expected to be stable
    # across re-syncs, and the sync matches an incoming account on it first. A
    # provider that re-keys it (Enable Banking mints a `uid` scoped to a single
    # session) must also set `stable_external_id`, or re-syncing inserts a
    # duplicate instead of recognising the account it already imported.
    external_id: str
    name: str
    type: str  # checking, savings, credit_card
    balance: Decimal
    currency: str
    credit_limit: Optional[Decimal] = None
    statement_close_day: Optional[int] = None
    payment_due_day: Optional[int] = None
    minimum_payment: Optional[Decimal] = None
    card_brand: Optional[str] = None
    card_level: Optional[str] = None
    # Last 4 chars of the bank's own identifier for the account, when the
    # provider exposes one. Disambiguates accounts a bank reports under an
    # identical name (issue #408). Never the full identifier — see mask_last4.
    masked_number: Optional[str] = None
    shared_balance_group: Optional[str] = None
    # The provider's session-independent identifier for this account, when it
    # offers one. See `external_id` above: it is *supposed* to be stable, and
    # Enable Banking is the provider that breaks that. This is the value that
    # survives (EB's `identification_hash`), and the sync uses it to rebind the
    # existing row to the new uid instead of duplicating the account.
    stable_external_id: Optional[str] = None
    # Keyed HMAC-SHA256 digest of a provider account identifier (IBAN, branch/
    # account number) computed by `hash_account_identifier`. Enables safe cross-
    # connection joint-account matching without storing raw account numbers.
    account_fingerprint: Optional[str] = None
    # Fingerprints for the same identifier keyed under `secret_key_fallbacks`,
    # allowing Tier 1 matching to survive SECRET_KEY rotations and upgrade the
    # persisted fingerprint to the current key.
    fallback_account_fingerprints: tuple[str, ...] = ()
    # Per-account institution override (SimpleFIN — issue #345). None = same
    # as connection. The external id is the provider's stable org id
    # (SimpleFIN conn_id) so a renamed bank updates its row instead of
    # minting a new one; name-only hints fall back to name identity.
    institution_external_id: Optional[str] = None
    institution_name: Optional[str] = None
    institution_logo_url: Optional[str] = None
    # Part of `balance` that the bank has auto-applied to an investment which
    # the provider also reports as a holding (Pluggy
    # `bankData.automaticallyInvestedBalance`, issue #843; a SimpleFIN
    # brokerage, whose balance is the whole portfolio while its positions
    # arrive as holdings, issue #343). Zero when unknown.
    # The sync subtracts it only when a live holding is counted for this
    # account (see connection_service._auto_invested_deductions), so the
    # money is counted once: as the asset when one mirrors it, as cash
    # otherwise.
    auto_invested_balance: Decimal = Decimal(0)
    # Provider-owned reserves, shown separately and never added to totals
    # (ADR 0022). None = unreported or incomplete; keep stored rows.
    # [] = explicitly reported, none active.
    reserved_balances: Optional[list[ReservedBalanceData]] = None
    # Credit still free on a card, when the provider reports it as a figure of
    # its own instead of only the debt. Enable Banking reports a card from the
    # account holder's side, and some banks (mBank PL, issue #624) put the
    # credit limit in the booked balance, so the debt is only exact as
    # `credit_limit - available_credit`. None = the provider said no such
    # thing, and `balance` is the debt as usual.
    available_credit: Optional[Decimal] = None


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
    # Text a provider keeps beside the description: searched and matched by rules, not shown in the list.
    notes: Optional[str] = None
    raw_data: Optional[dict] = None
    # Installment metadata (parcelamento) — populated by CC providers that expose it.
    installment_number: Optional[int] = None
    total_installments: Optional[int] = None
    installment_total_amount: Optional[Decimal] = None
    installment_purchase_date: Optional[date] = None
    # Provider-side identifier of the bill this transaction belongs to.
    # Resolved to a credit_card_bills.id FK at sync time (issue #92).
    bill_external_id: Optional[str] = None
    # Last four characters of the physical/virtual card used for this charge.
    # This belongs to the transaction rather than the account because a single
    # credit-card account can have additional or virtual cards. Never the PAN.
    card_masked_number: Optional[str] = None


@dataclass
class ConnectionData:
    external_id: str
    institution_name: str
    credentials: dict
    accounts: list[AccountData]
    # Fully-formed institution logo URL when the provider exposes one
    # (Pluggy connector.imageUrl, Enable Banking ASPSP logo). None for
    # providers/institutions without a logo (e.g. SimpleFin).
    logo_url: Optional[str] = None


@dataclass
class ConnectTokenData:
    access_token: str


@dataclass
class BillData:
    """A normalized credit-card bill (fatura), provider-agnostic.

    Only the fields universal to a credit-card statement are promoted. Anything
    a specific integration provides on top (finance charges, recorded payments,
    installment options, etc.) is preserved verbatim in `raw_data` so we can
    pull more out later without forcing a schema-shaped opinion now.
    """

    external_id: str
    due_date: date
    total_amount: Decimal
    currency: str = "BRL"
    minimum_payment: Optional[Decimal] = None
    raw_data: Optional[dict] = None


@dataclass
class HoldingData:
    """A normalized investment holding, provider-agnostic.

    Providers that don't expose holdings-style data return an empty list
    from `get_holdings`. Provider-specific fields that don't fit the common
    shape (rate, profit, issuer, status, type/subtype labels, etc.) go in
    `metadata` as-is — the sync layer stores it on Asset.external_metadata
    without trying to normalize it.
    """

    external_id: str
    name: str
    currency: str
    current_value: Decimal
    # Pre-tax/pre-fee market value when the provider exposes it separately
    # from the withdrawable balance. Performance comparisons use this value;
    # portfolio totals continue to use ``current_value``.
    gross_value: Optional[Decimal] = None
    quantity: Optional[Decimal] = None
    unit_price: Optional[Decimal] = None
    purchase_price: Optional[Decimal] = None
    purchase_date: Optional[date] = None
    isin: Optional[str] = None
    # Exchange/asset symbol (e.g. "AAPL", "DOGE") for the dedicated
    # `assets.ticker` column. Kept separate from `currency`: connectors that
    # report a crypto position put the ticker in both, and only one of them
    # belongs in a 3-char ISO currency field.
    ticker: Optional[str] = None
    maturity_date: Optional[date] = None
    # Date the provider says `current_value` refers to. A provider can keep
    # serving a frozen figure (a redeemed position it never reports as
    # closed), so this date is the only sign that the value stopped moving.
    # None = not reported by this provider.
    value_date: Optional[date] = None
    is_withdrawn: bool = False  # provider signaled the position was sold/transferred
    metadata: Optional[dict] = None
    # Owning-account hint (SimpleFIN — issue #345): holdings are reported per
    # account, so each investment account gets its own wallet ("401(k)" apart
    # from "Rollover IRA"). None = the connection-default wallet, for
    # providers whose holdings aren't attributable to an account.
    account_external_id: Optional[str] = None
    account_name: Optional[str] = None
    # Buys and sells of this holding, for providers that report them (plugin
    # contract 1). None: not reported, the stored ledger is left alone.
    trades: Optional[list["HoldingTradeData"]] = None
    # What kind of investment it is ("stock", "etf", "fund", "crypto"), for
    # providers that know. None: the generic "investment".
    asset_type: Optional[str] = None


@dataclass
class HoldingTradeData:
    """One buy or sell the provider reports for a holding.

    Performance treats these as the exact money moved in or out of the
    position, so a valuation increase is never mistaken for a purchase.
    `quantity` is always positive; `kind` carries the direction. `price` is
    per unit in the asset's currency. `fee` is the cost of executing the
    trade (brokerage, exchange fees), excluding income tax withheld on a
    sale.
    """

    external_id: str
    kind: Literal["buy", "sell"]
    date: date
    quantity: Decimal
    price: Decimal
    fee: Decimal = Decimal("0")


@dataclass
class InstitutionData:
    """One ASPSP/bank offered by an OAuth provider."""

    name: str  # canonical identifier the provider expects in subsequent calls
    display_name: str
    country: str  # ISO 3166-1 alpha-2
    logo: Optional[str] = None
    bic: Optional[str] = None
    psu_types: list[str] = field(default_factory=list)  # e.g. ["personal", "business"]
    max_consent_days: Optional[int] = None
    max_history_days: Optional[int] = None


@dataclass
class InstitutionListData:
    """List of supported institutions (banks) for a provider, optionally for one country."""

    countries: list[str]
    institutions: list[InstitutionData]


class SessionExpiredError(Exception):
    """Raised when a provider session/consent has expired and reauth is required."""


class ProviderUserActionRequired(Exception):
    """Raised when a provider needs the user to take an action outside the app.

    Example: Enable Banking restricted mode requires the user to pre-link
    accounts in the EB portal before sessions return any accounts.
    """

    def __init__(self, message: str, *, code: str, help_url: Optional[str] = None) -> None:
        super().__init__(message)
        self.code = code
        self.help_url = help_url


class ProviderRateLimited(Exception):
    """Raised when the upstream bank/aggregator is throttling data requests.

    Transient and outside the user's control — PSD2 caps unattended account
    access (commonly ~4/day per resource), so a burst of syncs returns HTTP
    429. The connection is healthy; callers should skip this run and retry
    later rather than flag it as errored.

    ``retry_after`` is the wait the provider asked for, in seconds, when it
    sent one (``Retry-After``); None when it did not.
    """

    def __init__(self, message: str = "", retry_after: Optional[float] = None):
        super().__init__(message)
        self.retry_after = retry_after


class ProviderNotConfiguredError(Exception):
    """Raised when a connection references a provider missing from the registry.

    This is a server configuration problem, not a bank problem — typically one
    process (e.g. the Celery worker) is not loading the environment that
    enables the provider while the API process is. The credentials are fine,
    so callers must not flip the connection to "error": the reconnect banner
    would send the user chasing the wrong fix (and burning setup tokens).
    """


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

    def __init__(self, settings: Settings | None = None):
        self._configured_settings = settings

    @property
    def settings(self) -> Settings:
        from app.core.config import get_settings

        return self._configured_settings or get_settings()

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique provider identifier (e.g. 'pluggy', 'belvo')."""
        ...

    @property
    def flow_type(self) -> str:
        """Connection flow type: 'oauth' for redirect-based, 'widget' for embedded widget."""
        return "oauth"

    @property
    def rereads_by_date(self) -> bool:
        """Whether ``get_transactions_by_date`` is implemented. The sync skips
        the pending re-read, and the query behind it, when it is not."""
        return False

    @property
    def redirect_uri(self) -> str:
        """Provider-specific OAuth redirect URI.

        Each provider reads its own env var (e.g.
        ``PLUGGY_OAUTH_REDIRECT_URI``, ``ENABLE_BANKING_OAUTH_REDIRECT_URI``)
        so different providers can register different URLs in their
        respective dashboards. When unset the URL is derived from
        ``FRONTEND_URL`` so a non-default port or domain works out of the box.
        """
        from app.core.config import get_settings

        settings = get_settings()
        return settings.pluggy_oauth_redirect_uri or default_oauth_redirect_uri()

    async def create_connect_token(
        self, client_user_id: str, item_id: str | None = None
    ) -> ConnectTokenData:
        """Create a connect token for widget-based flows. Override in widget providers."""
        raise NotImplementedError(f"{self.name} does not support widget connect tokens")

    async def list_institutions(
        self, country: Optional[str] = None
    ) -> "InstitutionListData":
        """List supported institutions (banks). Empty by default for providers
        that don't surface a selection step (Pluggy uses its own widget).
        """
        return InstitutionListData(countries=[], institutions=[])

    @abstractmethod
    async def get_oauth_url(
        self,
        redirect_uri: str,
        state: str,
        flow_params: Optional[dict] = None,
    ) -> str:
        """Generate OAuth URL for user to authorize.

        ``flow_params`` carries provider-specific options gathered up-front
        (e.g. EB needs ``{"country": "DE", "institution_name": "Revolut"}``).
        """
        ...

    async def reauth_url(
        self,
        credentials: dict,
        settings: dict,
        redirect_uri: str,
        state: str,
    ) -> str:
        """Build a re-authorization URL for an existing connection whose
        session/consent expired. Default raises — only providers with
        renewable consent (OAuth-redirect) need to override.
        """
        raise NotImplementedError(f"{self.name} does not support reauth_url")

    @abstractmethod
    async def handle_oauth_callback(self, code: str) -> ConnectionData:
        """Exchange OAuth code for access token and fetch initial data."""
        ...

    @abstractmethod
    async def get_accounts(self, credentials: dict) -> list[AccountData]:
        """Fetch accounts for a connection."""
        ...

    @abstractmethod
    async def get_transactions(
        self, credentials: dict, account_external_id: str,
        since: Optional[date] = None, payee_source: str = "auto",
    ) -> list[TransactionData]:
        """Fetch transactions for an account."""
        ...

    async def get_transactions_by_date(
        self, credentials: dict, account_external_id: str,
        date_from: date, payee_source: str = "auto",
    ) -> list[TransactionData]:
        """Fetch every transaction dated on or after ``date_from``.

        Providers whose ``get_transactions`` filters on something other than
        the transaction date (Pluggy filters on ingestion time) override this
        so the sync layer can re-read pending rows the incremental listing no
        longer returns. The default returns nothing, which leaves the sync of
        every other provider as it was.
        """
        return []

    def parse_raw_transaction(
        self, raw: dict, payee_source: str = "auto"
    ) -> Optional[TransactionData]:
        """Rebuild a transaction from the payload this provider stores in
        ``raw_data``, or None when it can't.

        A re-sync reads it on the payload stored before the new one is merged
        in: where a row still holds what that payload gave, the user never
        changed it, so a provider correction under the same id may replace
        it. Providers whose ``raw_data`` is the whole entry override this;
        the default leaves a known row's amount, type and payee as they are.
        """
        return None

    @abstractmethod
    async def refresh_credentials(self, credentials: dict) -> dict:
        """Refresh access token if needed."""
        ...

    async def trigger_refresh(self, credentials: dict | None) -> RefreshOutcome:
        """Ask the provider to pull fresh data from the underlying institution.

        Some aggregator providers cache the bank's data on their own side and
        only re-fetch on their own schedule. For those, the value we read on a
        sync may be older than what the bank actually has. When the user asks
        for fresh data, providers that expose an on-demand refresh should
        override this method to trigger it and poll until it completes.

        The default is a no-op (returns ``"skipped"``) — providers whose APIs
        already serve live data on every read don't need to override.
        """
        return "skipped"

    async def get_institution_logo(self, credentials: dict) -> Optional[str]:
        """Return the institution logo URL for an existing connection, or None.

        Used to backfill connections that were linked before logo capture
        existed (the sync layer calls this only when a connection has no
        stored logo). Default is no-op so providers without institution
        logos (e.g. SimpleFin) don't need to implement it.
        """
        return None

    async def get_holdings(self, credentials: dict) -> list[HoldingData]:
        """Fetch investment holdings for a connection.

        Providers that don't expose holdings (cash-only accounts, custom
        script providers without brokerage data, etc.) can rely on the
        default empty list.
        """
        return []

    async def get_holding_trades(
        self, credentials: dict, holdings: list[HoldingData]
    ) -> dict[str, list[HoldingTradeData]]:
        """Fetch the buy/sell history behind each of `holdings`.

        Returns each holding's trades keyed by its external id, with an empty
        list when the provider has none. A holding whose trades could not be
        fetched is left out, so the sync tries it again next time. Default is
        no trade data.
        """
        return {}

    async def get_bills(self, credentials: dict, account_external_id: str) -> list[BillData]:
        """Fetch credit-card bills (faturas) for an account.

        Providers without a bills endpoint, or non-regulated Pluggy
        connections that don't expose /bills, return the default empty list.
        The sync layer falls back to locally-computed cycle math in that case
        (see app.services.credit_card_service).
        """
        return []
