"""Write-only instance credentials and uncached runtime resolution."""

import uuid
from dataclasses import dataclass

from pydantic import SecretStr
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.services.crypto import decrypt, encrypt
from app.core import row_security
from app.core.config import Settings, get_settings
from app.core.database import async_session_maker
from app.models.app_settings import AppSetting
from app.models.bank_connection import BankConnection
from app.models.user import User
from app.models.workspace import Workspace
from app.models.workspace_provider_credential import WorkspaceProviderCredential
from app.providers.credentials import (
    PROVIDER_FIELDS,
    SECRET_FIELDS,
    SETTING_FIELDS,
    field_present,
    is_configured,
)
from app.providers.enable_banking import enable_banking_redirect_uri


class SecretStorageUnavailable(ValueError):
    """SECRET_KEY is too weak to encrypt saved credentials."""


@dataclass(frozen=True)
class WorkspaceProvider:
    """A provider whose credentials a workspace can replace with its own.

    `account_field` names the account the provider's connections are created
    under (a Pluggy client, say). A workspace with connections of this
    provider can't move to another account: the provider only knows those
    connections under the account that created them.
    """

    fields: tuple[str, ...]
    account_field: str


#: Providers that take credentials per workspace. To add one, list it here with
#: its fields from PROVIDER_FIELDS, cache anything keyed by credentials per
#: account rather than per process, and give it texts under
#: `providerSettings.workspaces.<provider>` in the frontend locales. The admin
#: screen reads the fields from `workspace_fields` in the provider status.
WORKSPACE_PROVIDERS: dict[str, WorkspaceProvider] = {
    "pluggy": WorkspaceProvider(fields=PROVIDER_FIELDS["pluggy"], account_field="pluggy_client_id"),
}


_WORKSPACE_FIELDS = {field for spec in WORKSPACE_PROVIDERS.values() for field in spec.fields}


class ProviderAccountChangeError(ValueError):
    """The change would strand connections created under another provider account."""


def can_store_secrets() -> bool:
    # Both published development defaults are shorter than 32 characters.
    return len(get_settings().secret_key.get_secret_value().strip()) >= 32


async def _overrides(session: AsyncSession) -> dict[str, str]:
    rows = await session.execute(
        select(AppSetting.key, AppSetting.value).where(AppSetting.key.in_(SETTING_FIELDS))
    )
    return {key: value for key, value in rows.all()}


def _resolve(overrides: dict[str, str]) -> Settings:
    settings = get_settings().model_copy(deep=True)
    for key, value in overrides.items():
        if key == "simplefin_enabled":
            settings.simplefin_enabled = value == "true"
        else:
            # A broken override must not silently switch to another account.
            value = decrypt(value) or ""
            setattr(
                settings,
                key,
                SecretStr(value) if isinstance(getattr(settings, key), SecretStr) else value,
            )
    if "enable_banking_private_key" in overrides:
        settings.enable_banking_private_key_file = ""
    return settings


async def _workspace_overrides(session: AsyncSession, workspace_id: uuid.UUID) -> dict[str, str]:
    rows = await session.execute(
        select(WorkspaceProviderCredential.key, WorkspaceProviderCredential.value).where(
            WorkspaceProviderCredential.workspace_id == workspace_id,
            WorkspaceProviderCredential.key.in_(_WORKSPACE_FIELDS),
        )
    )
    return {key: value for key, value in rows.all()}


class WorkspaceNotBound(RuntimeError):
    """Row security is on and the session works for another workspace, or none."""


async def resolve_settings(
    session: AsyncSession | None = None, workspace_id: uuid.UUID | None = None
) -> Settings:
    if session is None:
        async with async_session_maker() as owned_session:
            if workspace_id is not None:
                await row_security.bind_workspace(owned_session, workspace_id)
            return await resolve_settings(owned_session, workspace_id)
    overrides = await _overrides(session)
    if workspace_id is not None:
        # Under row security a session bound elsewhere reads no workspace rows,
        # which would quietly fall back to the instance account.
        if row_security.enabled() and row_security.bound_to(session) != workspace_id:
            raise WorkspaceNotBound("Session is not bound to the workspace being resolved")
        overrides |= await _workspace_overrides(session, workspace_id)
    return _resolve(overrides)


async def _rows_by_workspace(
    session: AsyncSession, workspace_ids: list[uuid.UUID], query
) -> list:
    """Rows of a workspace table for each listed workspace.

    Off, one query reads them all. Under row security a session sees one
    workspace at a time, so it is bound to each in turn.
    """
    if not row_security.enabled():
        return list((await session.execute(query)).all())
    rows: list = []
    for workspace_id in workspace_ids:
        await row_security.bind_workspace(session, workspace_id)
        rows += (await session.execute(query)).all()
    return rows


async def _workspace_usage(
    session: AsyncSession, provider: str, workspace_ids: list[uuid.UUID]
) -> tuple[dict[uuid.UUID, int], dict[uuid.UUID, list[str]]]:
    """Each workspace's connections of `provider`, and its saved values for it."""
    spec = WORKSPACE_PROVIDERS[provider]
    counts = await _rows_by_workspace(
        session,
        workspace_ids,
        select(BankConnection.workspace_id, func.count())
        .where(BankConnection.provider == provider)
        .group_by(BankConnection.workspace_id),
    )
    saved = await _rows_by_workspace(
        session,
        workspace_ids,
        select(WorkspaceProviderCredential.workspace_id, WorkspaceProviderCredential.value).where(
            WorkspaceProviderCredential.key.in_(spec.fields)
        ),
    )
    values: dict[uuid.UUID, list[str]] = {}
    for workspace_id, value in saved:
        values.setdefault(workspace_id, []).append(value)
    return {workspace_id: total for workspace_id, total in counts}, values


async def provider_status(session: AsyncSession) -> list[dict]:
    overrides = await _overrides(session)
    settings = _resolve(overrides)
    environment = get_settings()
    secret_storage_available = can_store_secrets()
    result = []
    for provider, fields in PROVIDER_FIELDS.items():
        statuses = {}
        for key in fields:
            present = field_present(settings, key)
            statuses[key] = {
                "configured": present,
                "source": "app" if key in overrides else "environment" if present else "none",
                "invalid": key in SECRET_FIELDS and key in overrides and not present,
                "environment_configured": field_present(environment, key),
            }
        result.append(
            {
                "name": provider,
                "configured": is_configured(provider, settings),
                "can_store_secrets": secret_storage_available,
                "fields": statuses,
                # The fields a workspace can replace with its own, if any.
                "workspace_fields": (
                    list(WORKSPACE_PROVIDERS[provider].fields)
                    if provider in WORKSPACE_PROVIDERS
                    else None
                ),
                # Read-only: derived from the server URL, shown so it can be
                # registered with the provider.
                "redirect_uri": (
                    enable_banking_redirect_uri() if provider == "enable_banking" else None
                ),
            }
        )
    return result


async def update_provider(session: AsyncSession, provider: str, values: dict) -> dict:
    if (
        provider not in PROVIDER_FIELDS
        or not values
        or set(values) - set(PROVIDER_FIELDS[provider])
    ):
        raise ValueError("Unknown provider or credential field")
    # Validate the whole patch before writing anything. Never echo submitted input.
    for key, value in values.items():
        if value is None:
            continue
        if key == "simplefin_enabled":
            if type(value) is not bool:
                raise ValueError("SimpleFIN enabled must be a boolean")
        elif not isinstance(value, str) or not value.strip() or len(value) > 16000:
            raise ValueError("Credentials must be nonempty strings of at most 16000 characters")
    if any(key in SECRET_FIELDS and value is not None for key, value in values.items()):
        if not can_store_secrets():
            raise SecretStorageUnavailable(
                "Set a unique SECRET_KEY of at least 32 characters before saving credentials"
            )
    await _ensure_fallback_keeps_account(session, provider, values)
    for key, value in values.items():
        row = await session.get(AppSetting, key)
        if value is None:
            if row is not None:
                await session.delete(row)
            continue
        stored = str(value).lower() if key == "simplefin_enabled" else encrypt(value.strip())
        if stored is None:
            raise ValueError("Credential cannot be empty")
        if row is None:
            session.add(AppSetting(key=key, value=stored))
        else:
            row.value = stored
    await session.commit()
    return next(item for item in await provider_status(session) if item["name"] == provider)


def _workspace_provider(provider: str) -> WorkspaceProvider:
    try:
        return WORKSPACE_PROVIDERS[provider]
    except KeyError:
        raise LookupError("Provider does not take workspace credentials") from None


async def workspace_provider_status(session: AsyncSession, provider: str) -> list[dict]:
    """Which credentials each workspace uses for `provider`. Never returns a value or ciphertext."""
    _workspace_provider(provider)
    workspaces = (
        await session.execute(
            select(Workspace.id, Workspace.name, User.email)
            .outerjoin(User, User.id == Workspace.created_by_user_id)
            .where(Workspace.is_archived.is_(False))
            .order_by(Workspace.name)
        )
    ).all()
    counts, saved = await _workspace_usage(session, provider, [row[0] for row in workspaces])
    return [
        {
            "workspace_id": workspace_id,
            "name": name,
            "owner_email": email,
            "connections": counts.get(workspace_id, 0),
            "has_credentials": workspace_id in saved,
            "invalid": not all(decrypt(value) for value in saved.get(workspace_id, [])),
        }
        for workspace_id, name, email in workspaces
    ]


async def _active_workspace(session: AsyncSession, workspace_id: uuid.UUID) -> Workspace:
    workspace = await session.get(Workspace, workspace_id)
    if workspace is None or workspace.is_archived:
        raise LookupError("Workspace not found")
    return workspace


async def _ensure_keeps_account(
    session: AsyncSession,
    provider: str,
    workspace_id: uuid.UUID,
    next_account: str,
) -> None:
    # A connection only exists under the account that created it. An unreadable
    # current value stays replaceable, or the workspace could never recover.
    field = WORKSPACE_PROVIDERS[provider].account_field
    current = getattr(await resolve_settings(session, workspace_id), field)
    if not current or current == next_account:
        return
    connected = await session.scalar(
        select(func.count())
        .select_from(BankConnection)
        .where(BankConnection.workspace_id == workspace_id, BankConnection.provider == provider)
    )
    if connected:
        raise ProviderAccountChangeError("Workspace has connections under another account")


async def set_workspace_credentials(
    session: AsyncSession, provider: str, workspace_id: uuid.UUID, values: dict
) -> None:
    spec = _workspace_provider(provider)
    await _active_workspace(session, workspace_id)
    await row_security.bind_workspace(session, workspace_id)
    if set(values) != set(spec.fields):
        raise ValueError("Every credential field of the provider is required")
    for value in values.values():
        if not isinstance(value, str) or not value.strip() or len(value) > 16000:
            raise ValueError("Credentials must be nonempty strings of at most 16000 characters")
    if not can_store_secrets():
        raise SecretStorageUnavailable(
            "Set a unique SECRET_KEY of at least 32 characters before saving credentials"
        )
    await _ensure_keeps_account(
        session, provider, workspace_id, values[spec.account_field].strip()
    )
    for key, value in values.items():
        row = await session.get(WorkspaceProviderCredential, (workspace_id, key))
        stored = encrypt(value.strip())
        if stored is None:
            raise ValueError("Credential cannot be empty")
        if row is None:
            session.add(WorkspaceProviderCredential(workspace_id=workspace_id, key=key, value=stored))
        else:
            row.value = stored
    await session.commit()


async def remove_workspace_credentials(
    session: AsyncSession, provider: str, workspace_id: uuid.UUID
) -> None:
    spec = _workspace_provider(provider)
    await _active_workspace(session, workspace_id)
    await row_security.bind_workspace(session, workspace_id)
    # Without its own values the workspace falls back to the instance account.
    instance_account = getattr(await resolve_settings(session), spec.account_field)
    await _ensure_keeps_account(session, provider, workspace_id, instance_account)
    await session.execute(
        delete(WorkspaceProviderCredential).where(
            WorkspaceProviderCredential.workspace_id == workspace_id,
            WorkspaceProviderCredential.key.in_(spec.fields),
        )
    )
    await session.commit()


async def _ensure_fallback_keeps_account(session: AsyncSession, provider: str, values: dict) -> None:
    """Refuse an instance account change that would strand connections.

    Workspaces without their own credentials use the instance ones, so moving
    the instance to another account moves them too.
    """
    spec = WORKSPACE_PROVIDERS.get(provider)
    if spec is None or spec.account_field not in values:
        return
    current = getattr(_resolve(await _overrides(session)), spec.account_field)
    value = values[spec.account_field]
    next_account = (
        value.strip() if isinstance(value, str) else getattr(get_settings(), spec.account_field)
    )
    # An unreadable current value stays replaceable, as for a workspace.
    if not current or current == next_account:
        return
    workspace_ids = list((await session.scalars(select(Workspace.id))).all())
    counts, saved = await _workspace_usage(session, provider, workspace_ids)
    if any(total and workspace_id not in saved for workspace_id, total in counts.items()):
        raise ProviderAccountChangeError(
            "Workspaces on the instance account have connections under it"
        )
