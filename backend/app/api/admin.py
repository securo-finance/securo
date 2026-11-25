import json
import re
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import AfterValidator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import UserManager, current_active_user, current_superuser, get_user_manager
from app.core.auth_policy import require_local_auth_enabled
from app.core.config import get_settings
from app.core.database import get_async_session
from app.core.rate_limit import client_ip
from app.core.app_clock import (
    app_timezone,
    environment_timezone,
    invalidate_timezone_cache,
    is_valid_timezone,
    saved_timezone_name,
    timezone_names,
)
from app.models.user import User
from app.schemas.admin import (
    AdminAccessLogList,
    AdminAccessLogRead,
    AdminUserCreate,
    AdminUserList,
    AdminUserRead,
    AdminUserUpdate,
    AppSettingRead,
    AppSettingUpdate,
    EmailStateRead,
    ProviderSettingRead,
    TimezoneSettingRead,
    clean_reason,
)
from app.services import admin_service
from app.services import email_service
from app.services import provider_settings

router = APIRouter(prefix="/api/admin", tags=["admin"])

ALLOWED_SETTINGS = {
    "registration_enabled",
    "credit_card_accounting_mode",
    "use_provider_categories",
    "theme_color_light",
    "theme_color_dark",
    "number_format",
    "date_format",
    "timezone",
}


@router.get("/users", response_model=AdminUserList)
async def list_users(
    search: str = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    session: AsyncSession = Depends(get_async_session),
    _user: User = Depends(current_superuser),
):
    users, total = await admin_service.list_users(session, search, page, limit)
    return AdminUserList(
        items=[AdminUserRead.model_validate(u) for u in users],
        total=total,
    )


@router.post(
    "/users",
    response_model=AdminUserRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_local_auth_enabled)],
)
async def create_user(
    data: AdminUserCreate,
    session: AsyncSession = Depends(get_async_session),
    _user: User = Depends(current_superuser),
    user_manager: UserManager = Depends(get_user_manager),
):
    try:
        user = await admin_service.create_user(session, user_manager, data)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return AdminUserRead.model_validate(user)


@router.get("/users/{user_id}", response_model=AdminUserRead)
async def get_user(
    user_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    _user: User = Depends(current_superuser),
):
    user = await admin_service.get_user(session, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return AdminUserRead.model_validate(user)


@router.patch("/users/{user_id}", response_model=AdminUserRead)
async def update_user(
    user_id: uuid.UUID,
    data: AdminUserUpdate,
    request: Request,
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_superuser),
):
    try:
        user = await admin_service.update_user(
            session, user_id, data, current_user.id, ip=client_ip(request)
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return AdminUserRead.model_validate(user)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: uuid.UUID,
    request: Request,
    # Same rule as the body of an update: trimmed, never blank, no NUL (#557).
    reason: Annotated[str, Query(...), AfterValidator(clean_reason)],
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_superuser),
):
    try:
        deleted = await admin_service.delete_user(
            session, user_id, current_user.id, reason=reason, ip=client_ip(request)
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")


@router.get("/access-log", response_model=AdminAccessLogList)
async def access_log(
    user_id: uuid.UUID | None = Query(None, description="Only this account"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    session: AsyncSession = Depends(get_async_session),
    _user: User = Depends(current_superuser),
):
    """Every administrative action on an account, newest first."""
    entries, total = await admin_service.list_access_log(
        session, target_user_id=user_id, page=page, limit=limit
    )
    return AdminAccessLogList(
        items=[AdminAccessLogRead.model_validate(e) for e in entries],
        total=total,
    )


@router.get("/settings/{key}", response_model=AppSettingRead)
async def get_setting(
    key: str,
    session: AsyncSession = Depends(get_async_session),
    _user: User = Depends(current_superuser),
):
    if key in provider_settings.SETTING_FIELDS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Setting not found")
    setting = await admin_service.get_app_setting(session, key)
    if not setting:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Setting not found")
    return AppSettingRead.model_validate(setting)


@router.get("/provider-settings", response_model=list[ProviderSettingRead])
async def get_provider_settings(
    session: AsyncSession = Depends(get_async_session),
    _user: User = Depends(current_superuser),
):
    return await provider_settings.provider_status(session)


@router.patch("/provider-settings/{provider}", response_model=ProviderSettingRead)
async def update_provider_settings(
    provider: str,
    request: Request,
    session: AsyncSession = Depends(get_async_session),
    _user: User = Depends(current_superuser),
):
    # Parse explicitly so schema validation errors cannot echo submitted secrets.
    try:
        body = await request.json()
        if not isinstance(body, dict) or set(body) != {"values"} or not isinstance(body["values"], dict):
            raise ValueError("Invalid provider settings")
        return await provider_settings.update_provider(session, provider, body["values"])
    except provider_settings.SecretStorageUnavailable as exc:
        # Fixed server-side text, never the submitted values.
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid provider settings") from None


@router.patch("/settings/{key}", response_model=AppSettingRead)
async def update_setting(
    key: str,
    data: AppSettingUpdate,
    session: AsyncSession = Depends(get_async_session),
    _user: User = Depends(current_superuser),
):
    if key not in ALLOWED_SETTINGS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Setting '{key}' is not configurable",
        )
    if key == "timezone" and not is_valid_timezone(data.value):
        raise HTTPException(status_code=400, detail="Invalid IANA timezone")
    SETTING_VALIDATORS = {
        "registration_enabled": {"true", "false"},
        "credit_card_accounting_mode": {"cash", "accrual"},
        "use_provider_categories": {"true", "false"},
        "number_format": {"auto", "comma_dot", "dot_comma", "space_comma"},
        "date_format": {"auto", "dmy", "mdy", "ymd"},
    }

    if key in SETTING_VALIDATORS and data.value not in SETTING_VALIDATORS[key]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid value for '{key}'. Allowed: {SETTING_VALIDATORS[key]}",
        )

    if key in ("theme_color_light", "theme_color_dark"):
        if not re.match(r"^#[0-9A-Fa-f]{6}$", data.value):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid hex color code for '{key}'. Expected format: #RRGGBB",
            )

    setting = await admin_service.set_app_setting(session, key, data.value)
    if key == "timezone":
        invalidate_timezone_cache()
    return AppSettingRead.model_validate(setting)


@router.delete("/settings/{key}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_setting(
    key: str,
    session: AsyncSession = Depends(get_async_session),
    _user: User = Depends(current_superuser),
):
    """Forget a saved setting so the application falls back to its default."""
    if key not in ALLOWED_SETTINGS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Setting '{key}' is not configurable",
        )
    await admin_service.delete_app_setting(session, key)
    if key == "timezone":
        invalidate_timezone_cache()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _email_state(session: AsyncSession) -> EmailStateRead:
    """The state both endpoints answer with, read after any change."""
    settings = get_settings()
    saved = await admin_service.get_email_templates(session)
    return EmailStateRead(
        enabled=settings.email_enabled,
        provider=settings.email_provider,
        sender=settings.email_from or settings.smtp_username,
        templates=email_service.effective_templates(saved),
    )


@router.get("/email", response_model=EmailStateRead)
async def email_state(
    session: AsyncSession = Depends(get_async_session),
    _user: User = Depends(current_superuser),
):
    """How outbound email is set up: the switch, the provider, the sender and
    the templates the recovery message would use. No secret is returned."""
    return await _email_state(session)


@router.put("/email", response_model=EmailStateRead)
async def update_email_state(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
    _user: User = Depends(current_superuser),
):
    """Replace the recovery templates, or clear them back to the built-in text.

    The body is read here instead of through a model so a broken template
    answers 400 with what is wrong, rather than a schema error nobody can act
    on. Nothing is saved unless every template sent validates.
    """
    try:
        body = json.loads(await request.body())
    except (ValueError, RecursionError):
        # RecursionError too: JSON nested past the parser's stack is a broken
        # body, and letting it escape answers 500 instead of saying so.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The body must be a JSON object",
        )
    # The field is required, even when empty: {} is a body that forgot it, and
    # clearing is explicit ({"templates": {}}), so an empty object must not
    # wipe the templates in use by accident.
    if not isinstance(body, dict) or "templates" not in body or set(body) - {"templates"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The body takes one field, templates, keyed by language",
        )
    try:
        templates = email_service.validate_templates(body["templates"])
    except email_service.TemplateError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    await admin_service.save_email_templates(session, templates)
    return await _email_state(session)


@router.get("/timezone", response_model=TimezoneSettingRead)
async def timezone_setting(
    session: AsyncSession = Depends(get_async_session),
    _user: User = Depends(current_superuser),
):
    return TimezoneSettingRead(
        timezone=str(app_timezone()),
        saved=await saved_timezone_name(session),
        fallback=str(environment_timezone()),
        available=sorted(timezone_names()),
    )


@router.get("/registration-status")
async def registration_status(
    session: AsyncSession = Depends(get_async_session),
):
    enabled = await admin_service.is_registration_enabled(session)
    return {"enabled": enabled}


@router.get("/default-colors")
async def default_colors(
    session: AsyncSession = Depends(get_async_session),
):
    light = await admin_service.get_app_setting(session, "theme_color_light")
    dark = await admin_service.get_app_setting(session, "theme_color_dark")
    return {"light": light.value if light else None, "dark": dark.value if dark else None}


@router.get("/accounting-mode")
async def accounting_mode(
    session: AsyncSession = Depends(get_async_session),
    _user: User = Depends(current_active_user),
):
    mode = await admin_service.get_credit_card_accounting_mode(session)
    return {"mode": mode}


@router.get("/number-format")
async def number_format(
    session: AsyncSession = Depends(get_async_session),
    _user: User = Depends(current_active_user),
):
    """Global display format for numbers and dates. Readable by any signed-in
    user (not just admins) so the frontend can format consistently for everyone.
    Defaults to 'auto' — derive separators from each user's display currency."""
    fmt = await admin_service.get_number_format(session)
    return {"format": fmt}


@router.get("/date-format")
async def date_format(
    session: AsyncSession = Depends(get_async_session),
    _user: User = Depends(current_active_user),
):
    """Global display format for dates. Readable by any signed-in user.
    Defaults to 'auto' — derive the field order from the number format /
    display currency. Month names always follow the user's app language."""
    fmt = await admin_service.get_date_format(session)
    return {"format": fmt}


async def check_registration_enabled(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
):
    enabled = await admin_service.is_registration_enabled(session)
    if not enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Registration is currently disabled",
        )
