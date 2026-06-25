import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.core.workspace_context import (
    WorkspaceContext,
    current_workspace,
    current_writable_workspace,
)
from app.providers import all_known_providers
from app.providers.base import ProviderUserActionRequired, SessionExpiredError
from app.schemas.bank_connection import (
    BankConnectionRead,
    ConnectionSettingsUpdate,
    ConnectTokenRequest,
    ConnectTokenResponse,
    InstitutionListResponse,
    OAuthCallbackRequest,
    OAuthUrlRequest,
    OAuthUrlResponse,
    ReauthUrlResponse,
    ReconnectTokenResponse,
)
from app.services import connection_service
from app.services.transfer_detection_service import detect_transfer_pairs, unlink_transfer_pair

router = APIRouter(prefix="/api/connections", tags=["connections"])


@router.get("/providers")
async def get_available_providers():
    """List all known open finance providers with configuration status."""
    return {"providers": all_known_providers()}


@router.post("/connect-token", response_model=ConnectTokenResponse)
async def create_connect_token(
    data: ConnectTokenRequest,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
):
    """Create a connect token for widget-based bank connection flows."""
    try:
        token_data = await connection_service.create_connect_token(data.provider, ctx.user_id)
        return ConnectTokenResponse(**token_data)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except SessionExpiredError as e:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create connect token: {str(e)}",
        )


@router.get("", response_model=list[BankConnectionRead])
async def list_connections(
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await connection_service.get_connections(session, ctx.workspace.id)


@router.post("/oauth/url", response_model=OAuthUrlResponse)
async def get_oauth_url(
    data: OAuthUrlRequest,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
):
    try:
        url = await connection_service.get_oauth_url(
            data.provider, ctx.user_id, ctx.workspace.id, flow_params=data.flow_params
        )
        return OAuthUrlResponse(url=url)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/{provider}/institutions", response_model=InstitutionListResponse)
async def list_provider_institutions(
    provider: str,
    country: str | None = None,
    ctx: WorkspaceContext = Depends(current_workspace),
):
    try:
        return await connection_service.list_provider_institutions(provider, country)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to load institutions: {str(e)}",
        )


@router.post("/oauth/callback", response_model=BankConnectionRead)
async def oauth_callback(
    data: OAuthCallbackRequest,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        connection = await connection_service.handle_oauth_callback(
            session,
            ctx.workspace.id,
            ctx.user_id,
            data.code,
            provider_name=data.provider,
            state=data.state,
        )
        return connection
    except ProviderUserActionRequired as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": str(e),
                "code": e.code,
                "help_url": e.help_url,
            },
        )
    except SessionExpiredError as e:
        raise HTTPException(
            status_code=status.HTTP_410_GONE, detail=str(e)
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to connect: {str(e)}",
        )


@router.post(
    "/{connection_id}/oauth/reauth-url", response_model=ReauthUrlResponse
)
async def get_reauth_url(
    connection_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        url = await connection_service.get_reauth_url(
            session, connection_id, ctx.workspace.id, ctx.user_id
        )
        return ReauthUrlResponse(url=url)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except NotImplementedError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to build reauth URL: {str(e)}",
        )


@router.post("/{connection_id}/sync")
async def sync_connection(
    connection_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        # Manual sync is user-initiated, so ask the provider to pull fresh
        # data from the bank before we read. Scheduled syncs (Celery) keep
        # the default behaviour: read whatever the provider already has.
        connection, merged_count = await connection_service.sync_connection(
            session,
            connection_id,
            ctx.workspace.id,
            ctx.user_id,
            trigger_provider_refresh=True,
        )
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
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Get a connect token for reconnecting an errored/expired connection."""
    connection = await connection_service.get_connection(session, connection_id, ctx.workspace.id)
    if not connection:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connection not found")

    creds = connection.credentials or {}
    item_id = creds.get("item_id") or creds.get("link_token")
    if not item_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Connection has no reconnect identifier",
        )

    try:
        token_data = await connection_service.create_connect_token(
            connection.provider, ctx.user_id, item_id=item_id
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
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    connection = await connection_service.update_connection_settings(
        session, connection_id, ctx.workspace.id, data.model_dump(exclude_unset=True)
    )
    if not connection:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connection not found")
    return connection


@router.delete("/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connection(
    connection_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    deleted = await connection_service.delete_connection(session, connection_id, ctx.workspace.id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connection not found")


@router.get("/{connection_id}/debug/accounts")
async def debug_fintoc_accounts(
    connection_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Return raw account list from the provider for debugging purposes."""
    import httpx
    from app.core.config import get_settings

    connection = await connection_service.get_connection(session, connection_id, ctx.workspace.id)
    if not connection:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connection not found")

    creds = connection.credentials or {}
    link_token = creds.get("link_token") or creds.get("link_token_enc")
    if not link_token:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No link_token found in credentials")

    async with httpx.AsyncClient(
        headers={"Authorization": get_settings().fintoc_secret_key}, timeout=30
    ) as client:
        response = await client.get(
            "https://api.fintoc.com/v1/accounts",
            params={"link_token": link_token},
        )

    return {"status_code": response.status_code, "accounts": response.json()}


@router.post("/transfers/detect")
async def detect_transfers(
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """One-time backfill scan: detect transfer pairs across all existing transactions in this workspace."""
    pairs_created = await detect_transfer_pairs(session, ctx.workspace.id)
    await session.commit()
    return {"pairs_created": pairs_created}


@router.delete("/transfers/{pair_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unlink_transfer(
    pair_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Manual unlink: remove a transfer pair link so both transactions are treated normally."""
    unlinked = await unlink_transfer_pair(session, ctx.workspace.id, pair_id)
    if not unlinked:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transfer pair not found")
    await session.commit()
