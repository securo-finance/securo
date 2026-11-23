"""Which model a workspace reads its documents with (ADR 0037).

The instance sets defaults through DOCUMENT_AI_*; a workspace owner may
replace any of them. A blank field falls back to the instance's value, so the
key an admin set for the whole instance is never shown to anyone and never
leaves the server.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.feature_flags import feature_flag
from app.models.document_extraction import WorkspaceDocumentAiSettings
from app.services.document_ai_capabilities import VisionAnswer, resolve_vision

PROVIDERS = ("ollama", "openai", "anthropic", "openai_compatible")
VISION_SETTINGS = ("auto", "true", "false")


def document_ai_enabled() -> bool:
    return feature_flag("DOCUMENT_AI_ENABLED")


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


async def get_workspace_settings(
    session: AsyncSession, workspace_id: uuid.UUID
) -> Optional[WorkspaceDocumentAiSettings]:
    return (
        await session.execute(
            select(WorkspaceDocumentAiSettings).where(WorkspaceDocumentAiSettings.workspace_id == workspace_id)
        )
    ).scalar_one_or_none()


def _decrypt(value: Optional[str]) -> str:
    if not value:
        return ""
    from app.agents.services.crypto import decrypt

    return decrypt(value) or ""


def _encrypt(value: str) -> Optional[str]:
    from app.agents.services.crypto import encrypt

    return encrypt(value)


async def resolve_model(session: AsyncSession, workspace_id: uuid.UUID) -> ResolvedModel:
    settings = get_settings()
    row = await get_workspace_settings(session, workspace_id)
    instance = ResolvedModel(
        provider=settings.document_ai_provider.strip().lower(),
        base_url=settings.document_ai_base_url.strip(),
        model=settings.document_ai_model.strip(),
        api_key=settings.document_ai_api_key.get_secret_value(),
        vision_setting=settings.document_ai_vision.strip().lower() or "auto",
        source="instance",
    )
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
    row = await get_workspace_settings(session, workspace_id)
    if row is not None:
        await session.delete(row)
        await session.commit()
