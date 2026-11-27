"""Which settings each provider needs, and whether they are present."""

from pathlib import Path

from app.core.config import Settings

PROVIDER_FIELDS = {
    "openexchangerates": ("openexchangerates_app_id",),
    "pluggy": ("pluggy_client_id", "pluggy_client_secret"),
    "enable_banking": ("enable_banking_app_id", "enable_banking_private_key"),
    "simplefin": ("simplefin_enabled",),
}
SETTING_FIELDS = {field for fields in PROVIDER_FIELDS.values() for field in fields}
SECRET_FIELDS = SETTING_FIELDS - {"simplefin_enabled"}


def field_present(settings: Settings, key: str) -> bool:
    if key == "enable_banking_private_key":
        key_file = (settings.enable_banking_private_key_file or "").strip()
        if key_file:
            try:
                with Path(key_file).open(encoding="utf-8") as private_key:
                    return bool(private_key.read(16000).strip())
            except (OSError, ValueError, UnicodeError):
                return False
    return bool(getattr(settings, key))


def is_configured(provider: str, settings: Settings) -> bool:
    return all(field_present(settings, key) for key in PROVIDER_FIELDS[provider])
