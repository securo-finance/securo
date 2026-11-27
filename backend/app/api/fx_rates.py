from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.core.auth import current_active_user, current_superuser
from app.core.config import get_settings
from app.core.database import get_async_session
from app.models.fx_rate import FxRate
from app.models.user import User
from app.providers.openexchangerates import FxProviderQuotaExceeded
from app.services.fx_rate_service import (
    remember_quota_exceeded,
    reset_fx_failure_cache,
    sync_rates_for_refresh,
)
from app.services.provider_settings import resolve_settings

router = APIRouter(prefix="/api/fx-rates", tags=["fx-rates"])


@router.post("/refresh")
async def refresh_rates(
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_superuser),
):
    """Sync today's rates for the whole instance.

    An administrator action (issue #636): the app id belongs to the instance,
    so any account could spend the monthly quota of every customer and, on
    success, clear the 429 pause that protects their conversions.

    A day already stored is reused instead of fetched again: one admin in a
    loop must not spend the quota, and conversions read that same stored row.
    """
    if not (await resolve_settings(session)).openexchangerates_app_id:
        raise HTTPException(
            status_code=503,
            detail="Exchange rates are not configured. Ask an administrator to configure Open Exchange Rates in Settings.",
        )
    today = app_today()
    try:
        count, skipped = await sync_rates_for_refresh(session, today)
    except FxProviderQuotaExceeded:
        # The request was fine: the plan's quota is spent. Say so, and back
        # off the on-demand path too, so conversions stop retrying.
        remember_quota_exceeded(today)
        raise HTTPException(
            status_code=429,
            detail="The exchange-rate provider quota is exhausted. Rates will update again when the quota resets.",
        ) from None
    except ValueError:
        # The provider rejected the request (often a wrong app ID). The
        # original error is not returned: it may describe the credential.
        raise HTTPException(
            status_code=502,
            detail="Exchange rates could not be fetched. Check the Open Exchange Rates app ID in Settings.",
        ) from None
    if skipped:
        return {
            "synced": False,
            "skipped": True,
            "rates_count": count,
            "date": today.isoformat(),
        }
    # A person asked the provider again: forget the failures remembered while
    # the on-demand path was backing off.
    reset_fx_failure_cache()
    return {"synced": True, "rates_count": count, "date": today.isoformat()}


@router.get("/status")
async def rates_status(
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    """Return last sync date and total stored rates."""
    last_date = await session.scalar(
        select(func.max(FxRate.date))
    )
    total = await session.scalar(
        select(func.count()).select_from(FxRate)
    ) or 0

    settings = get_settings()
    return {
        "last_sync_date": last_date.isoformat() if last_date else None,
        "total_rates": total,
        "fx_sync_mode": settings.fx_sync_mode,
        "configured": bool((await resolve_settings(session)).openexchangerates_app_id),
    }
