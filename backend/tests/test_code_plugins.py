"""Plugins with code: manifest, consent, install on the runner, connections, sign-in and sync.

The runner here is the real one (its app, in process, with a temporary data
folder), so the plugin's code really runs in a subprocess; only the catalog
and the OAuth state store are fakes.
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import tarfile
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pytest
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.access_token import AccessToken
from app.models.plugin_connection import PluginConnection
from app.models.workspace_plugin import WorkspacePlugin
from app.plugins import connections, index, runner, service, vault
from app.plugins.manifest import parse_manifest
from app.services import oauth_state
from plugin_runner.config import RunnerSettings
from plugin_runner.main import create_app

PLUGIN = "acme/ledger"
REPO = "https://github.com/acme/securo-ledger"
V1, V2 = "1" * 40, "2" * 40
INDEX_URL = "https://plugins.usesecuro.com/v1/index.json"

SOURCE = '''
class Plugin:
    def oauth_url(self, params, context):
        return {"url": "https://auth.example.com/authorize?client_id=" + context.config["client"]
                + "&state=" + params["state"] + "&redirect_uri=" + params["redirect_uri"]}

    def oauth_exchange(self, params, context):
        return {"secrets": {"access_token": "token-for-" + params["code"]}}

    def sync(self, params, context):
        if context.config.get("account") == "broken":
            raise RuntimeError("the service is down")
        if context.config.get("account") == "leaky":
            raise RuntimeError("GET https://api.ledger.example/v1?key=" + context.secrets["api_key"] + " failed")
        if context.config.get("account") == "expired":
            raise type("NeedsAuth", (Exception,), {})("sign in again")
        seen = params["state"].get("runs", 0)
        return {
            "state": {
                "runs": seen + 1,
                "key": context.secrets.get("api_key") or context.secrets.get("access_token"),
                "token": bool(context.securo.get("token")),
                "token_in_secrets": any(k.startswith("__securo") for k in context.secrets),
                "workspace": context.securo.get("workspace_id"),
                "integration": context.securo.get("integration"),
            },
            "secrets": {"refreshed": "yes"},
        }

plugin = Plugin()
'''

MANIFEST: dict[str, Any] = {
    "id": PLUGIN,
    "name": "Ledger",
    "description": "Brings in what happens on Ledger.",
    "version": "1.0.0",
    "repository": REPO,
    "license": "AGPL-3.0-only",
    "min_securo_version": "0.1.0",
    "runtime": {"entrypoint": "ledger:plugin"},
    "network": [{"host": "api.ledger.example", "purpose": "Reads your payouts"}],
    "api_scopes": [{"scope": "transactions:write", "purpose": "Adds your payouts as transactions"}],
    "contributes": {
        "integrations": [
            {
                "key": "keys",
                "name": "Ledger (API key)",
                "auth": "credentials",
                "fields": [
                    {"key": "api_key", "label": "API key", "secret": True},
                    {"key": "account", "label": "Account", "required": False},
                ],
                "sync_minutes": 60,
            },
            {
                "key": "signin",
                "name": "Ledger (sign in)",
                "auth": "oauth",
                "fields": [{"key": "client", "label": "Client ID"}],
                "sync_minutes": 30,
            },
        ]
    },
}


def _tarball(source: str) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        data = source.encode()
        info = tarfile.TarInfo("acme-securo-ledger-abc/ledger.py")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


class Catalog:
    def __init__(self) -> None:
        self.manifests = {V1: copy.deepcopy(MANIFEST)}
        self.archives = {V1: _tarball(SOURCE)}
        self.commit = V1
        self.archive_tampered = False

    def _manifest(self, commit: str) -> bytes:
        return json.dumps(self.manifests[commit]).encode()

    def index(self) -> dict:
        m = self.manifests[self.commit]
        return {
            "plugins": [
                {
                    "id": PLUGIN, "repository": REPO, "release": f"v{m['version']}", "commit": self.commit,
                    "manifest": f"manifests/{self.commit}.json",
                    "manifest_sha256": hashlib.sha256(self._manifest(self.commit)).hexdigest(),
                    "archive": f"archives/{self.commit}.tar.gz",
                    "archive_sha256": hashlib.sha256(self.archives[self.commit]).hexdigest(),
                    "name": m["name"], "description": m["description"], "version": m["version"],
                    "license": m["license"], "min_securo_version": m["min_securo_version"],
                    "contributes": {k: len(v) for k, v in m["contributes"].items()},
                }
            ]
        }

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == INDEX_URL:
            return httpx.Response(200, json=self.index())
        for commit in self.manifests:
            if url.endswith(f"/manifests/{commit}.json"):
                return httpx.Response(200, content=self._manifest(commit))
            if url.endswith(f"/archives/{commit}.tar.gz"):
                return httpx.Response(200, content=self.archives[commit] + (b"x" if self.archive_tampered else b""))
        return httpx.Response(404)

    def publish(self, commit: str, change) -> None:
        manifest = copy.deepcopy(self.manifests[self.commit])
        change(manifest)
        self.manifests[commit] = manifest
        self.archives[commit] = _tarball(SOURCE + "\n# " + commit)
        self.commit = commit


@pytest.fixture
def catalog(monkeypatch) -> Catalog:
    fake = Catalog()
    monkeypatch.setattr(index, "http_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(fake.handle)))
    index._manifests.clear()
    monkeypatch.setenv("PLUGINS_ENABLED", "true")
    return fake


@pytest.fixture
def plugin_runner(monkeypatch, tmp_path):
    """The real runner app, in process; the backend's client talks to it."""
    app = create_app(RunnerSettings(token="t", egress_secret="e", data_dir=tmp_path / "runner", call_timeout=20))
    transport = httpx.ASGITransport(app=app)

    class Client(runner.RunnerClient):
        def __init__(self, transport_=None):  # noqa: D401 - same shape as the real one
            self._client = httpx.AsyncClient(base_url="http://runner", headers={"Authorization": "Bearer t"}, transport=transport)

    monkeypatch.setattr(runner, "RunnerClient", Client)
    monkeypatch.setattr(runner, "configured", lambda: True)
    return Client


@pytest.fixture
def states(monkeypatch) -> dict:
    store: dict[str, dict] = {}

    async def put(payload):
        key = uuid.uuid4().hex
        store[key] = payload
        return key

    async def take(state):
        return store.pop(state, None)

    monkeypatch.setattr(oauth_state, "store_state", put)
    monkeypatch.setattr(oauth_state, "consume_state", take)
    return store


@pytest.fixture(autouse=True)
def sync_locks(monkeypatch) -> set:
    """The per-connection sync lock, in memory instead of Redis."""
    held: set[str] = set()

    async def acquire(key):
        if key in held:
            return False
        held.add(key)
        return True

    async def release(key):
        held.discard(key)

    monkeypatch.setattr(connections, "_acquire", acquire)
    monkeypatch.setattr(connections, "_release", release)
    return held


@pytest.fixture
def ids(test_user, test_workspace) -> tuple[uuid.UUID, uuid.UUID]:
    return test_workspace.id, test_user.id


async def _apply(session, workspace_id, user_id, action="install", *, is_owner=True):
    plan = await service.plan(session, workspace_id, PLUGIN, action, lang="en")
    return await service.apply(
        session, workspace_id, user_id, PLUGIN, action,
        commit=plan.commit, token=plan.sync.token, lang="en", is_owner=is_owner,
    )


async def _connect(session, workspace_id, user_id, integration="keys", **values):
    return await connections.create(
        session, workspace_id=workspace_id, user_id=user_id, can_write=True, plugin_id=PLUGIN,
        integration_key=integration, name="", values=values or {"api_key": "sk_live_1"}, lang="en",
    )


# --- The manifest -------------------------------------------------------------


def test_the_example_manifest_is_valid():
    manifest = parse_manifest(MANIFEST)
    assert manifest.has_code
    assert [i.key for i in manifest.contributes.integrations] == ["keys", "signin"]


@pytest.mark.parametrize(
    "change",
    [
        lambda m: m.pop("runtime"),  # integrations need code
        lambda m: m["network"].append({"host": "*.ledger.example", "purpose": "x"}),
        lambda m: m["network"].append({"host": "10.0.0.1", "purpose": "x"}),
        lambda m: m["network"].append({"host": "api.ledger.example", "purpose": "twice"}),
        lambda m: m["api_scopes"].append({"scope": "admin:write", "purpose": "x"}),
        lambda m: m["runtime"].update(entrypoint="os.system('rm')"),
        lambda m: m["runtime"].update(lockfile="../requirements.txt"),
        lambda m: m["contributes"]["integrations"][0].update(sync_minutes=1),
        lambda m: m["contributes"]["integrations"][0]["fields"].append({"key": "api_key", "label": "again"}),
        lambda m: m["contributes"].update(integrations=[]),  # a runtime with nothing to run
    ],
    ids=["no-runtime", "wildcard-host", "ip-host", "duplicate-host", "unknown-scope", "bad-entrypoint",
         "lockfile-outside", "sync-too-often", "duplicate-field", "runtime-without-integration"],
)
def test_manifests_that_break_the_code_contract_are_rejected(change):
    manifest = copy.deepcopy(MANIFEST)
    change(manifest)
    with pytest.raises(ValidationError):
        parse_manifest(manifest)


def test_a_data_plugin_cannot_declare_network_or_scopes():
    manifest = {k: v for k, v in MANIFEST.items() if k not in ("runtime", "contributes")}
    manifest["contributes"] = {"rules": [{
        "key": "r", "name": "R", "conditions_op": "or",
        "conditions": [{"field": "description", "op": "contains", "value": "X"}],
        "actions": [{"op": "ignore"}],
    }]}
    with pytest.raises(ValidationError, match="only for plugins with a runtime"):
        parse_manifest(manifest)


# --- Install, consent, update and remove --------------------------------------------


@pytest.mark.asyncio
async def test_code_is_only_installable_with_a_runner(session: AsyncSession, ids, catalog, monkeypatch):
    workspace_id, _ = ids
    monkeypatch.setattr(runner, "configured", lambda: False)
    items = await service.catalog(session, workspace_id)
    assert items[0].entry.supported is False
    with pytest.raises(service.PluginUnavailable):
        await service.plan(session, workspace_id, PLUGIN, "install", lang="en")


@pytest.mark.asyncio
async def test_the_plan_shows_what_the_owner_consents_to(session: AsyncSession, ids, catalog, plugin_runner):
    workspace_id, _ = ids
    plan = await service.plan(session, workspace_id, PLUGIN, "install", lang="en")
    assert plan.code is not None
    assert [h["host"] for h in plan.code.network] == ["api.ledger.example"]
    assert [s["scope"] for s in plan.code.api_scopes] == ["transactions:write"]
    assert [i["auth"] for i in plan.code.integrations] == ["credentials", "oauth"]
    assert plan.code.new_hosts == [] and plan.code.connections == 0


@pytest.mark.asyncio
async def test_only_the_owner_installs_code(session: AsyncSession, ids, catalog, plugin_runner):
    workspace_id, user_id = ids
    with pytest.raises(service.PluginForbidden):
        await _apply(session, workspace_id, user_id, is_owner=False)
    assert await session.scalar(select(WorkspacePlugin.id)) is None


@pytest.mark.asyncio
async def test_install_puts_the_code_on_the_runner_and_keeps_the_manifest(
    session: AsyncSession, ids, catalog, plugin_runner
):
    workspace_id, user_id = ids
    await _apply(session, workspace_id, user_id)
    async with plugin_runner() as client:
        assert [(p.id, p.commit, p.hosts) for p in await client.installed()] == [
            (PLUGIN, V1, ["api.ledger.example:443"])
        ]
    record = await session.scalar(select(WorkspacePlugin))
    assert record is not None and record.manifest is not None
    assert record.manifest["runtime"]["entrypoint"] == "ledger:plugin"


@pytest.mark.asyncio
async def test_code_that_does_not_match_its_checksum_is_refused(session: AsyncSession, ids, catalog, plugin_runner):
    workspace_id, user_id = ids
    catalog.archive_tampered = True
    with pytest.raises(index.PluginIndexError, match="checksum"):
        await _apply(session, workspace_id, user_id)
    assert await session.scalar(select(WorkspacePlugin.id)) is None


@pytest.mark.asyncio
async def test_an_update_lists_new_hosts_and_scopes(session: AsyncSession, ids, catalog, plugin_runner):
    workspace_id, user_id = ids
    await _apply(session, workspace_id, user_id)

    def v2(m):
        m["version"] = "1.1.0"
        m["network"].append({"host": "files.ledger.example", "purpose": "Downloads receipts"})
        m["api_scopes"].append({"scope": "accounts:read", "purpose": "Finds the account"})

    catalog.publish(V2, v2)
    plan = await service.plan(session, workspace_id, PLUGIN, "update", lang="en")
    assert plan.code is not None
    assert plan.code.new_hosts == ["files.ledger.example"]
    assert plan.code.new_scopes == ["accounts:read"]


@pytest.mark.asyncio
async def test_an_update_gives_connections_the_scopes_agreed_to(session: AsyncSession, ids, catalog, plugin_runner):
    workspace_id, user_id = ids
    await _apply(session, workspace_id, user_id)
    connection, _ = await _connect(session, workspace_id, user_id)
    old_token = connection.access_token_id

    catalog.publish(V2, lambda m: m.update(version="1.1.0", api_scopes=m["api_scopes"] + [{"scope": "accounts:read", "purpose": "x"}]))
    await _apply(session, workspace_id, user_id, "update")
    token = await session.get(AccessToken, old_token, populate_existing=True)
    assert token is not None and sorted(token.scopes) == ["accounts:read", "transactions:write"]

    # Dropping every scope revokes the token and the plugin no longer gets one.
    catalog.publish("3" * 40, lambda m: m.update(version="1.2.0", api_scopes=[]))
    await _apply(session, workspace_id, user_id, "update")
    await session.refresh(connection)
    assert connection.access_token_id is None
    assert "__securo_api_token" not in vault.unseal(connection.secrets)
    token = await session.get(AccessToken, old_token, populate_existing=True)
    assert token is not None and token.revoked_at is not None

    # Declaring scopes again mints a fresh token for each connection.
    catalog.publish("4" * 40, lambda m: m.update(version="1.3.0", api_scopes=[{"scope": "accounts:read", "purpose": "x"}]))
    await _apply(session, workspace_id, user_id, "update")
    await session.refresh(connection)
    fresh = await session.get(AccessToken, connection.access_token_id)
    assert fresh is not None and fresh.scopes == ["accounts:read"] and fresh.id != old_token
    assert vault.unseal(connection.secrets)["__securo_api_token"].startswith("sct_")


@pytest.mark.asyncio
async def test_a_workspace_on_an_older_version_is_refused_by_the_runner(
    session: AsyncSession, ids, catalog, plugin_runner
):
    """Another workspace updating must not run new code where nobody agreed to it."""
    workspace_id, user_id = ids
    await _apply(session, workspace_id, user_id)
    connection, _ = await _connect(session, workspace_id, user_id)
    record = await session.scalar(select(WorkspacePlugin))
    assert record is not None
    record.commit = V2  # as if this workspace had agreed to another version
    await session.commit()
    await connections.sync(session, connection)
    assert connection.status == "error" and "not installed" in (connection.last_error or "")


@pytest.mark.asyncio
async def test_remove_drops_connections_revokes_tokens_and_frees_the_runner(
    session: AsyncSession, ids, catalog, plugin_runner
):
    workspace_id, user_id = ids
    await _apply(session, workspace_id, user_id)
    connection, _ = await _connect(session, workspace_id, user_id)
    token_id = connection.access_token_id

    plan = await service.plan(session, workspace_id, PLUGIN, "remove", lang="en")
    assert plan.code is not None and plan.code.connections == 1
    with pytest.raises(service.PluginForbidden):
        await _apply(session, workspace_id, user_id, "remove", is_owner=False)
    await _apply(session, workspace_id, user_id, "remove")

    assert await session.scalar(select(PluginConnection.id)) is None
    token = await session.get(AccessToken, token_id, populate_existing=True)
    assert token is not None and token.revoked_at is not None
    async with plugin_runner() as client:
        assert await client.installed() == []


# --- Connections ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_connection_seals_secrets_and_gets_a_scoped_token(
    session: AsyncSession, ids, catalog, plugin_runner
):
    workspace_id, user_id = ids
    await _apply(session, workspace_id, user_id)
    connection, link = await _connect(session, workspace_id, user_id, api_key="sk_live_1", account="main")
    assert link is None and connection.status == "pending"
    assert connection.config == {"account": "main"}
    assert "sk_live_1" not in (connection.secrets or "")
    assert vault.unseal(connection.secrets)["api_key"] == "sk_live_1"
    token = await session.get(AccessToken, connection.access_token_id)
    assert token is not None and token.scopes == ["transactions:write"] and token.name == "Ledger: Ledger (API key)"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("values", "message"),
    [({}, "api_key is required"), ({"api_key": "k", "extra": "x"}, "unknown fields"), ({"api_key": 5}, "must be text")],
)
async def test_the_form_is_checked_against_the_manifest(session: AsyncSession, ids, catalog, plugin_runner, values, message):
    workspace_id, user_id = ids
    await _apply(session, workspace_id, user_id)
    with pytest.raises(connections.PluginConnectionError, match=message):
        await connections.create(
            session, workspace_id=workspace_id, user_id=user_id, can_write=True, plugin_id=PLUGIN,
            integration_key="keys", name="", values=values, lang="en",
        )


@pytest.mark.asyncio
async def test_sync_runs_the_plugin_and_keeps_its_cursor(session: AsyncSession, ids, catalog, plugin_runner):
    workspace_id, user_id = ids
    await _apply(session, workspace_id, user_id)
    connection, _ = await _connect(session, workspace_id, user_id)
    await connections.sync(session, connection)
    assert connection.status == "active" and connection.last_sync_at is not None
    assert connection.state == {
        "runs": 1, "key": "sk_live_1", "token": True, "token_in_secrets": False, "workspace": str(workspace_id),
        "integration": "keys",
    }
    assert vault.unseal(connection.secrets)["refreshed"] == "yes"
    await connections.sync(session, connection)
    assert connection.state["runs"] == 2


@pytest.mark.asyncio
async def test_failures_are_recorded_and_back_off(session: AsyncSession, ids, catalog, plugin_runner):
    workspace_id, user_id = ids
    await _apply(session, workspace_id, user_id)
    connection, _ = await _connect(session, workspace_id, user_id, api_key="k", account="broken")
    await connections.sync(session, connection)
    assert connection.status == "error" and connection.failures == 1
    assert "the service is down" in (connection.last_error or "")
    now = datetime.now(timezone.utc)
    assert not connections.due(connection, now + timedelta(minutes=61))  # doubled after a failure
    assert connections.due(connection, now + timedelta(minutes=121))

    expired, _ = await _connect(session, workspace_id, user_id, api_key="k", account="expired")
    await connections.sync(session, expired)
    assert expired.status == "needs_auth"
    assert not connections.due(expired, now + timedelta(days=30))


@pytest.mark.asyncio
async def test_oauth_sign_in(session: AsyncSession, ids, catalog, plugin_runner, states):
    workspace_id, user_id = ids
    await _apply(session, workspace_id, user_id)
    connection, link = await _connect(session, workspace_id, user_id, "signin", client="abc")
    assert connection.status == "needs_auth"
    assert link is not None and link.startswith("https://auth.example.com/authorize?client_id=abc&state=")
    assert "/plugins/oauth/callback" in link
    state = link.split("state=")[1].split("&")[0]

    other_user = uuid.uuid4()
    with pytest.raises(connections.PluginConnectionError, match="expired"):
        await connections.finish_oauth(session, workspace_id=workspace_id, user_id=other_user, code="c", state=state)
    # A state is single use: the failed attempt above consumed it.
    link = await connections.authorize_url(session, connection, user_id=user_id)
    state = link.split("state=")[1].split("&")[0]
    await connections.finish_oauth(session, workspace_id=workspace_id, user_id=user_id, code="c0de", state=state)
    assert connection.status == "pending"
    assert vault.unseal(connection.secrets)["access_token"] == "token-for-c0de"
    await connections.sync(session, connection)
    assert connection.state["key"] == "token-for-c0de"


@pytest.mark.asyncio
async def test_a_sign_in_that_cannot_start_leaves_nothing_behind(session: AsyncSession, ids, catalog, plugin_runner, states, monkeypatch):
    workspace_id, user_id = ids
    await _apply(session, workspace_id, user_id)

    async def down(*args, **kwargs):
        raise runner.RunnerUnavailable("the plugin runner can't be reached")

    monkeypatch.setattr(connections, "authorize_url", down)
    with pytest.raises(runner.RunnerUnavailable):
        await _connect(session, workspace_id, user_id, "signin", client="abc")
    assert await session.scalar(select(PluginConnection.id)) is None
    tokens = (await session.scalars(select(AccessToken))).all()
    assert all(t.revoked_at is not None for t in tokens)


@pytest.mark.asyncio
async def test_the_schedule_syncs_only_what_is_due(session: AsyncSession, ids, catalog, plugin_runner, states):
    from app.tasks.plugin_tasks import sync_due

    workspace_id, user_id = ids
    await _apply(session, workspace_id, user_id)
    fresh, _ = await _connect(session, workspace_id, user_id)
    signing_in, _ = await _connect(session, workspace_id, user_id, "signin", client="abc")
    recent, _ = await _connect(session, workspace_id, user_id)
    recent.last_sync_at = datetime.now(timezone.utc)
    await session.commit()
    assert await sync_due(session) == 1
    assert fresh.state["runs"] == 1 and recent.state == {} and signing_in.state == {}


def test_the_schedule_does_nothing_while_plugins_are_off(monkeypatch):
    from app.tasks import plugin_tasks

    monkeypatch.delenv("PLUGINS_ENABLED", raising=False)
    assert plugin_tasks.sync_plugin_connections() == {"synced": 0}


# --- API --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_connections(client: AsyncClient, auth_headers: dict, catalog, plugin_runner, monkeypatch):
    monkeypatch.setattr("app.api.plugins._queue_sync", lambda connection_id: None)
    plan = (await client.get(f"/api/plugins/{PLUGIN}/plan", params={"action": "install"}, headers=auth_headers)).json()
    assert plan["code"]["network"][0]["host"] == "api.ledger.example"
    body = {"action": "install", "commit": plan["commit"], "token": plan["token"]}
    assert (await client.post(f"/api/plugins/{PLUGIN}/apply", json=body, headers=auth_headers)).status_code == 200

    installed = (await client.get("/api/plugins", headers=auth_headers)).json()
    assert [i["key"] for i in installed[0]["integrations"]] == ["keys", "signin"]

    created = await client.post(
        "/api/plugins/connections",
        json={"plugin_id": PLUGIN, "integration": "keys", "name": "Main", "values": {"api_key": "sk_live_9"}},
        headers=auth_headers,
    )
    assert created.status_code == 201, created.text
    connection = created.json()["connection"]
    assert connection["secret_fields"] == ["api_key"]
    assert "sk_live_9" not in created.text

    synced = await client.post(f"/api/plugins/connections/{connection['id']}/sync", headers=auth_headers)
    assert synced.json()["status"] == "active"
    listed = (await client.get("/api/plugins/connections", headers=auth_headers)).json()
    assert [c["name"] for c in listed] == ["Main"]
    assert "sk_live_9" not in json.dumps(listed)

    bad = await client.post(
        "/api/plugins/connections", json={"plugin_id": PLUGIN, "integration": "keys", "values": {}}, headers=auth_headers
    )
    assert bad.status_code == 422
    assert (await client.delete(f"/api/plugins/connections/{connection['id']}", headers=auth_headers)).status_code == 204
    assert (await client.get("/api/plugins/connections", headers=auth_headers)).json() == []


@pytest.mark.asyncio
async def test_api_viewers_cannot_connect(
    client: AsyncClient, auth_headers: dict, viewer_auth_headers: dict, catalog, plugin_runner
):
    plan = (await client.get(f"/api/plugins/{PLUGIN}/plan", params={"action": "install"}, headers=auth_headers)).json()
    body = {"action": "install", "commit": plan["commit"], "token": plan["token"]}
    await client.post(f"/api/plugins/{PLUGIN}/apply", json=body, headers=auth_headers)
    resp = await client.post(
        "/api/plugins/connections",
        json={"plugin_id": PLUGIN, "integration": "keys", "values": {"api_key": "k"}},
        headers=viewer_auth_headers,
    )
    assert resp.status_code == 403


def test_scoped_tokens_reach_invoices():
    from app.core.access_token_scopes import ALL_SCOPES, required_scope

    assert "invoices:write" in ALL_SCOPES
    assert required_scope("POST", "/api/invoices/{invoice_id}/payments") == "invoices:write"
    assert required_scope("GET", "/api/invoice-schedules") == "invoices:read"


# --- Hardening found in review ------------------------------------------------------


async def _runner_commits(plugin_runner) -> list[str]:
    async with plugin_runner() as client:
        return sorted(p.commit for p in await client.installed())


async def _second_workspace(session, user_id) -> uuid.UUID:
    from app.models.workspace import Workspace, WorkspaceMember

    other = uuid.uuid4()
    session.add(Workspace(id=other, name="Other", kind="personal", default_currency="USD", locale="en"))
    await session.flush()
    session.add(WorkspaceMember(id=uuid.uuid4(), workspace_id=other, user_id=user_id, role="owner"))
    await session.commit()
    return other


@pytest.mark.asyncio
async def test_a_stale_preview_installs_no_code(session: AsyncSession, ids, catalog, plugin_runner):
    from app.plugins.sync import SyncPlanChanged

    workspace_id, user_id = ids
    plan = await service.plan(session, workspace_id, PLUGIN, "install", lang="en")
    with pytest.raises(SyncPlanChanged):
        await service.apply(
            session, workspace_id, user_id, PLUGIN, "install",
            commit=plan.commit, token="0" * 64, lang="en", is_owner=True,
        )
    assert await _runner_commits(plugin_runner) == []


@pytest.mark.asyncio
async def test_an_update_keeps_the_old_commit_while_another_workspace_runs_it(
    session: AsyncSession, ids, catalog, plugin_runner
):
    workspace_id, user_id = ids
    other = await _second_workspace(session, user_id)
    await _apply(session, workspace_id, user_id)
    await _apply(session, other, user_id)
    kept, _ = await _connect(session, other, user_id)

    catalog.publish(V2, lambda m: m.update(version="1.1.0"))
    await _apply(session, workspace_id, user_id, "update")
    assert await _runner_commits(plugin_runner) == [V1, V2]
    await connections.sync(session, kept)
    assert kept.status == "active"  # the other workspace still runs what its owner agreed to

    await _apply(session, other, user_id, "update")
    assert await _runner_commits(plugin_runner) == [V2]


@pytest.mark.asyncio
async def test_a_lost_commit_is_reinstalled_from_the_catalog(session: AsyncSession, ids, catalog, plugin_runner):
    workspace_id, user_id = ids
    await _apply(session, workspace_id, user_id)
    connection, _ = await _connect(session, workspace_id, user_id)
    async with plugin_runner() as client:
        await client.remove(PLUGIN)  # as if the runner's volume were new
    await connections.sync(session, connection)
    assert connection.status == "active"
    assert await _runner_commits(plugin_runner) == [V1]


@pytest.mark.asyncio
async def test_an_update_to_a_version_without_code_drops_connections(
    session: AsyncSession, ids, catalog, plugin_runner
):
    workspace_id, user_id = ids
    await _apply(session, workspace_id, user_id)
    connection, _ = await _connect(session, workspace_id, user_id)
    token_id = connection.access_token_id

    def data_only(m):
        for key in ("runtime", "network", "api_scopes"):
            m.pop(key)
        m["version"] = "2.0.0"
        m["contributes"] = {"rules": [{
            "key": "r", "name": "R", "conditions_op": "or",
            "conditions": [{"field": "description", "op": "contains", "value": "LEDGER"}],
            "actions": [{"op": "ignore"}],
        }]}

    catalog.publish(V2, data_only)
    await _apply(session, workspace_id, user_id, "update")
    assert await session.scalar(select(PluginConnection.id)) is None
    token = await session.get(AccessToken, token_id, populate_existing=True)
    assert token is not None and token.revoked_at is not None
    assert await _runner_commits(plugin_runner) == []


@pytest.mark.asyncio
async def test_secrets_never_come_back_in_error_messages(session: AsyncSession, ids, catalog, plugin_runner):
    workspace_id, user_id = ids
    await _apply(session, workspace_id, user_id)
    connection, _ = await _connect(session, workspace_id, user_id, api_key="sk_live_SECRET", account="leaky")
    await connections.sync(session, connection)
    assert connection.status == "error"
    assert "sk_live_SECRET" not in (connection.last_error or "")
    assert "key=***" in (connection.last_error or "")


@pytest.mark.asyncio
async def test_a_connection_syncs_once_at_a_time(session: AsyncSession, ids, catalog, plugin_runner, sync_locks):
    workspace_id, user_id = ids
    await _apply(session, workspace_id, user_id)
    connection, _ = await _connect(session, workspace_id, user_id)
    sync_locks.add(f"plugin-sync:{connection.id}")  # another sync is running
    await connections.sync(session, connection)
    assert connection.state == {} and connection.last_sync_at is None
    sync_locks.clear()
    await connections.sync(session, connection)
    assert connection.state["runs"] == 1


@pytest.mark.asyncio
async def test_one_failing_connection_does_not_stop_the_schedule(
    session: AsyncSession, ids, catalog, plugin_runner, monkeypatch
):
    from app.tasks.plugin_tasks import sync_due

    workspace_id, user_id = ids
    await _apply(session, workspace_id, user_id)
    first, _ = await _connect(session, workspace_id, user_id)
    second, _ = await _connect(session, workspace_id, user_id)
    first_id, second_id = first.id, second.id
    real_sync = connections.sync

    async def flaky(session_, connection):
        if connection.id == first_id:
            raise RuntimeError("database hiccup")
        return await real_sync(session_, connection)

    monkeypatch.setattr(connections, "sync", flaky)
    assert await sync_due(session) == 1
    refreshed = await session.get(PluginConnection, second_id, populate_existing=True)
    assert refreshed is not None and refreshed.state["runs"] == 1



def test_masking_reaches_nested_secrets():
    secrets = {"oauth": {"access_token": "token-1234", "scopes": ["read"]}, "api_key": "sk_9999"}
    assert connections.mask("Bearer token-1234 with sk_9999\x00", secrets) == "Bearer *** with ***"


@pytest.mark.asyncio
async def test_an_update_carries_new_intervals_and_drops_removed_integrations(
    session: AsyncSession, ids, catalog, plugin_runner, states
):
    workspace_id, user_id = ids
    await _apply(session, workspace_id, user_id)
    keys, _ = await _connect(session, workspace_id, user_id)
    signin, _ = await _connect(session, workspace_id, user_id, "signin", client="abc")
    signin_id, signin_token = signin.id, signin.access_token_id

    def v2(m):
        m["version"] = "1.1.0"
        m["contributes"]["integrations"] = [dict(m["contributes"]["integrations"][0], sync_minutes=None)]

    catalog.publish(V2, v2)
    await _apply(session, workspace_id, user_id, "update")
    await session.refresh(keys)
    assert keys.sync_minutes is None
    assert await session.get(PluginConnection, signin_id, populate_existing=True) is None
    token = await session.get(AccessToken, signin_token, populate_existing=True)
    assert token is not None and token.revoked_at is not None


@pytest.mark.asyncio
async def test_a_connection_added_after_the_preview_is_never_deleted(session: AsyncSession, ids, catalog, plugin_runner):
    from app.plugins.sync import SyncPlanChanged

    workspace_id, user_id = ids
    await _apply(session, workspace_id, user_id)
    plan = await service.plan(session, workspace_id, PLUGIN, "remove", lang="en")
    late, _ = await _connect(session, workspace_id, user_id)
    with pytest.raises(SyncPlanChanged):
        await service.apply(
            session, workspace_id, user_id, PLUGIN, "remove",
            commit=plan.commit, token=plan.sync.token, lang="en", is_owner=True,
        )
    assert await session.get(PluginConnection, late.id, populate_existing=True) is not None


@pytest.mark.asyncio
async def test_the_preview_says_an_update_without_code_drops_connections(
    session: AsyncSession, ids, catalog, plugin_runner
):
    workspace_id, user_id = ids
    await _apply(session, workspace_id, user_id)
    await _connect(session, workspace_id, user_id)

    def data_only(m):
        for key in ("runtime", "network", "api_scopes"):
            m.pop(key)
        m["version"] = "2.0.0"
        m["contributes"] = {"rules": [{
            "key": "r", "name": "R", "conditions_op": "or",
            "conditions": [{"field": "description", "op": "contains", "value": "LEDGER"}],
            "actions": [{"op": "ignore"}],
        }]}

    catalog.publish(V2, data_only)
    plan = await service.plan(session, workspace_id, PLUGIN, "update", lang="en")
    assert plan.code is not None and plan.code.drops_code and plan.code.connections == 1
