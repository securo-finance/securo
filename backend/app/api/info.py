"""Public capability/feature-flag endpoint.

Tells the frontend which optional features are enabled so it can hide
nav items, routes, etc. Lightweight — no auth required.
"""
from fastapi import APIRouter

from app.core.feature_flags import feature_flag

router = APIRouter(prefix="/api", tags=["info"])


@router.get("/info")
async def get_app_info():
    return {
        "features": {
            "agents": feature_flag("AGENTS_ENABLED"),
            "tesouro_direto": feature_flag("TESOURO_DIRETO_ENABLED"),
            "plugins": feature_flag("PLUGINS_ENABLED"),
            # Document reading rides on the agents module (ADR 0037); each
            # workspace turns it on by picking one of its LLM connections.
            "document_ai": feature_flag("AGENTS_ENABLED"),
        },
    }
