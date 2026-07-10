import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.core.workspace_context import (
    WorkspaceContext,
    current_workspace,
    current_writable_workspace,
)
from app.schemas.group import (
    GroupBalances,
    GroupCreate,
    GroupMemberCreate,
    GroupMemberRead,
    GroupMemberUpdate,
    GroupRead,
    GroupUpdate,
)
from app.schemas.group_settlement import (
    GroupSettlementCreate,
    GroupSettlementRead,
    GroupSettlementUpdate,
)
from app.schemas.transaction import TransactionRead
from app.services import balance_service, group_service, settlement_service

router = APIRouter(prefix="/api/groups", tags=["groups"])


@router.get("", response_model=list[GroupRead])
async def list_groups(
    include_archived: bool = Query(False),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await group_service.list_groups(
        session, ctx.workspace.id, ctx.user_id, include_archived=include_archived
    )


@router.post("", response_model=GroupRead, status_code=status.HTTP_201_CREATED)
async def create_group(
    data: GroupCreate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        return await group_service.create_group(session, ctx.workspace.id, ctx.user_id, data)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/{group_id}", response_model=GroupRead)
async def get_group(
    group_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    # Read endpoint — visible to workspace members and to cross-workspace
    # linked members (the Splitwise case where someone is added from
    # outside this workspace).
    group = await group_service.get_group_visible(session, group_id, ctx.workspace.id, ctx.user_id)
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    return group


@router.patch("/{group_id}", response_model=GroupRead)
async def update_group(
    group_id: uuid.UUID,
    data: GroupUpdate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        group = await group_service.update_group(
            session, group_id, ctx.workspace.id, ctx.user_id, data
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    return group


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(
    group_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        deleted = await group_service.delete_group(session, group_id, ctx.workspace.id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")


@router.get("/{group_id}/members", response_model=list[GroupMemberRead])
async def list_members(
    group_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    members = await group_service.list_members(session, group_id, ctx.workspace.id, ctx.user_id)
    if members is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    return members


@router.post(
    "/{group_id}/members",
    response_model=GroupMemberRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_member(
    group_id: uuid.UUID,
    data: GroupMemberCreate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        member = await group_service.create_member(session, group_id, ctx.workspace.id, data)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    if member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    return member


@router.patch("/{group_id}/members/{member_id}", response_model=GroupMemberRead)
async def update_member(
    group_id: uuid.UUID,
    member_id: uuid.UUID,
    data: GroupMemberUpdate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        member = await group_service.update_member(
            session, group_id, member_id, ctx.workspace.id, data
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    if member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")
    return member


@router.delete("/{group_id}/members/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_member(
    group_id: uuid.UUID,
    member_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        deleted = await group_service.delete_member(session, group_id, member_id, ctx.workspace.id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")


@router.get("/{group_id}/transactions", response_model=list[TransactionRead])
async def list_group_transactions(
    group_id: uuid.UUID,
    limit: int = 20,
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    txs = await group_service.list_transactions(
        session, group_id, ctx.workspace.id, ctx.user_id, limit=limit
    )
    if txs is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    return txs


@router.get("/{group_id}/balances", response_model=GroupBalances)
async def get_balances(
    group_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    balances = await balance_service.compute_balances(
        session, group_id, ctx.workspace.id, ctx.user_id
    )
    if balances is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    return balances


@router.get("/{group_id}/settlements", response_model=list[GroupSettlementRead])
async def list_settlements(
    group_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    settlements = await settlement_service.list_settlements(
        session, group_id, ctx.workspace.id, ctx.user_id
    )
    if settlements is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    return settlements


@router.post(
    "/{group_id}/settlements",
    response_model=GroupSettlementRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_settlement(
    group_id: uuid.UUID,
    data: GroupSettlementCreate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        settlement = await settlement_service.create_settlement(
            session, group_id, ctx.workspace.id, ctx.user_id, data
        )
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    if settlement is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    return settlement


@router.patch("/{group_id}/settlements/{settlement_id}", response_model=GroupSettlementRead)
async def update_settlement(
    group_id: uuid.UUID,
    settlement_id: uuid.UUID,
    data: GroupSettlementUpdate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        settlement = await settlement_service.update_settlement(
            session, group_id, settlement_id, ctx.workspace.id, ctx.user_id, data
        )
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    if settlement is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Settlement not found")
    return settlement


@router.delete(
    "/{group_id}/settlements/{settlement_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_settlement(
    group_id: uuid.UUID,
    settlement_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        deleted = await settlement_service.delete_settlement(
            session, group_id, settlement_id, ctx.workspace.id, ctx.user_id
        )
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Settlement not found")
