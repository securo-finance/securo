"""Public capability/feature-flag endpoint.

Tells the frontend which optional features are enabled so it can hide
nav items, routes, etc. Lightweight — no auth required.
"""
from fastapi import APIRouter

from app.core.config import get_settings
from app.core.feature_flags import feature_flag

router = APIRouter(prefix="/api", tags=["info"])


@router.get("/info")
async def get_app_info():
    return {
        "features": {
            "agents": feature_flag("AGENTS_ENABLED"),
            "tesouro_direto": feature_flag("TESOURO_DIRETO_ENABLED"),
            # Read from settings, like the routes that enforce it, so a value
            # set only in backend/.env reaches the frontend too.
            "performance_benchmarks": get_settings().performance_benchmarks_enabled,
        },
    }
