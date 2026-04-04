import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import current_active_user
from app.core.database import get_async_session
from app.models.user import User
from app.providers import all_known_providers
from app.schemas.bank_connection import (
    BankConnectionRead,
    OAuthUrlRequest,
    OAuthUrlResponse,
    OAuthCallbackRequest,
    ConnectTokenRequest,
    ConnectTokenResponse,
    ReconnectTokenResponse,
    ConnectionSettingsUpdate,
)
from app.services import connection_service
from app.services.month_service import ensure_current_month_defined
from app.services.transfer_detection_service import detect_transfer_pairs, unlink_transfer_pair

router = APIRouter(prefix="/api/connections", tags=["connections"])


@router.get("/providers")
async def get_available_providers():
    """List all known open finance providers with configuration status."""
    return {"providers": all_known_providers()}


@router.post("/connect-token", response_model=ConnectTokenResponse)
async def create_connect_token(
    data: ConnectTokenRequest,
    user: User = Depends(current_active_user),
):
    """Create a connect token for widget-based bank connection flows."""
    try:
        token_data = await connection_service.create_connect_token(data.provider, user.id)
        return ConnectTokenResponse(**token_data)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create connect token: {str(e)}",
        )


@router.get("", response_model=list[BankConnectionRead])
async def list_connections(
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    return await connection_service.get_connections(session, user.id)


@router.post("/oauth/url", response_model=OAuthUrlResponse)
async def get_oauth_url(
    data: OAuthUrlRequest,
    user: User = Depends(current_active_user),
):
    try:
        url = connection_service.get_oauth_url(data.provider, user.id)
        return OAuthUrlResponse(url=url)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/oauth/callback", response_model=BankConnectionRead)
async def oauth_callback(
    data: OAuthCallbackRequest,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    try:
        connection = await connection_service.handle_oauth_callback(
            session, user.id, data.code, data.provider
        )
        return connection
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to connect: {str(e)}",
        )


@router.post("/{connection_id}/sync")
async def sync_connection(
    connection_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    try:
        ensure_current_month_defined(user)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    try:
        connection, merged_count = await connection_service.sync_connection(session, connection_id, user.id)
        result = BankConnectionRead.model_validate(connection)
        return {**result.model_dump(mode="json"), "merged_count": merged_count}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Sync failed: {str(e)}",
        )


@router.post("/{connection_id}/reconnect-token", response_model=ReconnectTokenResponse)
async def get_reconnect_token(
    connection_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    """Get a connect token for reconnecting an errored/expired connection."""
    connection = await connection_service.get_connection(session, connection_id, user.id)
    if not connection:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connection not found")

    item_id = connection.credentials.get("item_id") if connection.credentials else None
    if not item_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Connection has no item_id for reconnection",
        )

    try:
        token_data = await connection_service.create_connect_token(
            connection.provider, user.id, item_id=item_id
        )
        return ReconnectTokenResponse(**token_data)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create reconnect token: {str(e)}",
        )


@router.patch("/{connection_id}/settings", response_model=BankConnectionRead)
async def update_settings(
    connection_id: uuid.UUID,
    data: ConnectionSettingsUpdate,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    connection = await connection_service.update_connection_settings(
        session, connection_id, user.id, data.model_dump(exclude_unset=True)
    )
    if not connection:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connection not found")
    return connection


@router.delete("/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connection(
    connection_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    deleted = await connection_service.delete_connection(session, connection_id, user.id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connection not found")


@router.post("/transfers/detect")
async def detect_transfers(
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    """One-time backfill scan: detect transfer pairs across all existing transactions."""
    pairs_created = await detect_transfer_pairs(session, user.id)
    await session.commit()
    return {"pairs_created": pairs_created}


@router.delete("/transfers/{pair_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unlink_transfer(
    pair_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    """Manual unlink: remove a transfer pair link so both transactions are treated normally."""
    unlinked = await unlink_transfer_pair(session, user.id, pair_id)
    if not unlinked:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transfer pair not found")
    await session.commit()
