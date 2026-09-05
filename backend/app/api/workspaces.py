"""Workspace + member management endpoints.

Every user gets a Personal workspace at registration. `POST` here
creates the additional ones and is the only place `kind` is ever set;
`PATCH` edits the rest of the workspace and deliberately cannot touch
it.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi_users import schemas as fu_schemas
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import current_active_user, get_user_manager, UserManager
from app.core.auth_policy import local_auth_enabled, require_local_auth_enabled
from app.core.database import get_async_session
from app.core.workspace_context import WorkspaceContext, current_workspace
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember
from app.schemas.workspace import (
    MemberInvite,
    MemberRead,
    MemberRoleUpdate,
    WorkspaceCreate,
    WorkspaceRead,
    WorkspaceUpdate,
)
from app.services import module_service, workspace_service

router = APIRouter(prefix="/api/workspaces", tags=["workspaces"])


def _user_display_name(user: User) -> str | None:
    prefs = user.preferences or {}
    return prefs.get("display_name") or None


def _workspace_read(workspace: Workspace, role: str | None) -> WorkspaceRead:
    """Build the read model for a workspace.

    Every response that returns a workspace goes through here, so a new
    read path cannot ship without `enabled_modules` — forgetting it
    would leave the frontend hiding modules the server enabled.
    """
    item = WorkspaceRead.model_validate(workspace)
    item.role = role
    item.enabled_modules = module_service.resolve_modules(workspace)
    return item


@router.get("", response_model=list[WorkspaceRead])
async def list_my_workspaces(
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    """Return every workspace the current user can access.

    Unions two sets:
      - workspaces where the user has a `workspace_members` row (role
        comes from that row)
      - workspaces where `workspaces.managed_by_user_id` matches the
        user but they have no membership (role = 'manager')

    A user who is both a member AND the external manager is reported
    with their concrete membership role (not the virtual manager one)
    since their explicit role is the more specific signal.
    """
    member_rows = await session.execute(
        select(Workspace, WorkspaceMember.role)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(
            WorkspaceMember.user_id == user.id,
            Workspace.is_archived.is_(False),
        )
        .order_by(Workspace.created_at.asc())
    )
    out: list[WorkspaceRead] = []
    seen_ids: set[uuid.UUID] = set()
    for ws, role in member_rows.all():
        out.append(_workspace_read(ws, role))
        seen_ids.add(ws.id)

    managed_rows = await session.execute(
        select(Workspace)
        .where(
            Workspace.managed_by_user_id == user.id,
            Workspace.is_archived.is_(False),
        )
        .order_by(Workspace.created_at.asc())
    )
    for ws in managed_rows.scalars().all():
        if ws.id in seen_ids:
            continue
        out.append(_workspace_read(ws, "manager"))
    return out


@router.post("", response_model=WorkspaceRead, status_code=status.HTTP_201_CREATED)
async def create_workspace_endpoint(
    body: WorkspaceCreate,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    """Create a new workspace; the caller becomes its manager.

    Use case: a user provisioning a workspace they'll operate (either
    for themselves as a second container, or on behalf of someone else
    who'll be invited later as the day-to-day owner).
    """
    workspace = await workspace_service.create_workspace(
        session,
        name=body.name,
        creator=user,
        kind=body.kind,
        default_currency=body.default_currency,
        locale=body.locale,
        tax_jurisdiction=body.tax_jurisdiction,
        icon=body.icon,
        color=body.color,
        self_membership=body.self_membership,
    )
    await session.commit()
    return _workspace_read(workspace, "owner" if body.self_membership else "manager")


@router.get("/current", response_model=WorkspaceRead)
async def get_current_workspace(ctx: WorkspaceContext = Depends(current_workspace)):
    """Return the workspace resolved from X-Workspace-Id (or the default)."""
    return _workspace_read(ctx.workspace, ctx.role)


@router.patch("/{workspace_id}", response_model=WorkspaceRead)
async def update_workspace(
    workspace_id: uuid.UUID,
    body: WorkspaceUpdate,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    member = await workspace_service.require_membership(
        session, workspace_id, user.id, min_role="owner"
    )
    workspace = await session.get(Workspace, workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    updates = body.model_dump(exclude_unset=True)
    for key, value in updates.items():
        setattr(workspace, key, value)
    # Changing the workspace currency follows through to the acting
    # owner's display currency, so the whole app re-renders in the new
    # currency (display is driven by user.currency_display, not the
    # workspace). Other members keep their own currency_display override.
    new_currency = updates.get("default_currency")
    if new_currency and (user.preferences or {}).get("currency_display") != new_currency:
        prefs = dict(user.preferences or {})
        prefs["currency_display"] = new_currency
        user.preferences = prefs
        session.add(user)
    await session.commit()
    await session.refresh(workspace)
    return _workspace_read(workspace, member.role)


@router.get("/{workspace_id}/members", response_model=list[MemberRead])
async def list_workspace_members(
    workspace_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    # Any member can list members of their own workspace.
    await workspace_service.require_membership(session, workspace_id, user.id)
    rows = await workspace_service.list_members(session, workspace_id)
    return [
        MemberRead(
            id=m.id,
            user_id=u.id,
            email=u.email,
            display_name=_user_display_name(u),
            role=m.role,
            joined_at=m.joined_at,
        )
        for m, u in rows
    ]


@router.post(
    "/{workspace_id}/members",
    response_model=MemberRead,
    status_code=status.HTTP_201_CREATED,
)
async def invite_member(
    workspace_id: uuid.UUID,
    body: MemberInvite,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
    user_manager: UserManager = Depends(get_user_manager),
):
    await workspace_service.require_membership(
        session, workspace_id, user.id, min_role="owner"
    )

    # Find existing user by email (case-insensitive — fastapi-users
    # stores email lowercased on register, but be safe).
    existing = await session.execute(
        select(User).where(User.email == body.email.lower())
    )
    target = existing.scalar_one_or_none()

    if target is None:
        # Brand-new user — creating the account here mints a local password,
        # so it is only allowed while local credentials are accepted.
        if body.password:
            require_local_auth_enabled()
        elif not local_auth_enabled():
            # There is no password to ask for in OIDC-only mode, so point at
            # what actually unblocks the invite.
            raise HTTPException(
                status_code=400,
                detail=(
                    "User not found. They must sign in through the identity "
                    "provider once before they can be added."
                ),
            )
        else:
            raise HTTPException(
                status_code=400,
                detail="User not found. Provide a password to create them.",
            )
        try:
            create_payload = fu_schemas.BaseUserCreate(
                email=body.email,
                password=body.password,
            )
            target = await user_manager.create(create_payload)
            # A user created to join this workspace inherits its currency
            # as their display currency, so the app comes up in the
            # workspace's currency rather than the USD default. Set this
            # before bootstrapping their Personal workspace below, which
            # reads currency_display for its own default_currency.
            ws = await session.get(Workspace, workspace_id)
            if ws is not None:
                target.preferences = {
                    **(target.preferences or {}),
                    "currency_display": ws.default_currency,
                }
                session.add(target)
                await session.flush()
            # The fresh user gets their own Personal workspace by virtue
            # of the registration hook (called below via on_after_register
            # when request is non-None; programmatic call leaves it empty,
            # so we bootstrap explicitly).
            await workspace_service.create_personal_workspace_for_user(session, target)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Could not create user: {e}")

    member = await workspace_service.add_member(
        session,
        workspace_id=workspace_id,
        user_id=target.id,
        role=body.role,
        invited_by_user_id=user.id,
    )
    await session.commit()
    return MemberRead(
        id=member.id,
        user_id=target.id,
        email=target.email,
        display_name=_user_display_name(target),
        role=member.role,
        joined_at=member.joined_at,
    )


@router.patch(
    "/{workspace_id}/members/{member_user_id}",
    response_model=MemberRead,
)
async def change_member_role(
    workspace_id: uuid.UUID,
    member_user_id: uuid.UUID,
    body: MemberRoleUpdate,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    await workspace_service.require_membership(
        session, workspace_id, user.id, min_role="owner"
    )
    member = await workspace_service.update_member_role(
        session, workspace_id, member_user_id, body.role
    )
    await session.commit()

    target = await session.get(User, member_user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Could not find user")

    return MemberRead(
        id=member.id,
        user_id=target.id,
        email=target.email,
        display_name=_user_display_name(target),
        role=member.role,
        joined_at=member.joined_at,
    )


@router.get("/{workspace_id}/stats")
async def workspace_stats(
    workspace_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    """KPIs surfaced on the settings page (members / accounts / transactions)."""
    await workspace_service.require_membership(session, workspace_id, user.id)
    return await workspace_service.get_workspace_stats(session, workspace_id)


@router.post("/{workspace_id}/archive", response_model=WorkspaceRead)
async def archive_workspace_endpoint(
    workspace_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    """Soft-delete: flips is_archived. Requires owner role. Refuses to
    archive the requester's last accessible workspace."""
    await workspace_service.require_membership(
        session, workspace_id, user.id, min_role="owner"
    )
    workspace = await workspace_service.archive_workspace(session, workspace_id, user.id)
    await session.commit()
    return _workspace_read(workspace, "owner")


@router.delete(
    "/{workspace_id}/members/{member_user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_workspace_member(
    workspace_id: uuid.UUID,
    member_user_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    # Owner can remove anyone; a non-owner can remove themselves only.
    requester = await workspace_service.require_membership(session, workspace_id, user.id)
    if requester.role != "owner" and member_user_id != user.id:
        raise HTTPException(status_code=403, detail="Only the owner can remove other members")
    await workspace_service.remove_member(session, workspace_id, member_user_id)
    await session.commit()
    return None
