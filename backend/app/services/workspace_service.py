"""Workspace lifecycle + membership helpers.

The Workspace entity sits between User and every financial entity. A
user always belongs to at least one workspace — their auto-created
Personal workspace from registration. Additional workspaces (Freelancer,
Small Business, etc.) can be created later via templates.
"""
import uuid
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.models.workspace import (
    MANAGER_VIRTUAL_ROLE,
    Workspace,
    WorkspaceMember,
    WORKSPACE_ROLES,
)


def _resolve_personal_name(lang: Optional[str]) -> str:
    """Localized default name for the auto-created Personal workspace."""
    if lang and lang.lower().startswith("pt"):
        return "Pessoal"
    return "Personal"


async def create_personal_workspace_for_user(
    session: AsyncSession,
    user: User,
    *,
    commit: bool = False,
) -> Workspace:
    """Create the user's auto-default Personal workspace + owner membership.

    Idempotent: if the user already has a workspace they created and
    belong to (e.g. the migration ran), returns the oldest one.

    The guard matches on identity, not on `kind`: the bootstrap
    workspace is the first one the user created and belongs to. Keying
    off `kind == "personal"` would also match a second personal
    workspace they made by hand, and which of the two came back would
    depend on row order.

    Caller is responsible for committing unless `commit=True`. Defaults
    to flush-only because callers often want to bundle this with the
    user-create transaction.
    """
    existing = await session.execute(
        select(Workspace)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(
            WorkspaceMember.user_id == user.id,
            Workspace.created_by_user_id == user.id,
        )
        .order_by(Workspace.created_at.asc())
        .limit(1)
    )
    found = existing.scalar_one_or_none()
    if found:
        return found

    prefs = user.preferences or {}
    lang = prefs.get("language")
    workspace = Workspace(
        name=_resolve_personal_name(lang),
        kind="personal",
        created_by_user_id=user.id,
        default_currency=prefs.get("currency_display", "USD"),
        locale=lang,
    )
    session.add(workspace)
    await session.flush()

    membership = WorkspaceMember(
        workspace_id=workspace.id,
        user_id=user.id,
        role="owner",
    )
    session.add(membership)
    await session.flush()
    if commit:
        await session.commit()
    return workspace


async def get_user_workspaces(session: AsyncSession, user_id: uuid.UUID) -> list[Workspace]:
    """Return every workspace the user is a member of, oldest first.

    Does NOT include workspaces the user only manages externally — those
    are listed separately by `get_managed_workspaces`. The listing
    endpoint unions the two so the frontend sees a single sorted set.
    """
    result = await session.execute(
        select(Workspace)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(
            WorkspaceMember.user_id == user_id,
            Workspace.is_archived.is_(False),
        )
        .order_by(Workspace.created_at.asc())
    )
    return list(result.scalars().all())


async def get_managed_workspaces(session: AsyncSession, user_id: uuid.UUID) -> list[Workspace]:
    """Return every workspace the user externally administers."""
    result = await session.execute(
        select(Workspace)
        .where(
            Workspace.managed_by_user_id == user_id,
            Workspace.is_archived.is_(False),
        )
        .order_by(Workspace.created_at.asc())
    )
    return list(result.scalars().all())


async def is_workspace_manager(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> bool:
    """True if `user_id` administers this workspace from outside membership."""
    result = await session.execute(
        select(Workspace.id).where(
            Workspace.id == workspace_id,
            Workspace.managed_by_user_id == user_id,
        )
    )
    return result.scalar_one_or_none() is not None


async def get_default_workspace(session: AsyncSession, user_id: uuid.UUID) -> Optional[Workspace]:
    """The user's first (oldest) workspace — used when no X-Workspace-Id header is set.

    Falls back to a managed workspace if the user has no memberships
    (e.g. a manager who hasn't been invited to any of the workspaces
    they administer).
    """
    workspaces = await get_user_workspaces(session, user_id)
    if workspaces:
        return workspaces[0]
    managed = await get_managed_workspaces(session, user_id)
    return managed[0] if managed else None


async def get_membership(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> Optional[WorkspaceMember]:
    """Return the user's membership row for a workspace, or None."""
    result = await session.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


def _virtual_manager_member(workspace_id: uuid.UUID, user_id: uuid.UUID) -> WorkspaceMember:
    """Construct an in-memory WorkspaceMember row representing manager
    access. NOT persisted — used only to flow through code paths that
    expect a `WorkspaceMember` value object."""
    virtual = WorkspaceMember(
        workspace_id=workspace_id,
        user_id=user_id,
        role=MANAGER_VIRTUAL_ROLE,
    )
    # Bypass the normal session lifecycle: this row never hits the DB.
    return virtual


async def require_membership(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    min_role: Optional[str] = None,
) -> WorkspaceMember:
    """Get the user's effective membership or raise 403/404.

    Accepts manager-of as a valid access path: if the user externally
    administers the workspace, returns a virtual `manager` membership
    with effective-owner rights.

    `min_role` enforces a role floor: 'viewer' (any access), 'editor'
    (no read-only), 'owner' (owner or manager).
    """
    member = await get_membership(session, workspace_id, user_id)
    if member is None:
        if await is_workspace_manager(session, workspace_id, user_id):
            member = _virtual_manager_member(workspace_id, user_id)
        else:
            raise HTTPException(status_code=404, detail="Workspace not found")
    if min_role is None:
        return member
    # `manager` is treated as effective owner for permission gates.
    role_rank = {"viewer": 1, "editor": 2, "owner": 3, "manager": 3}
    if role_rank.get(member.role, 0) < role_rank.get(min_role, 0):
        raise HTTPException(status_code=403, detail="Insufficient role")
    return member


async def create_workspace(
    session: AsyncSession,
    *,
    name: str,
    creator: User,
    kind: str = "personal",
    default_currency: Optional[str] = None,
    locale: Optional[str] = None,
    tax_jurisdiction: Optional[str] = None,
    icon: Optional[str] = None,
    color: Optional[str] = None,
    self_membership: bool = False,
    seed_defaults: bool = True,
) -> Workspace:
    """Create a new workspace administered by `creator`.

    By default the creator becomes the external manager (no
    `workspace_members` row). Pass `self_membership=True` to ALSO add
    them as an owner — useful when the creator is the human who'll
    actually use the workspace day-to-day.

    `seed_defaults=True` (the default) seeds the same starter
    categories + rules the Personal workspace gets. Without this every
    new workspace would force the user to rebuild their taxonomy from
    scratch.
    """
    prefs = creator.preferences or {}
    workspace_locale = locale or prefs.get("language") or "en"
    workspace = Workspace(
        name=name.strip() or "Workspace",
        kind=kind,
        created_by_user_id=creator.id,
        managed_by_user_id=creator.id,
        default_currency=default_currency or prefs.get("currency_display", "USD"),
        locale=workspace_locale,
        tax_jurisdiction=tax_jurisdiction,
        icon=icon,
        color=color,
    )
    session.add(workspace)
    await session.flush()
    if self_membership:
        session.add(
            WorkspaceMember(
                workspace_id=workspace.id,
                user_id=creator.id,
                role="owner",
            )
        )
        await session.flush()
    if seed_defaults:
        # Local imports to dodge circular dependencies — category_service
        # transitively pulls workspace_service through the autostamp
        # listener registration.
        from app.services.category_service import create_default_categories
        from app.services.rule_service import create_default_rules

        await create_default_categories(
            session, creator.id, workspace_locale, workspace_id=workspace.id
        )
        await create_default_rules(
            session, creator.id, workspace_locale, workspace_id=workspace.id
        )
    return workspace


async def list_members(session: AsyncSession, workspace_id: uuid.UUID) -> list[tuple[WorkspaceMember, User]]:
    """Return (membership, user) tuples for everyone in the workspace.

    Implemented as two queries instead of a JOIN — the User table comes
    from fastapi-users with a portable `GUID` UUID type, while our own
    tables use Postgres-native `UUID`. Postgres handles the comparison
    transparently but SQLite stores them differently and the JOIN finds
    no matches under test.
    """
    member_rows = await session.execute(
        select(WorkspaceMember)
        .where(WorkspaceMember.workspace_id == workspace_id)
        .order_by(WorkspaceMember.joined_at.asc())
    )
    members = list(member_rows.scalars().all())
    if not members:
        return []
    user_ids = [m.user_id for m in members]
    user_rows = await session.execute(select(User).where(User.id.in_(user_ids)))
    users_by_id = {u.id: u for u in user_rows.scalars().all()}
    return [(m, users_by_id[m.user_id]) for m in members if m.user_id in users_by_id]


async def add_member(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    role: str = "editor",
    invited_by_user_id: Optional[uuid.UUID] = None,
) -> WorkspaceMember:
    """Insert a new membership. Caller validates the inviter's permission."""
    if role not in WORKSPACE_ROLES:
        raise HTTPException(status_code=400, detail=f"Invalid role: {role}")
    existing = await get_membership(session, workspace_id, user_id)
    if existing:
        raise HTTPException(status_code=409, detail="User is already a member")
    member = WorkspaceMember(
        workspace_id=workspace_id,
        user_id=user_id,
        role=role,
        invited_by_user_id=invited_by_user_id,
    )
    session.add(member)
    await session.flush()
    return member


async def update_member_role(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    new_role: str,
) -> WorkspaceMember:
    if new_role not in WORKSPACE_ROLES:
        raise HTTPException(status_code=400, detail=f"Invalid role: {new_role}")
    member = await get_membership(session, workspace_id, user_id)
    if member is None:
        raise HTTPException(status_code=404, detail="Member not found")
    # Block demoting the sole owner — leaves the workspace ownerless.
    if member.role == "owner" and new_role != "owner":
        owners = await session.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.role == "owner",
            )
        )
        owner_rows = owners.scalars().all()
        if len(owner_rows) <= 1:
            raise HTTPException(status_code=400, detail="Cannot demote the sole owner")
    member.role = new_role
    await session.flush()
    return member


async def get_workspace_stats(
    session: AsyncSession, workspace_id: uuid.UUID
) -> dict:
    """Counts surfaced on the settings page header strip."""
    from sqlalchemy import func

    from app.models.account import Account
    from app.models.transaction import Transaction

    members_q = await session.execute(
        select(func.count(WorkspaceMember.id)).where(
            WorkspaceMember.workspace_id == workspace_id
        )
    )
    accounts_q = await session.execute(
        select(func.count(Account.id)).where(Account.workspace_id == workspace_id)
    )
    transactions_q = await session.execute(
        select(func.count(Transaction.id)).where(
            Transaction.workspace_id == workspace_id
        )
    )
    return {
        "members": int(members_q.scalar() or 0),
        "accounts": int(accounts_q.scalar() or 0),
        "transactions": int(transactions_q.scalar() or 0),
    }


async def archive_workspace(
    session: AsyncSession, workspace_id: uuid.UUID, requester_id: uuid.UUID
) -> Workspace:
    """Soft-delete: flip is_archived. Refuses to archive the requester's
    LAST accessible workspace (they'd be locked out)."""
    workspace = await session.get(Workspace, workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    if workspace.is_archived:
        return workspace
    # Count this user's other workspaces (member OR manager).
    other_member = await session.execute(
        select(func.count(WorkspaceMember.id))
        .join(Workspace, Workspace.id == WorkspaceMember.workspace_id)
        .where(
            WorkspaceMember.user_id == requester_id,
            WorkspaceMember.workspace_id != workspace_id,
            Workspace.is_archived.is_(False),
        )
    )
    other_managed = await session.execute(
        select(func.count(Workspace.id)).where(
            Workspace.managed_by_user_id == requester_id,
            Workspace.id != workspace_id,
            Workspace.is_archived.is_(False),
        )
    )
    if int(other_member.scalar() or 0) + int(other_managed.scalar() or 0) == 0:
        raise HTTPException(
            status_code=400,
            detail="Cannot archive your last workspace",
        )
    workspace.is_archived = True
    await session.flush()
    return workspace


async def remove_member(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    member = await get_membership(session, workspace_id, user_id)
    if member is None:
        raise HTTPException(status_code=404, detail="Member not found")
    if member.role == "owner":
        owners = await session.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.role == "owner",
            )
        )
        if len(owners.scalars().all()) <= 1:
            raise HTTPException(status_code=400, detail="Cannot remove the sole owner")
    await session.delete(member)
    await session.flush()
