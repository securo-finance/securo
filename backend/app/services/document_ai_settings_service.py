"""Which model documents are read with, and whether reading is on (ADR 0037).

Everything is set in the app, never in the environment: an admin turns
document reading on for the instance and picks its model in Admin Settings
(stored as app settings, the key sealed with Fernet, like the bank providers'
credentials, ADR 0020). A workspace owner may replace any field for the
workspace. A blank field falls back to the instance's value, so the key an
admin set for the whole instance is never shown to anyone and never leaves
the server.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app_settings import AppSetting
from app.models.document_extraction import WorkspaceDocumentAiSettings
from app.services.document_ai_capabilities import VisionAnswer, resolve_vision

PROVIDERS = ("ollama", "openai", "anthropic", "openai_compatible")
VISION_SETTINGS = ("auto", "true", "false")

ENABLED_KEY = "document_ai_enabled"
PROVIDER_KEY = "document_ai_provider"
BASE_URL_KEY = "document_ai_base_url"
MODEL_KEY = "document_ai_model"
API_KEY_KEY = "document_ai_api_key"
VISION_KEY = "document_ai_vision"
INSTANCE_KEYS = (ENABLED_KEY, PROVIDER_KEY, BASE_URL_KEY, MODEL_KEY, API_KEY_KEY, VISION_KEY)


class SecretStorageUnavailable(ValueError):
    """SECRET_KEY is too weak to seal a saved key."""


async def _instance_rows(session: AsyncSession) -> dict[str, str]:
    rows = await session.execute(
        select(AppSetting.key, AppSetting.value).where(AppSetting.key.in_(INSTANCE_KEYS))
    )
    return {key: value for key, value in rows.all()}


async def document_ai_enabled(session: AsyncSession) -> bool:
    value = (
        await session.execute(select(AppSetting.value).where(AppSetting.key == ENABLED_KEY))
    ).scalar_one_or_none()
    return value == "true"


@dataclass
class ResolvedModel:
    provider: str
    base_url: str
    model: str
    api_key: str
    vision_setting: str
    # Where the provider and model came from: the workspace or the instance.
    source: str

    @property
    def configured(self) -> bool:
        if self.provider not in PROVIDERS or not self.model:
            return False
        if self.provider == "openai_compatible" and not self.base_url:
            return False
        if self.provider in ("openai", "anthropic") and not self.api_key:
            return False
        return True


def _decrypt(value: Optional[str]) -> str:
    if not value:
        return ""
    from app.agents.services.crypto import decrypt

    return decrypt(value) or ""


def _encrypt(value: str) -> Optional[str]:
    from app.agents.services.crypto import encrypt
    from app.services.provider_settings import can_store_secrets

    if not can_store_secrets():
        raise SecretStorageUnavailable(
            "Set a SECRET_KEY of at least 32 characters before saving a key in the app."
        )
    return encrypt(value)


async def instance_model(session: AsyncSession) -> ResolvedModel:
    rows = await _instance_rows(session)
    return ResolvedModel(
        provider=(rows.get(PROVIDER_KEY) or "").strip().lower(),
        base_url=(rows.get(BASE_URL_KEY) or "").strip(),
        model=(rows.get(MODEL_KEY) or "").strip(),
        api_key=_decrypt(rows.get(API_KEY_KEY)),
        vision_setting=(rows.get(VISION_KEY) or "auto").strip().lower(),
        source="instance",
    )


async def instance_has_api_key(session: AsyncSession) -> bool:
    return bool((await _instance_rows(session)).get(API_KEY_KEY))


async def save_instance_settings(
    session: AsyncSession,
    *,
    enabled: bool,
    provider: Optional[str],
    base_url: Optional[str],
    model: Optional[str],
    api_key: Optional[str],
    clear_api_key: bool,
    vision: str,
) -> None:
    provider = (provider or "").strip().lower()
    if provider and provider not in PROVIDERS:
        raise ValueError(f"Unknown provider: {provider}")
    vision = (vision or "auto").strip().lower()
    if vision not in VISION_SETTINGS:
        raise ValueError(f"Unknown vision setting: {vision}")
    values: dict[str, Optional[str]] = {
        ENABLED_KEY: "true" if enabled else "false",
        PROVIDER_KEY: provider or None,
        BASE_URL_KEY: (base_url or "").strip() or None,
        MODEL_KEY: (model or "").strip() or None,
        VISION_KEY: vision,
    }
    if clear_api_key:
        values[API_KEY_KEY] = None
    elif api_key and api_key.strip():
        values[API_KEY_KEY] = _encrypt(api_key.strip())
    for key, value in values.items():
        row = await session.get(AppSetting, key)
        if value is None:
            if row is not None:
                await session.delete(row)
        elif row is None:
            session.add(AppSetting(key=key, value=value))
        else:
            row.value = value
    await session.commit()


async def get_workspace_settings(
    session: AsyncSession, workspace_id: uuid.UUID
) -> Optional[WorkspaceDocumentAiSettings]:
    return (
        await session.execute(
            select(WorkspaceDocumentAiSettings).where(WorkspaceDocumentAiSettings.workspace_id == workspace_id)
        )
    ).scalar_one_or_none()


async def resolve_model(session: AsyncSession, workspace_id: uuid.UUID) -> ResolvedModel:
    instance = await instance_model(session)
    row = await get_workspace_settings(session, workspace_id)
    if row is None:
        return instance
    own_provider = (row.provider or "").strip().lower()
    own_base_url = (row.base_url or "").strip()
    switched = bool(own_provider) and own_provider != instance.provider
    # A workspace that picked another provider never inherits the instance's
    # URL or key: those belong to the instance's provider. Nor does one that
    # points at its own URL: the instance's key would be sent to a host the
    # admin never chose.
    keeps_instance_key = not switched and not own_base_url
    return ResolvedModel(
        provider=own_provider or instance.provider,
        base_url=own_base_url or ("" if switched else instance.base_url),
        model=(row.model or "").strip() or ("" if switched else instance.model),
        api_key=_decrypt(row.api_key_encrypted) or (instance.api_key if keeps_instance_key else ""),
        vision_setting=(row.vision if row.vision and row.vision != "auto" else instance.vision_setting),
        source="workspace" if (own_provider or row.model) else "instance",
    )


async def vision_for(resolved: ResolvedModel, *, probe: bool = True) -> VisionAnswer:
    return await resolve_vision(
        provider=resolved.provider,
        base_url=resolved.base_url,
        model=resolved.model,
        setting=resolved.vision_setting,
        probe=probe,
    )


def build_provider(resolved: ResolvedModel):
    from app.agents.providers.registry import build_provider as _build

    return _build(
        resolved.provider,
        api_key=resolved.api_key,
        base_url=resolved.base_url or None,
        model=resolved.model,
    )


async def save_workspace_settings(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    provider: Optional[str],
    base_url: Optional[str],
    model: Optional[str],
    api_key: Optional[str],
    clear_api_key: bool,
    vision: str,
) -> WorkspaceDocumentAiSettings:
    provider = (provider or "").strip().lower() or None
    if provider is not None and provider not in PROVIDERS:
        raise ValueError(f"Unknown provider: {provider}")
    vision = (vision or "auto").strip().lower()
    if vision not in VISION_SETTINGS:
        raise ValueError(f"Unknown vision setting: {vision}")
    row = await get_workspace_settings(session, workspace_id)
    if row is None:
        row = WorkspaceDocumentAiSettings(workspace_id=workspace_id)
        session.add(row)
    row.provider = provider
    row.base_url = (base_url or "").strip() or None
    row.model = (model or "").strip() or None
    if clear_api_key:
        row.api_key_encrypted = None
    elif api_key:
        row.api_key_encrypted = _encrypt(api_key.strip())
    row.vision = vision
    row.updated_by_user_id = user_id
    await session.commit()
    await session.refresh(row)
    return row


async def delete_workspace_settings(session: AsyncSession, workspace_id: uuid.UUID) -> None:
    await session.execute(
        delete(WorkspaceDocumentAiSettings).where(WorkspaceDocumentAiSettings.workspace_id == workspace_id)
    )
    await session.commit()
