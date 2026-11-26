"""Changes to a provider's account run one at a time (ADR 0049).

Each path takes the per-provider lock before it reads, so a change made in
another transaction is either already committed or still to come, never in
between.
"""

import asyncio
import secrets
import uuid

import pytest
from pydantic import SecretStr

from app.agents.services.crypto import _fernet
from app.core.config import get_settings
from app.models.bank_connection import BankConnection
from app.models.user import User
from app.models.workspace import Workspace
from app.services import provider_settings

pytestmark = pytest.mark.asyncio

OWN = {"pluggy_client_id": "own-client", "pluggy_client_secret": "own-secret"}


@pytest.fixture(autouse=True)
def instance_client(monkeypatch):
    monkeypatch.setattr(get_settings(), "secret_key", SecretStr(secrets.token_hex(32)))
    monkeypatch.setattr(get_settings(), "pluggy_client_id", "instance-client")
    monkeypatch.setattr(get_settings(), "pluggy_client_secret", SecretStr("instance-secret"))
    _fernet.cache_clear()
    yield
    _fernet.cache_clear()


async def _workspace(sessions) -> uuid.UUID:
    async with sessions() as session:
        user = User(email=f"{uuid.uuid4().hex}@example.com", hashed_password="x")
        session.add(user)
        await session.flush()
        workspace = Workspace(name="Mom", created_by_user_id=user.id)
        session.add(workspace)
        await session.commit()
        return workspace.id


async def _waits_for_the_lock(sessions, change) -> None:
    """`change` blocks while another transaction holds the lock, and runs once it ends."""
    async with sessions() as holder:
        await provider_settings.lock_provider_accounts(holder, "pluggy")
        task = asyncio.create_task(change())
        await asyncio.sleep(0.5)
        assert not task.done(), "ran without waiting for the lock"
        await holder.commit()
    await asyncio.wait_for(task, timeout=10)


@pytest.mark.parametrize("path", ["set", "remove", "instance", "connect"])
async def test_each_account_change_waits_for_the_provider_lock(postgres_sessions, path):
    workspace_id = await _workspace(postgres_sessions)
    if path == "remove":
        async with postgres_sessions() as session:
            await provider_settings.set_workspace_credentials(session, "pluggy", workspace_id, OWN)

    async def change():
        async with postgres_sessions() as session:
            if path == "set":
                await provider_settings.set_workspace_credentials(session, "pluggy", workspace_id, OWN)
            elif path == "remove":
                await provider_settings.remove_workspace_credentials(session, "pluggy", workspace_id)
            elif path == "instance":
                await provider_settings.update_provider(
                    session, "pluggy", {"pluggy_client_id": "other-client"}
                )
            else:
                used = await provider_settings.resolve_settings(session, workspace_id)
                await provider_settings.ensure_account_unchanged(session, "pluggy", workspace_id, used)
                await session.commit()

    await _waits_for_the_lock(postgres_sessions, change)


def _pause_after_check(monkeypatch):
    """Hold each account check open for a moment, so a concurrent change lands inside it."""
    real = provider_settings._ensure_keeps_account

    async def slow(*args, **kwargs):
        await real(*args, **kwargs)
        await asyncio.sleep(0.5)

    monkeypatch.setattr(provider_settings, "_ensure_keeps_account", slow)


async def test_two_first_saves_for_a_workspace_do_not_collide(postgres_sessions, monkeypatch):
    """Without the lock both would find no row, both insert, and one would fail."""
    workspace_id = await _workspace(postgres_sessions)
    _pause_after_check(monkeypatch)

    async def save(secret):
        async with postgres_sessions() as session:
            values = {"pluggy_client_id": "own-client", "pluggy_client_secret": secret}
            await provider_settings.set_workspace_credentials(session, "pluggy", workspace_id, values)

    await asyncio.gather(save("first"), save("second"))
    async with postgres_sessions() as session:
        await provider_settings.resolve_settings(session, workspace_id)


async def test_a_removal_and_an_instance_change_cannot_both_pass(postgres_sessions, monkeypatch):
    """The interleaving from review: W uses the instance client through its own
    values and has a connection. Removing W's values and moving the instance to
    another client are each allowed alone, but not together."""
    workspace_id = await _workspace(postgres_sessions)
    async with postgres_sessions() as session:
        same = {"pluggy_client_id": "instance-client", "pluggy_client_secret": "instance-secret"}
        await provider_settings.set_workspace_credentials(session, "pluggy", workspace_id, same)
        user_id = (await session.get(Workspace, workspace_id)).created_by_user_id
        session.add(BankConnection(
            user_id=user_id, workspace_id=workspace_id, provider="pluggy",
            external_id="item-1", institution_name="Banco", credentials={"item_id": "item-1"},
        ))
        await session.commit()

    async def remove():
        async with postgres_sessions() as session:
            await provider_settings.remove_workspace_credentials(session, "pluggy", workspace_id)

    async def move_instance():
        async with postgres_sessions() as session:
            await provider_settings.update_provider(
                session, "pluggy", {"pluggy_client_id": "other-client"}
            )

    # The removal checks first and pauses; the instance change comes in meanwhile.
    _pause_after_check(monkeypatch)

    async def move_instance_during_removal():
        await asyncio.sleep(0.2)
        await move_instance()

    results = await asyncio.gather(remove(), move_instance_during_removal(), return_exceptions=True)
    refused = [r for r in results if isinstance(r, provider_settings.ProviderAccountChangeError)]
    assert len(refused) == 1, results
    async with postgres_sessions() as session:
        # Whichever ran first, W still resolves to the client its item lives under.
        resolved = await provider_settings.resolve_settings(session, workspace_id)
        assert resolved.pluggy_client_id == "instance-client"
