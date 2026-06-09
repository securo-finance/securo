import uuid
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.asset_group import AssetGroup
from app.models.collection import Collection
from app.schemas.collection import CollectionCreate, CollectionRead, CollectionUpdate


def _to_read(collection: Collection) -> CollectionRead:
    # Members are eager-loaded (selectin) so this is N+1-free.
    account_ids = [a.id for a in collection.accounts]
    wallet_ids = [g.id for g in collection.asset_groups]
    return CollectionRead(
        id=collection.id,
        user_id=collection.user_id,
        name=collection.name,
        icon=collection.icon,
        color=collection.color,
        position=collection.position,
        account_ids=account_ids,
        account_count=len(account_ids),
        wallet_ids=wallet_ids,
        wallet_count=len(wallet_ids),
    )


async def _accounts_in_workspace(
    session: AsyncSession, workspace_id: uuid.UUID, account_ids: list[uuid.UUID]
) -> list[Account]:
    """Resolve account ids to Account rows, scoped to the workspace so a
    collection can never reference accounts from another workspace."""
    if not account_ids:
        return []
    result = await session.execute(
        select(Account).where(
            Account.workspace_id == workspace_id,
            Account.id.in_(account_ids),
        )
    )
    return list(result.scalars().all())


async def _wallets_in_workspace(
    session: AsyncSession, workspace_id: uuid.UUID, wallet_ids: list[uuid.UUID]
) -> list[AssetGroup]:
    """Resolve wallet (asset_group) ids to rows, scoped to the workspace."""
    if not wallet_ids:
        return []
    result = await session.execute(
        select(AssetGroup).where(
            AssetGroup.workspace_id == workspace_id,
            AssetGroup.id.in_(wallet_ids),
        )
    )
    return list(result.scalars().all())


async def get_collections(
    session: AsyncSession, workspace_id: uuid.UUID
) -> list[CollectionRead]:
    result = await session.execute(
        select(Collection)
        .where(Collection.workspace_id == workspace_id)
        .order_by(Collection.position, Collection.name)
    )
    return [_to_read(c) for c in result.scalars().all()]


async def _get(
    session: AsyncSession, collection_id: uuid.UUID, workspace_id: uuid.UUID
) -> Optional[Collection]:
    result = await session.execute(
        select(Collection).where(
            Collection.id == collection_id,
            Collection.workspace_id == workspace_id,
        )
    )
    return result.scalar_one_or_none()


async def _next_position(session: AsyncSession, workspace_id: uuid.UUID) -> int:
    row = await session.execute(
        select(func.coalesce(func.max(Collection.position), -1) + 1).where(
            Collection.workspace_id == workspace_id
        )
    )
    return int(row.scalar() or 0)


async def create_collection(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    data: CollectionCreate,
) -> CollectionRead:
    position = data.position or await _next_position(session, workspace_id)
    collection = Collection(
        user_id=user_id,
        workspace_id=workspace_id,
        name=data.name,
        icon=data.icon,
        color=data.color,
        position=position,
    )
    collection.accounts = await _accounts_in_workspace(session, workspace_id, data.account_ids)
    collection.asset_groups = await _wallets_in_workspace(session, workspace_id, data.wallet_ids)
    session.add(collection)
    await session.commit()
    await session.refresh(collection)
    return _to_read(collection)


async def update_collection(
    session: AsyncSession,
    collection_id: uuid.UUID,
    workspace_id: uuid.UUID,
    data: CollectionUpdate,
) -> Optional[CollectionRead]:
    collection = await _get(session, collection_id, workspace_id)
    if not collection:
        return None

    fields = data.model_dump(exclude_unset=True)
    account_ids = fields.pop("account_ids", None)
    wallet_ids = fields.pop("wallet_ids", None)
    for key, value in fields.items():
        setattr(collection, key, value)
    if account_ids is not None:
        collection.accounts = await _accounts_in_workspace(session, workspace_id, account_ids)
    if wallet_ids is not None:
        collection.asset_groups = await _wallets_in_workspace(session, workspace_id, wallet_ids)

    await session.commit()
    await session.refresh(collection)
    return _to_read(collection)


async def delete_collection(
    session: AsyncSession, collection_id: uuid.UUID, workspace_id: uuid.UUID
) -> bool:
    collection = await _get(session, collection_id, workspace_id)
    if not collection:
        return False
    await session.delete(collection)
    await session.commit()
    return True
