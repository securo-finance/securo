import json
import logging
import uuid
from typing import Optional

from fastapi_users.exceptions import UserAlreadyExists
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import UserManager
from app.core.config import get_settings
from app.models.app_settings import AppSetting
from app.models.user import User
from app.schemas.admin import AdminUserCreate, AdminUserUpdate
from app.services import email_service, user_removal_service

logger = logging.getLogger(__name__)


async def list_users(
    session: AsyncSession,
    search: Optional[str] = None,
    page: int = 1,
    limit: int = 50,
) -> tuple[list[User], int]:
    query = select(User).where(user_removal_service.not_removed())
    count_query = (
        select(func.count()).select_from(User).where(user_removal_service.not_removed())
    )

    if search:
        query = query.where(User.email.ilike(f"%{search}%"))
        count_query = count_query.where(User.email.ilike(f"%{search}%"))

    total = (await session.execute(count_query)).scalar() or 0

    query = query.order_by(User.email).offset((page - 1) * limit).limit(limit)
    result = await session.execute(query)
    users = list(result.scalars().all())

    return users, total


async def get_user(session: AsyncSession, user_id: uuid.UUID) -> Optional[User]:
    result = await session.execute(
        select(User).where(User.id == user_id, user_removal_service.not_removed())
    )
    return result.scalar_one_or_none()


async def create_user(
    session: AsyncSession,
    user_manager: UserManager,
    data: AdminUserCreate,
) -> User:
    from decimal import Decimal

    from fastapi_users.schemas import BaseUserCreate

    from app.models.account import Account
    from app.services.category_service import create_default_categories
    from app.services.rule_service import create_default_rules
    from app.services.workspace_service import create_personal_workspace_for_user

    create_schema = BaseUserCreate(
        email=data.email,
        password=data.password,
        is_superuser=data.is_superuser,
        is_active=True,
        is_verified=True,
    )

    try:
        user = await user_manager.create(create_schema)
    except UserAlreadyExists:
        raise ValueError("A user with this email already exists")

    # Set preferences if provided
    if data.preferences:
        user.preferences = data.preferences
        session.add(user)
        await session.commit()
        await session.refresh(user)

    # Seed default wallet, categories, and rules. The on_after_register
    # hook (auth.py) short-circuits when called programmatically with
    # request=None — which is exactly this path — so admin-created users
    # would otherwise land with no defaults and a broken UX.
    lang = (user.preferences or {}).get("language", "en")
    # Every new user gets a Personal workspace + owner membership so the
    # seeded wallet/categories/rules below have somewhere to live.
    workspace = await create_personal_workspace_for_user(session, user)
    await session.commit()

    wallet_name = "Carteira" if lang.startswith("pt") else "Wallet"
    wallet = Account(
        user_id=user.id,
        workspace_id=workspace.id,
        name=wallet_name,
        type="checking",
        balance=Decimal("0.00"),
        currency=user.primary_currency,
    )
    session.add(wallet)
    await session.commit()

    await create_default_categories(session, user.id, lang, workspace_id=workspace.id)
    await create_default_rules(session, user.id, lang, workspace_id=workspace.id)

    return user


async def update_user(
    session: AsyncSession,
    user_id: uuid.UUID,
    data: AdminUserUpdate,
    current_user_id: uuid.UUID,
) -> Optional[User]:
    user = await get_user(session, user_id)
    if not user:
        return None

    # Self-protection: can't demote or deactivate self
    if user_id == current_user_id:
        if data.is_superuser is False:
            raise ValueError("Cannot remove your own admin privileges")
        if data.is_active is False:
            raise ValueError("Cannot deactivate your own account")

    if data.email is not None and data.email != user.email:
        existing = await session.execute(
            select(User).where(User.email == data.email)
        )
        if existing.scalar_one_or_none():
            raise ValueError("A user with this email already exists")
        user.email = data.email
    if data.is_active is not None:
        user.is_active = data.is_active
    if data.is_superuser is not None:
        user.is_superuser = data.is_superuser
    if data.preferences is not None:
        user.preferences = data.preferences
    if data.password is not None:
        from fastapi_users.password import PasswordHelper

        password_helper = PasswordHelper()
        user.hashed_password = password_helper.hash(data.password)

    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def delete_user(
    session: AsyncSession,
    user_id: uuid.UUID,
    current_user_id: uuid.UUID,
) -> bool:
    if user_id == current_user_id:
        raise ValueError("Cannot delete your own account")

    # Locked: parallel deletions queue, and the next finds nothing left. It also
    # holds back rows being written in their name until this commits.
    user = await session.scalar(
        select(User)
        .where(User.id == user_id, user_removal_service.not_removed())
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not user:
        return False

    # Check last superuser protection
    if user.is_superuser:
        count_result = await session.execute(
            select(func.count()).select_from(User).where(
                User.is_superuser == True,  # noqa: E712
                User.is_active == True,  # noqa: E712
            )
        )
        superuser_count = count_result.scalar() or 0
        if superuser_count <= 1:
            raise ValueError("Cannot delete the last admin user")

    await user_removal_service.remove_user(session, user)
    return True


async def get_app_setting(session: AsyncSession, key: str) -> Optional[AppSetting]:
    result = await session.execute(
        select(AppSetting).where(AppSetting.key == key)
    )
    return result.scalar_one_or_none()


async def delete_app_setting(session: AsyncSession, key: str) -> bool:
    """Remove a saved setting. Returns False when nothing was saved."""
    setting = await get_app_setting(session, key)
    if setting is None:
        return False
    await session.delete(setting)
    await session.commit()
    return True


async def set_app_setting(session: AsyncSession, key: str, value: str) -> AppSetting:
    setting = await get_app_setting(session, key)
    if setting:
        setting.value = value
    else:
        setting = AppSetting(key=key, value=value)
    session.add(setting)
    await session.commit()
    await session.refresh(setting)
    return setting


EMAIL_TEMPLATES_KEY = "email_templates"


async def get_email_templates(session: AsyncSession) -> dict[str, dict[str, str]]:
    """The recovery templates an administrator saved, or nothing.

    A value edited by hand in the database is dropped instead of raising: the
    person asking for their account back must still get the built-in text.
    """
    setting = await get_app_setting(session, EMAIL_TEMPLATES_KEY)
    if setting is None:
        return {}
    try:
        return email_service.parse_templates(setting.value)
    except email_service.TemplateError:
        logger.warning("Stored email templates no longer validate; using the built-in text")
        return {}


async def save_email_templates(
    session: AsyncSession, templates: dict[str, dict[str, str]]
) -> None:
    """Store the templates, or clear them back to the built-in text."""
    if templates:
        await set_app_setting(
            session,
            EMAIL_TEMPLATES_KEY,
            json.dumps(templates, ensure_ascii=False, sort_keys=True),
        )
    else:
        await delete_app_setting(session, EMAIL_TEMPLATES_KEY)


async def is_registration_enabled(session: AsyncSession) -> bool:
    """Whether public sign-up is open.

    The value an administrator saved wins; with nothing saved, the
    REGISTRATION_ENABLED environment variable decides."""
    setting = await get_app_setting(session, "registration_enabled")
    if setting:
        return setting.value.lower() == "true"
    return get_settings().registration_enabled


async def get_credit_card_accounting_mode(session: AsyncSession) -> str:
    """Return the global CC accounting mode: 'cash' or 'accrual'.

    Global app setting — affects how dashboard/reports/budgets bucket credit
    card transactions. Defaults to 'cash' (current behavior) when unset."""
    setting = await get_app_setting(session, "credit_card_accounting_mode")
    if setting and setting.value in ("cash", "accrual"):
        return setting.value
    return "cash"


async def get_number_format(session: AsyncSession) -> str:
    """Return the global number/date display format.

    Global app setting controlling thousands/decimal separators (and, on the
    frontend, the locale used for date display). One of 'auto', 'comma_dot'
    (1,000.00), 'dot_comma' (1.000,00) or 'space_comma' (1 000,00). Defaults to
    'auto', which derives the format from the user's display currency."""
    setting = await get_app_setting(session, "number_format")
    if setting and setting.value in ("auto", "comma_dot", "dot_comma", "space_comma"):
        return setting.value
    return "auto"


async def get_date_format(session: AsyncSession) -> str:
    """Return the global date display format.

    Global app setting controlling the date field order. One of 'auto'
    (derive from the number format / currency), 'dmy' (DD/MM/YYYY), 'mdy'
    (MM/DD/YYYY) or 'ymd' (YYYY-MM-DD). Month names follow the user's app
    language regardless. Defaults to 'auto'."""
    setting = await get_app_setting(session, "date_format")
    if setting and setting.value in ("auto", "dmy", "mdy", "ymd"):
        return setting.value
    return "auto"


async def use_provider_categories(session: AsyncSession) -> bool:
    """Whether sync should map provider-supplied categories (e.g. Pluggy's
    `category` field) onto the user's seeded categories via PLUGGY_CATEGORY_MAP.

    Global app setting — affects every connection's sync. Defaults to True
    (preserves current behavior). Admins can flip it off when the provider's
    classification is consistently wrong and they'd rather have everything
    arrive uncategorized so user-defined Rules are the only source of truth."""
    setting = await get_app_setting(session, "use_provider_categories")
    if setting:
        return setting.value.lower() == "true"
    return True
