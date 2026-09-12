"""Write-only instance credentials and uncached runtime resolution."""

from pathlib import Path

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.services.crypto import decrypt, encrypt
from app.core.config import Settings, get_settings
from app.core.database import async_session_maker
from app.models.app_settings import AppSetting

PROVIDER_FIELDS = {
    "openexchangerates": ("openexchangerates_app_id",),
    "pluggy": ("pluggy_client_id", "pluggy_client_secret"),
    "enable_banking": ("enable_banking_app_id", "enable_banking_private_key"),
    "simplefin": ("simplefin_enabled",),
}
SECRET_FIELDS = {field for fields in PROVIDER_FIELDS.values() for field in fields} - {
    "simplefin_enabled"
}
SETTING_FIELDS = SECRET_FIELDS | {"simplefin_enabled"}


def can_store_secrets() -> bool:
    # Both published development defaults are shorter than 32 characters.
    return len(get_settings().secret_key.get_secret_value().strip()) >= 32


async def _overrides(session: AsyncSession) -> dict[str, str]:
    rows = await session.execute(
        select(AppSetting.key, AppSetting.value).where(AppSetting.key.in_(SETTING_FIELDS))
    )
    return {key: value for key, value in rows.all()}


def _resolve(overrides: dict[str, str]) -> Settings:
    settings = get_settings().model_copy(deep=True)
    for key, value in overrides.items():
        if key == "simplefin_enabled":
            settings.simplefin_enabled = value == "true"
        else:
            # A broken override must not silently switch to another account.
            value = decrypt(value) or ""
            setattr(
                settings,
                key,
                SecretStr(value) if isinstance(getattr(settings, key), SecretStr) else value,
            )
    if "enable_banking_private_key" in overrides:
        settings.enable_banking_private_key_file = ""
    return settings


async def resolve_settings(session: AsyncSession | None = None) -> Settings:
    if session is None:
        async with async_session_maker() as owned_session:
            return await resolve_settings(owned_session)
    return _resolve(await _overrides(session))


def _field_present(settings: Settings, key: str) -> bool:
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
    return all(_field_present(settings, key) for key in PROVIDER_FIELDS[provider])


async def provider_status(session: AsyncSession) -> list[dict]:
    overrides = await _overrides(session)
    settings = _resolve(overrides)
    environment = get_settings()
    secret_storage_available = can_store_secrets()
    result = []
    for provider, fields in PROVIDER_FIELDS.items():
        statuses = {}
        for key in fields:
            present = _field_present(settings, key)
            statuses[key] = {
                "configured": present,
                "source": "app" if key in overrides else "environment" if present else "none",
                "invalid": key in SECRET_FIELDS and key in overrides and not present,
                "environment_configured": _field_present(environment, key),
            }
        result.append(
            {"name": provider, "configured": is_configured(provider, settings),
             "can_store_secrets": secret_storage_available, "fields": statuses}
        )
    return result


async def update_provider(session: AsyncSession, provider: str, values: dict) -> dict:
    if (
        provider not in PROVIDER_FIELDS
        or not values
        or set(values) - set(PROVIDER_FIELDS[provider])
    ):
        raise ValueError("Unknown provider or credential field")
    # Validate the whole patch before writing anything. Never echo submitted input.
    for key, value in values.items():
        if value is None:
            continue
        if key == "simplefin_enabled":
            if type(value) is not bool:
                raise ValueError("SimpleFIN enabled must be a boolean")
        elif not isinstance(value, str) or not value.strip() or len(value) > 16000:
            raise ValueError("Credentials must be nonempty strings of at most 16000 characters")
    if any(key in SECRET_FIELDS and value is not None for key, value in values.items()):
        if not can_store_secrets():
            raise ValueError("Set a unique SECRET_KEY of at least 32 characters before saving credentials")
    for key, value in values.items():
        row = await session.get(AppSetting, key)
        if value is None:
            if row is not None:
                await session.delete(row)
            continue
        stored = str(value).lower() if key == "simplefin_enabled" else encrypt(value.strip())
        if stored is None:
            raise ValueError("Credential cannot be empty")
        if row is None:
            session.add(AppSetting(key=key, value=stored))
        else:
            row.value = stored
    await session.commit()
    return next(item for item in await provider_status(session) if item["name"] == provider)
