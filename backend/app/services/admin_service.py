import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi_users.exceptions import UserAlreadyExists
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import UserManager
from app.core.config import get_settings
from app.models.admin_access_log import AdminAccessLog, AdminAccessLogViewer
from app.models.app_settings import AppSetting
from app.models.user import User
from app.schemas.admin import AdminUserCreate, AdminUserUpdate
from app.services import account_lookup_service, email_service

logger = logging.getLogger(__name__)


async def list_users(
    session: AsyncSession,
    search: Optional[str] = None,
    page: int = 1,
    limit: int = 50,
) -> tuple[list[User], int]:
    query = select(User)
    count_query = select(func.count()).select_from(User)

    if search:
        query = query.where(User.email.ilike(f"%{search}%"))
        count_query = count_query.where(User.email.ilike(f"%{search}%"))

    total = (await session.execute(count_query)).scalar() or 0

    query = query.order_by(User.email).offset((page - 1) * limit).limit(limit)
    result = await session.execute(query)
    users = list(result.scalars().all())

    return users, total


async def get_user(session: AsyncSession, user_id: uuid.UUID) -> Optional[User]:
    result = await session.execute(select(User).where(User.id == user_id))
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
    *,
    ip: Optional[str] = None,
) -> Optional[User]:
    # Locked, not just read: a deletion of the same account can commit between
    # this read and the record this call writes, and the record's foreign key
    # then fails with a 500 (#567). Taking the row first makes the two queue;
    # if the deletion went first, this read finds nothing and the route
    # answers 404.
    locked = await session.execute(
        select(User).where(User.id == user_id).with_for_update()
    )
    user = locked.scalar_one_or_none()
    if not user:
        return None

    # Self-protection: can't demote or deactivate self
    if user_id == current_user_id:
        if data.is_superuser is False:
            raise ValueError("Cannot remove your own admin privileges")
        if data.is_active is False:
            raise ValueError("Cannot deactivate your own account")

    # What the record shows: every field this call changes, old value and new
    # (#556). `target_email` is the account's name before it, not after.
    changes: dict[str, dict] = {}
    previous_email = user.email

    if data.email is not None:
        requested = str(data.email)
        # Addresses are compared without regard to case, the way sign-in
        # resolves them: `Taken@example.com` and `taken@example.com` reach the
        # same mailbox, so they must name the same account here too (#556).
        if requested.lower() != user.email.lower():
            # One address, one account, also when the instance still holds two
            # rows for it in different case (#568): asking whether it is taken
            # answers 400, never a 500 out of a lookup that found two.
            taken = await account_lookup_service.find_account_by_email(
                session, requested, exclude_user_id=user_id
            )
            if taken is not None:
                raise ValueError("A user with this email already exists")
            waiting = await session.execute(
                select(User.id)
                .where(
                    func.lower(User.pending_email) == requested.lower(),
                    User.id != user_id,
                )
                .limit(1)
            )
            if waiting.first() is not None:
                raise ValueError("Another account is already waiting to confirm this address")
            # The address is a recovery credential, so the administrator asks
            # and the owner decides: the login address does not move until the
            # person whose account it is confirms from their own session.
            user.pending_email = requested
            user.pending_email_requested_at = datetime.now(timezone.utc)
            changes["email"] = {
                "from": previous_email,
                "to": requested,
                "pending": True,
            }
        elif user.pending_email is not None:
            # Asked back to the address it already has: the request is over.
            changes["email"] = {
                "from": user.pending_email,
                "to": user.email,
                "pending": False,
            }
            user.pending_email = None
            user.pending_email_requested_at = None
    if data.is_active is not None:
        if data.is_active != user.is_active:
            changes["is_active"] = {"from": user.is_active, "to": data.is_active}
        user.is_active = data.is_active
    if data.is_superuser is not None:
        if data.is_superuser != user.is_superuser:
            changes["is_superuser"] = {"from": user.is_superuser, "to": data.is_superuser}
        user.is_superuser = data.is_superuser
    if data.preferences is not None:
        changes["preferences"] = {"changed": True}
        user.preferences = data.preferences

    await _record_admin_access(
        session,
        current_user_id,
        user,
        "user.update",
        data.reason,
        ip,
        target_email=previous_email,
        changes=changes or None,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def delete_user(
    session: AsyncSession,
    user_id: uuid.UUID,
    current_user_id: uuid.UUID,
    *,
    reason: str,
    ip: Optional[str] = None,
) -> bool:
    if user_id == current_user_id:
        raise ValueError("Cannot delete your own account")

    # Locked, not just read: two deletions of the same account used to both
    # find it and both write their record, and the loser died on the log's
    # foreign key with a 500 (#557). Taking the row first makes them queue, so
    # the second one finds nothing left and answers 404 — which is what a
    # deletion of something already gone means.
    locked = await session.execute(
        select(User).where(User.id == user_id).with_for_update()
    )
    user = locked.scalar_one_or_none()
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

    # Cascade delete user data in correct order
    from app.models.transaction_attachment import TransactionAttachment
    from app.models.transaction import Transaction
    from app.models.budget import Budget
    from app.models.recurring_transaction import RecurringTransaction
    from app.models.import_log import ImportLog
    from app.models.rule import Rule
    from app.models.asset_value import AssetValue
    from app.models.asset import Asset
    from app.models.payee import PayeeMapping, Payee
    from app.models.account import Account
    from app.models.category import Category
    from app.models.category_group import CategoryGroup
    from app.models.bank_connection import BankConnection
    from app.models.invoice import Invoice
    from app.models.invoice_schedule import InvoiceSchedule

    # Delete attachments for user's transactions
    tx_ids_query = select(Transaction.id).where(Transaction.user_id == user_id)
    await session.execute(
        delete(TransactionAttachment).where(
            TransactionAttachment.transaction_id.in_(tx_ids_query)
        )
    )

    # Delete in dependency order
    await session.execute(delete(Transaction).where(Transaction.user_id == user_id))
    await session.execute(delete(Budget).where(Budget.user_id == user_id))
    await session.execute(delete(RecurringTransaction).where(RecurringTransaction.user_id == user_id))
    await session.execute(delete(ImportLog).where(ImportLog.user_id == user_id))
    await session.execute(delete(Rule).where(Rule.user_id == user_id))

    # Asset values depend on assets
    asset_ids_query = select(Asset.id).where(Asset.user_id == user_id)
    await session.execute(
        delete(AssetValue).where(AssetValue.asset_id.in_(asset_ids_query))
    )
    await session.execute(delete(Asset).where(Asset.user_id == user_id))

    await session.execute(delete(PayeeMapping).where(PayeeMapping.user_id == user_id))
    # Billing records belong to the workspace and RESTRICT client deletion.
    # Keep their clients and let the creator FK clear when the user is deleted;
    # unrelated user payees retain the existing cleanup behavior.
    await session.execute(
        delete(Payee).where(
            Payee.user_id == user_id,
            ~select(Invoice.id).where(Invoice.payee_id == Payee.id).exists(),
            ~select(InvoiceSchedule.id).where(InvoiceSchedule.payee_id == Payee.id).exists(),
        )
    )
    await session.execute(delete(Account).where(Account.user_id == user_id))
    await session.execute(delete(Category).where(Category.user_id == user_id))
    await session.execute(delete(CategoryGroup).where(CategoryGroup.user_id == user_id))
    await session.execute(delete(BankConnection).where(BankConnection.user_id == user_id))

    # The log row goes in the same commit as the deletion: either the account
    # is gone and the record says why, or neither happened.
    await _record_admin_access(session, current_user_id, user, "user.delete", reason, ip)

    # Delete the user
    await session.delete(user)
    await session.commit()
    return True


async def _record_admin_access(
    session: AsyncSession,
    actor_id: uuid.UUID,
    target: User,
    action: str,
    reason: str,
    ip: Optional[str],
    *,
    target_email: Optional[str] = None,
    changes: Optional[dict] = None,
) -> AdminAccessLog:
    """Add the audit row to the session. The caller commits it with the change.

    ``target_email`` is the address the account had when the action was taken;
    the caller passes it because the update already moved what the row would
    otherwise read. ``changes`` names every field it touched, old and new.
    """
    actor = await get_user(session, actor_id)
    entry = AdminAccessLog(
        id=uuid.uuid4(),
        actor_id=actor_id,
        actor_email=actor.email if actor else "unknown",
        target_user_id=target.id,
        target_email=target_email if target_email is not None else target.email,
        action=action,
        changes=changes,
        reason=reason,
        ip=ip,
    )
    session.add(entry)
    for viewer_id in await _audience_for(session, target):
        session.add(AdminAccessLogViewer(entry_id=entry.id, viewer_id=viewer_id))
    return entry


async def _audience_for(session: AsyncSession, target: User) -> set[uuid.UUID]:
    """Who may read a record about `target`: the target, its owners and managers.

    A manager acts with owner rights without being on the roster (ADR 0003), so
    they are named here too: their access cannot be derived from a membership.

    Read now and written with the record, because a later deletion of the
    target also deletes the memberships this is derived from.
    """
    from app.models.workspace import Workspace, WorkspaceMember

    member_workspaces = select(WorkspaceMember.workspace_id).where(
        WorkspaceMember.user_id == target.id
    )
    owners = await session.execute(
        select(WorkspaceMember.user_id).where(
            WorkspaceMember.workspace_id.in_(member_workspaces),
            WorkspaceMember.role == "owner",
        )
    )
    managers = await session.execute(
        select(Workspace.managed_by_user_id).where(Workspace.id.in_(member_workspaces))
    )
    # The same rule read the other way: someone who manages a workspace holds
    # owner rights over it without being on the roster, so its owners could have
    # been read through that account and get to see the record too.
    managed_workspaces = select(Workspace.id).where(
        Workspace.managed_by_user_id == target.id
    )
    managed_owners = await session.execute(
        select(WorkspaceMember.user_id).where(
            WorkspaceMember.workspace_id.in_(managed_workspaces),
            WorkspaceMember.role == "owner",
        )
    )
    audience = {target.id, *owners.scalars().all(), *managed_owners.scalars().all()}
    # The column is nullable, and a workspace without a manager says so with a
    # null instead of staying out of the query.
    audience.update(manager_id for manager_id in managers.scalars().all() if manager_id)
    return audience


async def list_access_log(
    session: AsyncSession,
    target_user_id: Optional[uuid.UUID] = None,
    page: int = 1,
    limit: int = 50,
) -> tuple[list[AdminAccessLog], int]:
    """Every administrative action, newest first, optionally for one account."""
    query = select(AdminAccessLog)
    count_query = select(func.count()).select_from(AdminAccessLog)
    if target_user_id is not None:
        query = query.where(AdminAccessLog.target_user_id == target_user_id)
        count_query = count_query.where(AdminAccessLog.target_user_id == target_user_id)

    total = (await session.execute(count_query)).scalar() or 0
    query = (
        query.order_by(AdminAccessLog.created_at.desc())
        .offset((page - 1) * limit)
        .limit(limit)
    )
    return list((await session.execute(query)).scalars().all()), total


async def list_access_log_for_viewer(
    session: AsyncSession,
    viewer_id: uuid.UUID,
    page: int = 1,
    limit: int = 50,
) -> tuple[list[AdminAccessLog], int]:
    """Administrative actions the viewer is entitled to see (ADR 0042).

    Entries about the viewer's own account, and entries about a member of a
    workspace the viewer owned when they were written: whoever could be
    impersonated through that account gets to know it happened. The audience
    was recorded with the entry, so it survives the member's deletion.
    """
    visible = AdminAccessLog.id.in_(
        select(AdminAccessLogViewer.entry_id).where(
            AdminAccessLogViewer.viewer_id == viewer_id
        )
    )

    total = (
        await session.execute(
            select(func.count()).select_from(AdminAccessLog).where(visible)
        )
    ).scalar() or 0
    query = (
        select(AdminAccessLog)
        .where(visible)
        .order_by(AdminAccessLog.created_at.desc())
        .offset((page - 1) * limit)
        .limit(limit)
    )
    return list((await session.execute(query)).scalars().all()), total


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
