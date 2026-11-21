import asyncio
import ipaddress
import logging
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from math import isfinite
from os import getenv
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import PrivateAttr, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Use the same environment variable that systemd uses: https://systemd.io/CREDENTIALS/
# If not defined, defaults to docker secrets defaults (https://docs.docker.com/compose/how-tos/use-secrets/)
CREDENTIALS_DIRECTORY: list[Path] = [
    Path(p) for p in getenv("CREDENTIALS_DIRECTORY", "/run/secrets").split(":") if p
]

# Where a reverse proxy in front of the backend can sit: loopback, the
# private ranges Docker and Podman give container networks, and a LAN.
DEFAULT_TRUSTED_PROXY_NETWORKS = (
    "127.0.0.0/8,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,fc00::/7"
)


@lru_cache(maxsize=8)
def parse_trusted_proxy_networks(
    value: str,
) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    """The networks in a comma-separated TRUSTED_PROXY_NETWORKS value; a bare
    address is a network of one. Raises ValueError on anything else."""
    return tuple(
        ipaddress.ip_network(entry.strip(), strict=False)
        for entry in value.split(",")
        if entry.strip()
    )


class Settings(BaseSettings):
    # App
    app_name: str = "Securo"
    debug: bool = False

    # Database
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/securo"

    # Auth. Signs session and password-reset tokens and derives the key that
    # encrypts saved credentials. Left unset (or on a public default), the
    # instance generates its own and keeps it in the database: see
    # app.core.instance_secrets and get_settings().
    secret_key: SecretStr = SecretStr("")
    # Keys this instance encrypted with before, tried when opening a saved
    # credential. Never used to sign or check a token.
    _secret_key_fallbacks: tuple[str, ...] = PrivateAttr(default=())
    # Set when the instance key may hold old ciphertexts but wasn't read yet.
    _stored_key_url: str | None = PrivateAttr(default=None)
    local_auth_enabled: bool = True
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24  # 24 hours

    # Pluggy
    pluggy_client_id: str = ""
    pluggy_client_secret: SecretStr = SecretStr("")
    # Empty means "derive from FRONTEND_URL" (see BankProvider.redirect_uri).
    # Set explicitly only when the URL registered in the provider dashboard
    # differs from where the app is served.
    pluggy_oauth_redirect_uri: str = ""
    # Gates the BrasilAPI outbound lookup in _lookup_bank_info (pluggy.py).
    # Off by default: no third-party call happens until a deploy opts in.
    brasilapi_institution_lookup_enabled: bool = False

    # Enable Banking (European PSD2 banks)
    enable_banking_app_id: str = ""
    enable_banking_private_key: SecretStr = SecretStr("")  # raw PEM; supports \n-escaped envs
    enable_banking_private_key_file: str = ""  # path to PEM file; takes precedence
    enable_banking_api_url: str = "https://api.enablebanking.com"
    enable_banking_oauth_redirect_uri: str = ""  # empty derives from FRONTEND_URL
    # Comma-separated BalanceStatus codes, most preferred first. Booked
    # balances lead: available ones can include the credit line.
    enable_banking_balance_priority: str = "ITBD,CLBD,OPBD,ITAV,CLAV,XPCD,OPAV"

    # SimpleFIN Bridge (US/intl banks, paste-a-token flow). Off by default.
    # The bridge URL defaults to the beta/sandbox host so users can test with
    # the demo token; flip to https://bridge.simplefin.org for production.
    simplefin_enabled: bool = False
    simplefin_api_url: str = "https://beta-bridge.simplefin.org"

    # Frontend
    frontend_url: str = "http://localhost:5173"
    # Absolute path to a built frontend (Vite's `dist`), served in-process by
    # the API. Empty by default and deliberately so: the published backend
    # image ships no frontend build, and a default path would let it start
    # serving whatever happened to be at that location.
    frontend_dist_path: str = ""
    # Docker Compose passes FRONTEND_URL empty when it is not set, plus the
    # published port; an empty FRONTEND_URL becomes http://localhost:<port>.
    frontend_port: int = 3000

    # WebAuthn / passkeys
    webauthn_rp_name: str = "Securo"
    # Empty means the RP ID follows the domain the browser is on, which is what
    # most deployments want. Set it to pin passkeys to one domain (e.g.
    # securo.example.com); requests from other origins are then rejected.
    webauthn_rp_id: str = ""
    # Empty means the expected origin follows the browser. Only set this when the
    # app is reached at exactly one origin and you want it enforced.
    webauthn_origin: str = ""
    webauthn_challenge_ttl_seconds: int = 300

    # Defaults
    default_currency: str = "USD"  # fallback currency when user preference is unavailable

    # FX Rates
    openexchangerates_app_id: str = ""
    supported_currencies: str = "USD,EUR,GBP,BRL,CAD,AUD,CHF,ARS,JPY,MXN,INR,SEK,DKK,NOK,PLN,CZK,HUF,RON,CRC,IDR,COP,PEN,UYU,CLP,DOP,KZT,RUB,GTQ,PHP,UAH,NZD,VND,SGD,AZN,TRY,PKR,MDL,AED,THB,EGP,MYR,CNY,SAR,QAR,JMD,RSD,MKD,HKD,TWD,ZAR,GEL,ILS"  # comma-separated list
    fx_sync_mode: str = "on_demand"  # "on_demand" or "scheduled"

    # Storage
    storage_provider: Literal["local", "s3"] = "local"
    storage_local_path: str = "./data/attachments"
    storage_max_file_size_mb: int = 10
    storage_allowed_extensions: str = "jpg,jpeg,png,webp,gif,heic,pdf"
    storage_max_attachments_per_transaction: int = 10
    # An invoice gathers more paper than a transaction does: the bill, the
    # fiscal document, a receipt, the contract behind it, and a correction
    # of any of them.
    storage_max_attachments_per_invoice: int = 20

    # Scheduled backups require one filesystem shared by the API and Celery
    # worker. Docker Compose provides it; Helm sets this from its backups PVC.
    backup_storage_shared: bool = True

    # S3 storage: blank credentials use the SDK's default credential chain.
    storage_s3_bucket: str = ""
    storage_s3_region: str = ""
    storage_s3_access_key: SecretStr = SecretStr("")
    storage_s3_secret_key: SecretStr = SecretStr("")
    storage_s3_session_token: SecretStr = SecretStr("")
    storage_s3_endpoint_url: str = ""  # for S3-compatible services (MinIO, DigitalOcean Spaces)
    storage_s3_allow_insecure_http: bool = False  # development only: permits http:// endpoints
    storage_s3_addressing_style: str = "auto"  # auto|path|virtual; checked only when S3 is selected

    # Registration
    registration_enabled: bool = True

    # OIDC login (works with Authentik, Pocket ID, and other standard OIDC providers)
    oidc_enabled: bool = False
    oidc_provider_name: str = "OIDC"
    oidc_discovery_url: str = (
        ""  # e.g. https://auth.example.com/application/o/securo/.well-known/openid-configuration
    )
    oidc_client_id: str = ""
    oidc_client_secret: SecretStr = SecretStr("")
    oidc_redirect_uri: str = ""  # defaults to {FRONTEND_URL}/api/auth/oidc/callback
    oidc_scopes: str = "openid email profile"
    oidc_auto_register: bool = True
    oidc_existing_user_link_mode: str = "disabled"  # disabled|verified_email|email
    oidc_require_verified_email: bool = True
    oidc_sync_roles: bool = False
    oidc_roles_claim: str = "groups"
    oidc_admin_roles: str = ""  # comma-separated provider roles/groups that grant Securo admin
    oidc_workspace_role_map: str = ""  # JSON: {"provider-role": "owner|editor|viewer"}
    # RP-initiated logout (#1623): where to send the browser so signing out
    # ends the provider's own session too. Empty reads end_session_endpoint
    # from the discovery document; a provider with neither keeps today's
    # behaviour (the local token is dropped and nothing else).
    oidc_logout_url: str = ""

    # Deploy scripts that source the .env file with a shell need values with
    # spaces to be quoted, and some panels (PaaS env vars, manual exports)
    # keep those quotes in the value. Drop one pair of surrounding quotes so
    # the provider always receives clean scope tokens (#887).
    @field_validator("enable_banking_balance_priority")
    @classmethod
    def validate_balance_priority(cls, value: str) -> str:
        codes = [code.strip().upper() for code in value.split(",")]
        valid = {
            "CLAV", "CLBD", "FWAV", "INFO", "ITAV", "ITBD", "OPAV", "OPBD",
            "OTHR", "PRCD", "VALU", "XPCD",
        }
        if not codes or any(code not in valid for code in codes) or len(set(codes)) != len(codes):
            raise ValueError("Balance priority must contain unique, valid BalanceStatus codes")
        return ",".join(codes)

    @field_validator("oidc_scopes")
    @classmethod
    def _strip_oidc_scopes_quotes(cls, value: str) -> str:
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1].strip()
        return value

    # Celery
    redis_url: str = "redis://localhost:6379/0"
    # Minimum age of a successful sync before automatic polling retries it.
    # Set to 6 for banks limiting unattended account access to four times/day.
    bank_sync_min_interval_hours: float = 4

    @model_validator(mode="after")
    def validate_bank_sync_interval(self):
        if not isfinite(self.bank_sync_min_interval_hours) or self.bank_sync_min_interval_hours <= 0:
            raise ValueError("BANK_SYNC_MIN_INTERVAL_HOURS must be finite and positive")
        try:
            datetime.now(timezone.utc) - timedelta(hours=self.bank_sync_min_interval_hours)
        except OverflowError as exc:
            raise ValueError("BANK_SYNC_MIN_INTERVAL_HOURS exceeds the supported date range") from exc
        return self

    # Reverse-proxy trust for client-IP-based rate limiting. 0 (default) means
    # request.client.host is used as-is, which is only correct when nothing
    # sits between the client and this service. In the shipped docker-compose
    # topology the backend is reached through the bundled nginx frontend, so
    # request.client.host is always nginx's container address. Set this to the
    # number of trusted reverse proxies in front of the backend (usually 1) to
    # derive the client IP from X-Forwarded-For instead, trusting only that
    # many hops from the right; a chain shorter than expected is not trusted.
    # The same IP is the Psu-Ip-Address of user-triggered Enable Banking syncs
    # (app.providers.psu), which only sends it when it is a public address.
    trusted_proxy_hops: int = 0
    # Where those proxies can be, as comma-separated networks or addresses.
    # X-Forwarded-For is only read past a peer inside them; any other peer is
    # the client itself, so a caller who reaches the backend port (or nginx,
    # past an outer proxy) cannot pick its own address (ADR 0024). The default
    # covers loopback and the private ranges container networks use; narrow
    # it when untrusted clients share those ranges with the proxies.
    trusted_proxy_networks: str = DEFAULT_TRUSTED_PROXY_NETWORKS

    @field_validator("trusted_proxy_networks")
    @classmethod
    def _check_trusted_proxy_networks(cls, value: str) -> str:
        try:
            parse_trusted_proxy_networks(value)
        except ValueError as exc:
            raise ValueError(f"TRUSTED_PROXY_NETWORKS is not a list of networks: {exc}") from exc
        return value

    # Per-IP ceiling on the assistant chat endpoint, in requests per minute.
    # Every message spends provider tokens, so a public demo should keep it
    # low (for example 5); on a self-hosted instance one person uses, normal
    # use stays well below the default. A turn that runs several tools is
    # still one request, so the ceiling counts questions, not tool calls.
    # 0 turns the limiter off.
    agents_chat_rate_limit_per_minute: int = 30

    # Logo size for market-priced asset icons. The logo URL is built from
    # the company website we get from the market-price provider; no API
    # key or third-party account is required. Defaults to 128×128 which
    # is what Google's favicon service caps at before upscaling.
    logo_size: int = 128

    # Brazilian Treasury bond prices (official Tesouro Transparente CSV).
    # On by default since most users are Brazilian; the official CSV is only
    # fetched when someone actually searches a bond, and the UI pre-warm is
    # gated to Brazilian users, so non-Brazilian installs pay ~zero cost.
    # Set TESOURO_DIRETO_ENABLED=false to fully disable (e.g. to avoid the
    # external dependency on the Brazilian government endpoint).
    tesouro_direto_enabled: bool = True

    # Data plugins (categories and rules) from the plugin catalog. Gated by
    # PLUGINS_ENABLED, off by default: while off nothing is fetched and no
    # plugin can be installed. The built-in rule packs don't depend on it.
    # The catalog is a static index, fetched only when someone opens it,
    # never in the background; manifests come from the same host, checked
    # against the index's sha256. Point it at a mirror to host your own.
    plugins_index_url: str = "https://plugins.usesecuro.com/v1/index.json"
    # Plugins with code run in the `securo-plugins` runner (Compose profile
    # `plugins`), never in this process. Blank URL: no runner, so no plugin
    # with code can be installed. The token is shared with the runner only.
    plugins_runner_url: str = ""
    plugins_runner_token: str = ""
    # How a plugin's code reaches this API, over the internal network. Only
    # plugins that declared `api_scopes` get a token to use it.
    plugins_api_url: str = "http://backend:8000/api"

    @property
    def oidc_login_available(self) -> bool:
        return bool(self.oidc_enabled and self.oidc_client_id and self.oidc_discovery_url)

    @model_validator(mode="after")
    def validate_storage_settings(self) -> "Settings":
        if self.storage_provider != "s3":
            return self
        # Normalize, not just check: the provider sends these values as they are.
        self.storage_s3_bucket = self.storage_s3_bucket.strip()
        raw_endpoint_url = self.storage_s3_endpoint_url
        self.storage_s3_endpoint_url = raw_endpoint_url.strip()
        if raw_endpoint_url and not self.storage_s3_endpoint_url:
            # A blank value would otherwise mean "no endpoint" and silently reach AWS.
            raise ValueError("STORAGE_S3_ENDPOINT_URL must not be blank when set")
        if not self.storage_s3_bucket:
            raise ValueError("STORAGE_S3_BUCKET is required when STORAGE_PROVIDER=s3")
        if self.storage_s3_addressing_style not in {"auto", "path", "virtual"}:
            raise ValueError("STORAGE_S3_ADDRESSING_STYLE must be one of: auto, path, virtual")
        has_access_key = bool(self.storage_s3_access_key.get_secret_value())
        has_secret_key = bool(self.storage_s3_secret_key.get_secret_value())
        if has_access_key != has_secret_key:
            raise ValueError("Set both STORAGE_S3_ACCESS_KEY and STORAGE_S3_SECRET_KEY, or neither")
        if self.storage_s3_session_token.get_secret_value() and not has_access_key:
            raise ValueError("STORAGE_S3_SESSION_TOKEN requires explicit S3 access and secret keys")
        if self.storage_s3_endpoint_url:
            endpoint = urlsplit(self.storage_s3_endpoint_url)
            allowed_schemes = {"https", "http"} if self.storage_s3_allow_insecure_http else {"https"}
            if (
                endpoint.scheme not in allowed_schemes
                or not endpoint.hostname
                or endpoint.username is not None
                or endpoint.password is not None
                or endpoint.query
                or endpoint.fragment
            ):
                raise ValueError(
                    "STORAGE_S3_ENDPOINT_URL must be an HTTPS URL without credentials, query or fragment "
                    "(http:// requires STORAGE_S3_ALLOW_INSECURE_HTTP=true, development only)"
                )
        return self

    @model_validator(mode="after")
    def default_frontend_url(self) -> "Settings":
        if not self.frontend_url:
            self.frontend_url = f"http://localhost:{self.frontend_port}"
        return self

    @property
    def secret_key_fallbacks(self) -> tuple[str, ...]:
        if self._stored_key_url is not None and not _in_event_loop():
            # Read on first use rather than at import: the database may still
            # be starting then, and a key missed once would stay missed for
            # the life of the process. Kept only once the read succeeds.
            # Never from the event loop: the API loads it at startup in a
            # thread (load_stored_secret_key), so a request doesn't wait.
            from app.core import instance_secrets as instance

            try:
                stored = instance.read_existing(self._stored_key_url, instance.SECRET_KEY_NAME)
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "Could not read the stored instance key yet: %s", exc
                )
                return self._secret_key_fallbacks
            current = self.secret_key.get_secret_value()
            if stored and stored != current:
                self._secret_key_fallbacks = (stored, *self._secret_key_fallbacks)
            self._stored_key_url = None
        return self._secret_key_fallbacks

    @model_validator(mode="after")
    def validate_auth_settings(self) -> "Settings":
        if not self.local_auth_enabled and not self.oidc_login_available:
            missing = []
            if not self.oidc_enabled:
                missing.append("OIDC_ENABLED=true")
            if not self.oidc_client_id:
                missing.append("OIDC_CLIENT_ID")
            if not self.oidc_discovery_url:
                missing.append("OIDC_DISCOVERY_URL")
            raise ValueError(
                "LOCAL_AUTH_ENABLED=false requires a complete OIDC configuration; "
                f"missing: {', '.join(missing)}"
            )
        return self

    # The CWD-relative ".env" is kept for backward compatibility; the anchored
    # backend/.env guarantees the API and the Celery worker/beat resolve the
    # same file no matter which working directory each service is launched
    # from (a systemd unit without WorkingDirectory= used to leave the worker
    # with default settings, silently disabling every bank-sync provider).
    #
    # extra="ignore" because the same .env is shared with Docker Compose and with
    # the optional modules: it legitimately holds keys this model doesn't declare
    # (COMPOSE_PROFILES, FRONTEND_PORT, AGENTS_*, ...). Unknown keys coming from
    # the process environment are ignored by pydantic-settings anyway; without
    # this, the very same key written into the .env file aborted startup with
    # "Extra inputs are not permitted".
    model_config = SettingsConfigDict(
        env_file=(".env", Path(__file__).resolve().parents[2] / ".env"),
        secrets_dir=CREDENTIALS_DIRECTORY,
        extra="ignore",
    )


def _in_event_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def load_stored_secret_key() -> bool:
    """Read the instance key kept as a fallback, from a thread. True once
    there is nothing left to read."""
    settings = get_settings()
    settings.secret_key_fallbacks  # noqa: B018 (reads and caches)
    return settings._stored_key_url is None


@lru_cache
def get_settings() -> Settings:
    return resolve_secret_key(Settings())


def resolve_secret_key(settings: Settings) -> Settings:
    """Use the instance key when the environment has none of its own (#1173).

    A SECRET_KEY set in the environment always wins. Missing, or set to one of
    the public defaults that shipped with every install path, the instance
    uses a key generated once and stored in the database, and the credentials
    encrypted with the public default move to it (app.core.instance_secrets).
    """
    from app.core import instance_secrets as instance

    raw = settings.secret_key.get_secret_value()
    configured = raw.strip()
    if not instance.is_public_or_missing(configured):
        # The key stays exactly as given. Anything encrypted while the
        # instance key was in use (SECRET_KEY set after it existed) still
        # opens: that key is read on first use (secret_key_fallbacks).
        settings._stored_key_url = settings.database_url
        settings._secret_key_fallbacks = tuple(
            k for k in instance.PUBLIC_SECRET_KEYS if k != raw
        )
        return settings

    if configured:
        logging.getLogger(__name__).warning(
            "SECRET_KEY is set to a public default, which anyone can use to sign in as any "
            "user. Using this instance's own key instead."
        )
    # Values were encrypted with the key exactly as configured, padding and
    # all, or with an empty one; try those as well as the public defaults.
    old_keys = tuple(dict.fromkeys((raw, configured, *instance.PUBLIC_SECRET_KEYS)))
    key = instance.fetch_or_create(
        settings.database_url,
        instance.SECRET_KEY_NAME,
        on_created=instance.reencrypt_with(old_keys),
    )
    settings.secret_key = SecretStr(key)
    settings._secret_key_fallbacks = old_keys
    return settings
