"""Logos a plugin ships in its package: declared in the manifest, kept on install, served by Securo."""
from __future__ import annotations

import copy
import io
import json
import tarfile
import uuid
from decimal import Decimal

import pytest
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.bank_connection import BankConnection
from app.models.plugin_logo import PluginLogo
from app.plugins import connections, logos
from app.plugins.manifest import parse_manifest
from app.services import account_service, connection_service
from tests import test_code_plugins as code_plugins
from tests import test_provider_plugins as provider_plugins
from tests.test_code_plugins import PLUGIN, V1, V2, _apply

catalog = code_plugins.catalog
plugin_runner = code_plugins.plugin_runner
sync_locks = code_plugins.sync_locks
ids = code_plugins.ids

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 24


def _package(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(f"acme-securo-ledger-abc/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _manifest(**logos_: str) -> dict:
    manifest = provider_plugins._provider_manifest()
    manifest["logos"] = logos_ or {"broker": "logos/broker.png"}
    return manifest


# --- The manifest ---------------------------------------------------------------


def test_a_plugin_with_code_declares_its_logos():
    assert parse_manifest(_manifest()).logos == {"broker": "logos/broker.png"}


@pytest.mark.parametrize(
    "path",
    ["logos/broker.svg", "https://cdn.example/broker.png", "../broker.png", "logos/../../broker.png", "/broker.png"],
    ids=["svg", "url", "parent", "escape", "absolute"],
)
def test_a_logo_is_a_png_or_webp_inside_the_package(path):
    with pytest.raises(ValidationError):
        parse_manifest(_manifest(broker=path))


def test_only_a_plugin_with_code_ships_logos():
    from tests.test_data_plugins import MANIFEST as DATA_MANIFEST

    manifest = dict(copy.deepcopy(DATA_MANIFEST), logos={"broker": "logos/broker.png"})
    with pytest.raises(ValidationError, match="only for plugins with a runtime"):
        parse_manifest(manifest)


# --- Taking them out of the package -------------------------------------------------


def test_logos_are_read_from_the_package_by_their_signature():
    found = logos.extract(
        _package({"logos/a.png": PNG, "logos/b.webp": WEBP}), {"a": "logos/a.png", "b": "logos/b.webp", "c": "logos/a.png"}
    )
    assert {k: kind for k, (kind, _) in found.items()} == {"a": "image/png", "b": "image/webp", "c": "image/png"}


@pytest.mark.parametrize(
    ("files", "message"),
    [
        ({}, "missing from the package"),
        ({"logos/a.png": b"<svg onload=alert(1)>"}, "not a PNG or WebP"),
        ({"logos/a.png": PNG + b"\x00" * logos.MAX_LOGO_BYTES}, "larger than"),
    ],
    ids=["missing", "not-an-image", "too-large"],
)
def test_a_logo_that_isnt_a_small_image_is_refused(files, message):
    with pytest.raises(logos.LogoError, match=message):
        logos.extract(_package(files), {"a": "logos/a.png"})


# --- Installing, syncing and serving -----------------------------------------------


@pytest.fixture
def logo_catalog(catalog):
    catalog.manifests[V1] = _manifest()
    catalog.archives[V1] = _package({"ledger.py": provider_plugins.PROVIDER_SOURCE.encode(), "logos/broker.png": PNG})
    return catalog


def _source_with_logo() -> str:
    data = provider_plugins.payload_data()
    data["accounts"][0]["institution_logo"] = "broker"
    return provider_plugins.PROVIDER_SOURCE.replace(
        json.dumps(json.dumps(provider_plugins.payload_data())), json.dumps(json.dumps(data)), 1
    )


@pytest.mark.asyncio
async def test_installing_keeps_the_logos_and_accounts_show_them(
    session: AsyncSession, ids, logo_catalog, plugin_runner, sync_locks
):
    workspace_id, user_id = ids
    logo_catalog.archives[V1] = _package({"ledger.py": _source_with_logo().encode(), "logos/broker.png": PNG})
    await _apply(session, workspace_id, user_id)
    (logo,) = (await session.scalars(select(PluginLogo))).all()
    assert (logo.plugin_id, logo.commit, logo.key, logo.content_type, logo.data) == (PLUGIN, V1, "broker", "image/png", PNG)

    connection, _ = await code_plugins._connect(session, workspace_id, user_id)
    await connections.sync(session, connection)
    assert connection.status == "active", connection.last_error
    bank = await session.get(BankConnection, connection.bank_connection_id)
    assert bank is not None
    await session.refresh(bank)
    assert bank.logo_url == logos.url(logo.sha256)


@pytest.mark.asyncio
async def test_the_logo_is_served_by_its_checksum(client: AsyncClient, session: AsyncSession, catalog):
    import hashlib

    digest = hashlib.sha256(PNG).hexdigest()
    session.add(PluginLogo(plugin_id=PLUGIN, commit="3" * 40, key="broker", content_type="image/png", sha256=digest, data=PNG))
    await session.commit()
    resp = await client.get(logos.url(digest))
    assert resp.status_code == 200 and resp.content == PNG
    assert resp.headers["content-type"] == "image/png"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in resp.headers["content-security-policy"]
    assert (await client.get(logos.url("0" * 64))).status_code == 404
    assert (await client.get(f"{logos.URL_PREFIX}not-a-checksum")).status_code == 404


@pytest.mark.asyncio
async def test_a_package_without_its_logo_cant_be_installed(session: AsyncSession, ids, catalog, plugin_runner):
    from app.plugins.service import PluginUnavailable

    catalog.manifests[V1] = _manifest()  # declares logos/broker.png, the package has no such file
    with pytest.raises(PluginUnavailable, match="missing from the package"):
        await _apply(session, *ids)
    assert (await session.scalars(select(PluginLogo))).all() == []


@pytest.mark.asyncio
async def test_logos_go_with_the_last_workspace_that_runs_the_commit(
    session: AsyncSession, ids, logo_catalog, plugin_runner
):
    await _apply(session, *ids)
    logo_catalog.manifests[V2] = dict(copy.deepcopy(logo_catalog.manifests[V1]), version="1.1.0")
    logo_catalog.archives[V2] = logo_catalog.archives[V1]
    logo_catalog.commit = V2
    await _apply(session, *ids, "update")
    rows = (await session.scalars(select(PluginLogo))).all()
    assert [r.commit for r in rows] == [V2]
    await _apply(session, *ids, "remove")
    assert (await session.scalars(select(PluginLogo))).all() == []


# --- Unlinking a card -------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_unlinked_card_keeps_showing_its_debt(session: AsyncSession, test_user, test_workspace):
    bank = BankConnection(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id, provider="plugin:acme/ledger",
        external_id="x", institution_name="Ledger", credentials={}, settings={}, status="active",
    )
    session.add(bank)
    await session.commit()
    data = {
        "contract": 1,
        "accounts": [{
            "external_id": "card", "name": "Card", "type": "credit_card", "balance": "200.00", "currency": "USD",
            "transactions": [
                {"external_id": "c1", "description": "Groceries", "amount": "50.00", "date": "2026-11-10", "type": "debit"},
                {"external_id": "c2", "description": "Taxi", "amount": "8.00", "date": "2026-11-12", "type": "debit",
                 "status": "pending"},
            ],
        }],
    }
    await connection_service.sync_connection(
        session, bank.id, test_workspace.id, test_user.id, provider=provider_plugins.adapter(data)
    )
    account = await session.scalar(select(Account).where(Account.external_id == "card"))
    assert account is not None
    before = await account_service.get_account_summary(session, account.id, test_workspace.id)
    assert before is not None and Decimal(str(before["current_balance"])) == Decimal("-200.00")

    account_id, workspace_id = account.id, test_workspace.id
    await connection_service.unlink_connection(session, bank.id, workspace_id)
    await session.commit()
    session.expire_all()
    account = await session.get(Account, account_id)
    assert account is not None and account.connection_id is None
    after = await account_service.get_account_summary(session, account_id, workspace_id)
    assert after is not None and Decimal(str(after["current_balance"])) == Decimal("-200.00")
