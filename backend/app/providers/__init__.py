from app.core.config import Settings
from app.providers.base import (
    AccountData,
    BankProvider,
    ConnectTokenData,
    ConnectionData,
    HoldingData,
    InstitutionData,
    InstitutionListData,
    ProviderUserActionRequired,
    RefreshOutcome,
    SessionExpiredError,
    TransactionData,
)

# Registry of available providers.
_PROVIDERS: dict[str, type[BankProvider]] = {}

# All known providers the system supports (extensible for future connectors).
KNOWN_PROVIDERS = [
    {
        "name": "pluggy",
        "display_name": "Pluggy",
        "description": "Open finance provider for Brazilian banks",
        "flow_type": "widget",
        "requires_institution_select": False,
        "supports_asset_sync": True,
    },
    {
        "name": "enable_banking",
        "display_name": "Enable Banking",
        "description": "European banks via PSD2 open banking",
        "flow_type": "oauth",
        "requires_institution_select": True,
        "supports_asset_sync": False,
    },
    {
        "name": "simplefin",
        "display_name": "SimpleFIN",
        "description": "US and international banks via SimpleFIN Bridge",
        "flow_type": "token",
        "requires_institution_select": False,
        "supports_asset_sync": True,
    },
]


def register_provider(name: str, cls: type[BankProvider]) -> None:
    """Register a bank provider implementation."""
    _PROVIDERS[name] = cls


def get_provider(name: str, settings: Settings | None = None) -> BankProvider:
    """Get an instance of a registered bank provider by name."""
    available_providers = _available_providers(settings)
    provider_class = available_providers.get(name)
    if not provider_class:
        available = ", ".join(available_providers) or "(none)"
        raise ValueError(f"Unknown provider: {name}. Available: {available}")
    return provider_class(settings=settings) if settings is not None else provider_class()


def list_providers() -> list[dict[str, str]]:
    """Return info about all registered providers."""
    return [
        {"name": name, "flow_type": cls().flow_type}
        for name, cls in _available_providers().items()
    ]


def all_known_providers(settings: Settings | None = None) -> list[dict]:
    """Return all known providers with a configured flag."""
    available = _available_providers(settings)
    return [
        {**p, "configured": p["name"] in available}
        for p in KNOWN_PROVIDERS
    ]


def _available_providers(settings: Settings | None = None) -> dict[str, type[BankProvider]]:
    """Derive availability without mutating process-wide registration."""
    from app.core.config import get_settings
    settings = settings or get_settings()
    providers = dict(_PROVIDERS)

    if settings.pluggy_client_id and settings.pluggy_client_secret:
        from app.providers.pluggy import PluggyProvider
        providers["pluggy"] = PluggyProvider

    from app.services.provider_settings import is_configured

    if is_configured("enable_banking", settings):
        from app.providers.enable_banking import EnableBankingProvider
        providers["enable_banking"] = EnableBankingProvider

    if settings.simplefin_enabled:
        from app.providers.simplefin import SimpleFinProvider
        providers["simplefin"] = SimpleFinProvider

    return providers


_storage_provider = None


def get_storage_provider():
    """Get the configured storage provider (singleton)."""
    global _storage_provider
    if _storage_provider is None:
        from app.core.config import get_settings

        settings = get_settings()
        if settings.storage_provider == "local":
            from app.providers.local_storage import LocalStorageProvider

            _storage_provider = LocalStorageProvider()
        else:
            raise NotImplementedError(
                f"Storage provider '{settings.storage_provider}' is not yet implemented. "
                "Supported: 'local'"
            )
    return _storage_provider


__all__ = [
    "BankProvider",
    "AccountData",
    "TransactionData",
    "ConnectionData",
    "ConnectTokenData",
    "HoldingData",
    "InstitutionData",
    "InstitutionListData",
    "ProviderUserActionRequired",
    "RefreshOutcome",
    "SessionExpiredError",
    "register_provider",
    "get_provider",
    "list_providers",
    "all_known_providers",
    "get_storage_provider",
]
