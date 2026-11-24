"""Public capability/feature-flag endpoint.

Tells the frontend which optional features are enabled so it can hide
nav items, routes, etc. Lightweight — no auth required.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.core.feature_flags import feature_flag
from app.services.document_ai_settings_service import document_ai_enabled

router = APIRouter(prefix="/api", tags=["info"])


@router.get("/info")
async def get_app_info(session: AsyncSession = Depends(get_async_session)):
    return {
        "features": {
            "agents": feature_flag("AGENTS_ENABLED"),
            "tesouro_direto": feature_flag("TESOURO_DIRETO_ENABLED"),
            "plugins": feature_flag("PLUGINS_ENABLED"),
            # Turned on by an admin in the app, not the environment (ADR 0037).
            "document_ai": await document_ai_enabled(session),
        },
    }
