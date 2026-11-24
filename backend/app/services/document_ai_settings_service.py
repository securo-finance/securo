"""Which LLM connection reads a workspace's documents (ADR 0037).

Document reading is part of the agents module: it is available while
AGENTS_ENABLED is on, and it reads with one of the LLM connections people
already manage under Agents (provider, URL, key and default model live
there, nowhere else). A workspace owner picks one of their own connections
for the workspace, from the workspace settings or from the connection
itself; until one is picked, reading is off for that workspace.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.feature_flags import feature_flag
from app.models.document_extraction import WorkspaceDocumentAiSettings
from app.models.workspace import WorkspaceMember
from app.services.document_ai_capabilities import VisionAnswer, resolve_vision

VISION_SETTINGS = ("auto", "true", "false")


def document_ai_available() -> bool:
    """Whether the agents module, and with it document reading, is on."""
    return feature_flag("AGENTS_ENABLED")


@dataclass
class ResolvedModel:
    provider: str
    base_url: str
    model: str
    api_key: str
    vision_setting: str
    connection_id: Optional[uuid.UUID] = None
    connection_name: Optional[str] = None
    connection_owner_id: Optional[uuid.UUID] = None

    @property
    def configured(self) -> bool:
        return self.connection_id is not None and bool(self.provider) and bool(self.model)


OFF = ResolvedModel(provider="", base_url="", model="", api_key="", vision_setting="auto")


async def get_workspace_settings(
    session: AsyncSession, workspace_id: uuid.UUID
) -> Optional[WorkspaceDocumentAiSettings]:
    return (
        await session.execute(
            select(WorkspaceDocumentAiSettings).where(WorkspaceDocumentAiSettings.workspace_id == workspace_id)
        )
    ).scalar_one_or_none()


async def _connection(session: AsyncSession, connection_id: uuid.UUID):
    from app.agents.models.connection import LlmConnection

    return (
        await session.execute(select(LlmConnection).where(LlmConnection.id == connection_id))
    ).scalar_one_or_none()


async def _is_member(session: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    from app.models.workspace import Workspace

    member = (
        await session.execute(
            select(WorkspaceMember.id).where(
                WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if member is not None:
        return True
    manager = (
        await session.execute(select(Workspace.managed_by_user_id).where(Workspace.id == workspace_id))
    ).scalar_one_or_none()
    return manager == user_id


async def resolve_model(session: AsyncSession, workspace_id: uuid.UUID) -> ResolvedModel:
    """The connection and model this workspace reads with, or OFF."""
    if not document_ai_available():
        return OFF
    row = await get_workspace_settings(session, workspace_id)
    if row is None or row.connection_id is None:
        return OFF
    conn = await _connection(session, row.connection_id)
    # A connection deleted since, or one whose owner left the workspace, no
    # longer reads for it: their key is theirs, not the workspace's.
    if conn is None or not await _is_member(session, workspace_id, conn.user_id):
        return OFF
    from app.agents.services.crypto import decrypt

    return ResolvedModel(
        provider=conn.kind,
        base_url=conn.base_url or "",
        model=(row.model or "").strip() or (conn.default_model or ""),
        api_key=decrypt(conn.api_key_encrypted) or "",
        vision_setting=row.vision or "auto",
        connection_id=conn.id,
        connection_name=conn.name,
        connection_owner_id=conn.user_id,
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
    connection_id: Optional[uuid.UUID],
    model: Optional[str],
    vision: str,
) -> WorkspaceDocumentAiSettings:
    """Pick one of the user's own connections for the workspace (None: off)."""
    vision = (vision or "auto").strip().lower()
    if vision not in VISION_SETTINGS:
        raise ValueError(f"Unknown vision setting: {vision}")
    if connection_id is not None:
        conn = await _connection(session, connection_id)
        if conn is None or conn.user_id != user_id:
            raise LookupError("Connection not found")
    row = await get_workspace_settings(session, workspace_id)
    if row is None:
        row = WorkspaceDocumentAiSettings(workspace_id=workspace_id)
        session.add(row)
    row.connection_id = connection_id
    row.model = (model or "").strip() or None
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
