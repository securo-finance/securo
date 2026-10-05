from collections.abc import Iterable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import current_active_user
from app.core.config import get_settings
from app.core.database import get_async_session
from app.models.user import User
from app.services.fx_rate_service import _resolve_rate

router = APIRouter(prefix="/api/settings", tags=["settings"])

# Keeps the dashboard switcher a row of chips rather than an overflow menu.
MAX_QUICK_CURRENCIES = 6


class CurrencyPreferences(BaseModel):
    currency_display: str
    quick_currencies: list[str]


class DisplayCurrencyUpdate(BaseModel):
    currency: str


class QuickCurrenciesUpdate(BaseModel):
    currencies: list[str]


def _supported_codes() -> list[str]:
    return [c.strip().upper() for c in get_settings().supported_currencies.split(",") if c.strip()]


def _read_prefs(user: User) -> CurrencyPreferences:
    prefs = user.preferences or {}
    display = prefs.get("currency_display", get_settings().default_currency)
    # Unset means "no shortlist chosen yet" — seed it with the active currency
    # so the settings UI has something to show and the dashboard switcher
    # stays hidden until a second currency is picked.
    quick = prefs.get("quick_currencies") or [display]
    return CurrencyPreferences(currency_display=display, quick_currencies=quick)


def _normalize(codes: Iterable[str]) -> list[str]:
    """Upper-case and de-duplicate while preserving the caller's order."""
    out: list[str] = []
    for raw in codes:
        code = raw.strip().upper()
        if code and code not in out:
            out.append(code)
    return out


def _require_supported(codes: Iterable[str]) -> None:
    supported = _supported_codes()
    unknown = [c for c in codes if c not in supported]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unsupported currency: {', '.join(unknown)}")


async def _require_convertible(session: AsyncSession, code: str) -> None:
    """Reject a switch we could only render with fabricated 1:1 numbers.

    ``get_rate`` deliberately falls back to 1:1 so live reads still render
    something, which for a currency switch would relabel unconverted amounts
    in the new symbol. ``_resolve_rate`` returns None honestly instead.
    """
    if await _resolve_rate(session, "USD", code, None, allow_fetch=True) is None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"No FX rate available for {code}. Check the FX provider "
                "configuration, then try again."
            ),
        )


def _keep_display_reachable(prefs: dict) -> dict:
    """Ensure the active currency is always offered by the switcher.

    Without this, switching to a currency outside the shortlist would leave
    the dashboard with no chip to switch back from.
    """
    display = prefs.get("currency_display", get_settings().default_currency)
    quick = prefs.get("quick_currencies") or []
    if quick and display not in quick and len(quick) < MAX_QUICK_CURRENCIES:
        prefs["quick_currencies"] = [*quick, display]
    return prefs


async def _save(session: AsyncSession, user: User, prefs: dict) -> CurrencyPreferences:
    user.preferences = _keep_display_reachable(prefs)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return _read_prefs(user)


@router.get("/attachments")
async def get_attachment_settings():
    """Return attachment configuration for this instance."""
    settings = get_settings()
    allowed = [ext.strip().lower() for ext in settings.storage_allowed_extensions.split(",") if ext.strip()]
    return {
        "allowed_extensions": allowed,
        "max_file_size_mb": settings.storage_max_file_size_mb,
        "max_attachments_per_transaction": settings.storage_max_attachments_per_transaction,
        "max_attachments_per_invoice": settings.storage_max_attachments_per_invoice,
    }


@router.get("/currency-preferences", response_model=CurrencyPreferences)
async def get_currency_preferences(user: User = Depends(current_active_user)):
    """Return the active display currency and the quick-switch shortlist."""
    return _read_prefs(user)


@router.put("/currency-preferences/display", response_model=CurrencyPreferences)
async def set_display_currency(
    body: DisplayCurrencyUpdate,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    """Switch the display currency.

    Drives server-side conversion of every aggregate
    (``User.primary_currency``), so callers must treat the response as
    invalidating every cached amount — they were all converted in the
    previous currency.
    """
    code = _normalize([body.currency])
    if not code:
        raise HTTPException(status_code=400, detail="Currency is required")
    _require_supported(code)

    prefs = dict(user.preferences or {})
    if code[0] != prefs.get("currency_display"):
        await _require_convertible(session, code[0])
    prefs["currency_display"] = code[0]
    return await _save(session, user, prefs)


@router.put("/currency-preferences/quick-list", response_model=CurrencyPreferences)
async def set_quick_currencies(
    body: QuickCurrenciesUpdate,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    """Replace the quick-switch shortlist.

    A pure preference write: it changes which chips the dashboard offers, not
    the currency amounts are converted into, so no FX lookup is needed.
    """
    codes = _normalize(body.currencies)
    _require_supported(codes)
    if len(codes) > MAX_QUICK_CURRENCIES:
        raise HTTPException(
            status_code=400, detail=f"At most {MAX_QUICK_CURRENCIES} quick currencies"
        )

    prefs = dict(user.preferences or {})
    prefs["quick_currencies"] = codes
    return await _save(session, user, prefs)
