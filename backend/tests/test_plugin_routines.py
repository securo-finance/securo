"""File fields and routines of a plugin's integration (ADR 0035)."""
from __future__ import annotations

import base64
import copy
from datetime import timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import select

from app.models.plugin_connection import PluginConnection, PluginConnectionRoutine
from app.plugins import connections, vault
from app.plugins.manifest import parse_manifest
from app.tasks import plugin_tasks
from tests import test_code_plugins as code_plugins
from tests.test_code_plugins import PLUGIN, V1, V2, _apply, _tarball

catalog = code_plugins.catalog
plugin_runner = code_plugins.plugin_runner
sync_locks = code_plugins.sync_locks
ids = code_plugins.ids

SOURCE = '''
import base64


class Plugin:
    def sync(self, params, context):
        return {"state": {"synced": True}}

    def routine(self, params, context):
        if params["routine"] == "broken":
            raise RuntimeError("the remote end said no to " + context.secrets["password"])
        if params["routine"] == "expired":
            raise type("NeedsAuth", (Exception,), {})("sign in again")
        bundle = context.secrets["bundle"]
        layout = context.config.get("layout")
        runs = params["state"].get("runs", 0) + 1
        return {
            "state": {
                "runs": runs,
                "routine": params["routine"],
                "bundle": [bundle["name"], len(base64.b64decode(bundle["content"]))],
                "layout": layout and layout["name"],
                "last_run_at": params["last_run_at"],
            },
            "secrets": {"session": "s-" + str(runs)},
        }

plugin = Plugin()
'''

BUNDLE = {"name": "keys.p12", "content": base64.b64encode(b"\x00binary\xffbundle").decode()}
LAYOUT = {"name": "layout.csv", "content": base64.b64encode(b"a,b\n1,2\n").decode()}


def _manifest(**integration: Any) -> dict[str, Any]:
    manifest = copy.deepcopy(code_plugins.MANIFEST)
    manifest["contributes"] = {
        "integrations": [
            {
                "key": "files",
                "name": "Files",
                "auth": "credentials",
                "fields": [
                    {"key": "bundle", "label": "Key bundle", "type": "file", "secret": True, "accept": [".p12", ".pfx"], "max_kb": 4},
                    {"key": "password", "label": "Password", "secret": True},
                    {"key": "layout", "label": "Layout", "type": "file", "required": False},
                ],
                "routines": [
                    {"key": "send", "name": {"en": "Send", "pt-BR": "Enviar"}, "every_minutes": 15, "timeout_seconds": 30},
                    {"key": "collect", "name": "Collect", "every_minutes": 60},
                    {"key": "broken", "name": "Broken", "every_minutes": 15},
                ],
                **integration,
            }
        ]
    }
    return manifest


@pytest.fixture
def installed(catalog, plugin_runner):
    catalog.manifests[V1] = _manifest()
    catalog.archives[V1] = _tarball(SOURCE)
    return catalog


async def _install(session, ids_):
    await _apply(session, *ids_)


async def _connect(session, ids_, **values: Any) -> PluginConnection:
    workspace_id, user_id = ids_
    connection, _ = await connections.create(
        session, workspace_id=workspace_id, user_id=user_id, can_write=True, plugin_id=PLUGIN,
        integration_key="files", name="", lang="en",
        values=values or {"bundle": BUNDLE, "password": "hunter22", "layout": LAYOUT},
    )
    return connection


async def _routine(session, connection, key) -> PluginConnectionRoutine:
    return await connections.get_routine(session, connection, key)


# --- The manifest -------------------------------------------------------------


def test_a_field_can_take_a_file_and_an_integration_can_run_routines():
    integration = parse_manifest(_manifest()).contributes.integrations[0]
    bundle, password, layout = integration.fields
    assert (bundle.type, bundle.accept, bundle.max_kb) == ("file", [".p12", ".pfx"], 4)
    assert (password.type, layout.max_kb) == ("text", 512)
    assert [(r.key, r.every_minutes, r.timeout_seconds) for r in integration.routines] == [
        ("send", 15, 30), ("collect", 60, None), ("broken", 15, None),
    ]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda i: i["fields"][1].update(accept=[".txt"]), "accept and max_kb are for a file field"),
        (lambda i: i["fields"][0].update(accept=["p12"]), "accept"),
        (lambda i: i["fields"][0].update(max_kb=4096), "max_kb"),
        (lambda i: i["fields"][0].update(type="folder"), "type"),
        (lambda i: i["routines"].append({"key": "send", "name": "Again", "every_minutes": 15}), "routine keys must be unique"),
        (lambda i: i["routines"][0].update(every_minutes=5), "every_minutes"),
        (lambda i: i["routines"][0].update(timeout_seconds=3600), "timeout_seconds"),
        (lambda i: i["routines"][0].pop("every_minutes"), "every_minutes"),
    ],
    ids=["file-options-on-text", "extension-without-dot", "too-large", "unknown-type", "duplicate-routine",
         "too-often", "too-long", "no-schedule"],
)
def test_fields_and_routines_that_break_the_rules_are_refused(change, message):
    manifest = _manifest()
    change(manifest["contributes"]["integrations"][0])
    with pytest.raises(ValidationError, match=message):
        parse_manifest(manifest)


# --- File fields ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_file_is_sealed_with_the_secrets_or_kept_with_the_settings(session, ids, installed):
    await _install(session, ids)
    connection = await _connect(session, ids)
    secrets = vault.unseal(connection.secrets)
    assert secrets["bundle"] == BUNDLE and "bundle" not in connection.config
    assert connection.config["layout"] == LAYOUT
    assert connections.file_summary(connection.config["layout"]) == {"name": "layout.csv", "size": 8}


@pytest.mark.parametrize(
    ("bundle", "message"),
    [
        ({"name": "keys.txt", "content": BUNDLE["content"]}, "must be a .p12, .pfx file"),
        ({"name": "keys.p12", "content": "not base64!"}, "can't be read as a file"),
        ({"name": "keys.p12", "content": base64.b64encode(b"x" * 5000).decode()}, "at most 4 KB"),
        ({"name": "keys.p12", "content": ""}, "must be a file|is empty"),
        ("just text", "must be a file"),
        ({"name": "", "content": BUNDLE["content"]}, "must be a file"),
    ],
    ids=["wrong-extension", "not-base64", "too-big", "empty", "text", "no-name"],
)
@pytest.mark.asyncio
async def test_a_file_that_doesnt_fit_the_field_is_refused(session, ids, installed, bundle, message):
    await _install(session, ids)
    with pytest.raises(connections.PluginConnectionError, match=message):
        await _connect(session, ids, bundle=bundle, password="hunter22")


@pytest.mark.asyncio
async def test_only_the_files_own_name_is_kept(session, ids, installed):
    await _install(session, ids)
    connection = await _connect(session, ids, bundle={**BUNDLE, "name": "C:\\\\Users\\\\me\\\\KEYS.P12"}, password="x" * 8)
    assert vault.unseal(connection.secrets)["bundle"]["name"] == "KEYS.P12"


@pytest.mark.asyncio
async def test_the_api_shows_a_files_name_and_size_never_its_content(
    client: AsyncClient, auth_headers: dict, session, ids, installed
):
    await _install(session, ids)
    created = await client.post(
        "/api/plugins/connections",
        json={"plugin_id": PLUGIN, "integration": "files", "values": {"bundle": BUNDLE, "password": "hunter22", "layout": LAYOUT}},
        headers=auth_headers,
    )
    assert created.status_code == 201, created.text
    body = created.json()["connection"]
    assert body["config"] == {"layout": {"name": "layout.csv", "size": 8}}
    assert body["secret_fields"] == ["bundle", "password"]
    assert BUNDLE["content"] not in created.text and LAYOUT["content"] not in created.text

    # Leaving a file out keeps it; a new one replaces it.
    connection_id = body["id"]
    kept = await client.put(f"/api/plugins/connections/{connection_id}/values", json={"values": {}}, headers=auth_headers)
    assert kept.status_code == 200 and kept.json()["config"]["layout"]["name"] == "layout.csv"
    other = {"name": "other.csv", "content": base64.b64encode(b"x").decode()}
    replaced = await client.put(
        f"/api/plugins/connections/{connection_id}/values", json={"values": {"layout": other}}, headers=auth_headers
    )
    assert replaced.json()["config"]["layout"] == {"name": "other.csv", "size": 1}


# --- Routines -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_connection_gets_one_row_per_routine(session, ids, installed):
    await _install(session, ids)
    connection = await _connect(session, ids)
    rows = await connections.list_routines(session, [connection.id])
    assert [(r.routine, r.every_minutes, r.timeout_seconds, r.last_run_at) for r in rows] == [
        ("broken", 15, None, None), ("collect", 60, None, None), ("send", 15, 30, None),
    ]


@pytest.mark.asyncio
async def test_a_routine_runs_the_plugins_routine_method_with_its_own_state(session, ids, installed):
    await _install(session, ids)
    connection = await _connect(session, ids)
    send = await _routine(session, connection, "send")

    assert await connections.run_routine(session, connection, send) is send
    assert send.failures == 0 and send.last_error is None and send.last_run_at is not None
    assert send.state == {
        "runs": 1, "routine": "send", "bundle": ["keys.p12", 14], "layout": "layout.csv", "last_run_at": None,
    }
    # Secrets it hands back are merged in; the form's stay.
    secrets = vault.unseal(connection.secrets)
    assert secrets["session"] == "s-1" and secrets["password"] == "hunter22"

    await connections.run_routine(session, connection, send)
    assert send.state["runs"] == 2 and send.state["last_run_at"] is not None
    collect = await _routine(session, connection, "collect")
    assert collect.state == {}  # each routine keeps its own


@pytest.mark.asyncio
async def test_a_failing_routine_backs_off_on_its_own_and_hides_secrets(session, ids, installed):
    await _install(session, ids)
    connection = await _connect(session, ids)
    broken = await _routine(session, connection, "broken")
    await connections.run_routine(session, connection, broken)
    error = broken.last_error or ""
    assert broken.failures == 1 and "said no to ***" in error and "hunter22" not in error
    # The connection itself isn't in error for it.
    assert connection.status != "error"

    now = broken.last_run_at
    assert now is not None
    assert not connections.routine_due(broken, connection, now + timedelta(minutes=15))
    assert connections.routine_due(broken, connection, now + timedelta(minutes=30))


@pytest.mark.asyncio
async def test_a_routine_that_needs_signing_in_stops_the_connection(session, ids, catalog, plugin_runner):
    manifest = _manifest()
    manifest["contributes"]["integrations"][0]["routines"].append({"key": "expired", "name": "Expired", "every_minutes": 15})
    catalog.manifests[V1] = manifest
    catalog.archives[V1] = _tarball(SOURCE)
    await _install(session, ids)
    connection = await _connect(session, ids)
    expired = await _routine(session, connection, "expired")
    await connections.run_routine(session, connection, expired)
    assert connection.status == "needs_auth"
    send = await _routine(session, connection, "send")
    assert not connections.routine_due(send, connection)


@pytest.mark.asyncio
async def test_a_routine_waits_while_the_connection_is_busy(session, ids, installed, sync_locks):
    await _install(session, ids)
    connection = await _connect(session, ids)
    send = await _routine(session, connection, "send")
    sync_locks.add(f"plugin-sync:{connection.id}")
    assert await connections.run_routine(session, connection, send) is None
    assert send.last_run_at is None


@pytest.mark.asyncio
async def test_a_connection_without_a_sync_is_active_once_a_routine_ran(session, ids, installed):
    await _install(session, ids)
    connection = await _connect(session, ids)
    assert connection.status == "pending"
    await connections.run_routine(session, connection, await _routine(session, connection, "send"))
    assert connection.status == "active"


@pytest.mark.asyncio
async def test_the_schedule_runs_only_the_routines_whose_time_came(session, ids, installed):
    await _install(session, ids)
    connection = await _connect(session, ids)
    assert await plugin_tasks.run_due_routines(session) == 3
    assert await plugin_tasks.run_due_routines(session) == 0
    collect = await _routine(session, connection, "collect")
    assert collect.last_run_at is not None
    collect.last_run_at = collect.last_run_at - timedelta(minutes=61)
    await session.commit()
    assert await plugin_tasks.run_due_routines(session) == 1


@pytest.mark.asyncio
async def test_routines_follow_an_update_of_the_plugin(session, ids, installed):
    await _install(session, ids)
    connection = await _connect(session, ids)
    send = await _routine(session, connection, "send")
    await connections.run_routine(session, connection, send)

    def change(manifest):
        manifest["version"] = "1.1.0"
        routines = manifest["contributes"]["integrations"][0]["routines"]
        routines[0]["every_minutes"] = 30
        del routines[1]  # collect is gone
        routines.append({"key": "report", "name": "Report", "every_minutes": 1440})

    installed.publish(V2, change)
    installed.archives[V2] = _tarball(SOURCE + "\n# v2")
    await _apply(session, *ids, "update")
    rows = {r.routine: r for r in await connections.list_routines(session, [connection.id])}
    assert set(rows) == {"send", "broken", "report"}
    assert rows["send"].every_minutes == 30 and rows["send"].state["runs"] == 1  # kept its history


@pytest.mark.asyncio
async def test_removing_a_connection_or_the_plugin_takes_its_routines(session, ids, installed):
    await _install(session, ids)
    first = await _connect(session, ids)
    await _connect(session, ids)
    await connections.delete(session, first)
    assert len(await connections.list_routines(session, [first.id])) == 0
    await _apply(session, *ids, "remove")
    assert (await session.scalars(select(PluginConnectionRoutine))).all() == []


@pytest.mark.asyncio
async def test_the_api_lists_routines_and_runs_one_now(
    client: AsyncClient, auth_headers: dict, viewer_auth_headers: dict, session, ids, installed
):
    await _install(session, ids)
    connection = await _connect(session, ids)
    listed = (await client.get("/api/plugins/connections", headers=auth_headers)).json()
    assert [r["key"] for r in listed[0]["routines"]] == ["broken", "collect", "send"]
    assert "state" not in listed[0]["routines"][0]
    (plugin,) = (await client.get("/api/plugins", headers=auth_headers)).json()
    routines = plugin["integrations"][0]["routines"]
    assert routines[0] == {"key": "send", "name": {"en": "Send", "pt-BR": "Enviar"}, "description": None,
                           "every_minutes": 15, "timeout_seconds": 30}
    assert plugin["integrations"][0]["fields"][0]["type"] == "file"

    url = f"/api/plugins/connections/{connection.id}/routines/send/run"
    assert (await client.post(url, headers=viewer_auth_headers)).status_code == 403
    ran = await client.post(url, headers=auth_headers)
    assert ran.status_code == 200, ran.text
    send = next(r for r in ran.json()["routines"] if r["key"] == "send")
    assert send["last_run_at"] is not None and send["failures"] == 0
    missing = await client.post(f"/api/plugins/connections/{connection.id}/routines/nope/run", headers=auth_headers)
    assert missing.status_code == 404


# --- An integration that only runs routines -----------------------------------------


def test_an_integration_with_routines_and_no_schedule_doesnt_sync():
    only_routines = parse_manifest(_manifest()).contributes.integrations[0]
    assert not only_routines.syncs
    assert parse_manifest(_manifest(sync_minutes=60)).contributes.integrations[0].syncs
    plain = _manifest(routines=[])
    assert parse_manifest(plain).contributes.integrations[0].syncs  # manual sync, as before


@pytest.mark.asyncio
async def test_connecting_runs_its_routines_and_never_calls_sync(
    client: AsyncClient, auth_headers: dict, session, ids, installed, monkeypatch
):
    from app.api import plugins as plugins_api

    queued: list[str] = []
    monkeypatch.setattr(plugins_api, "_queue_sync", lambda cid: queued.append(f"sync:{cid}"))
    monkeypatch.setattr(plugins_api, "_queue_routines", lambda cid: queued.append(f"routines:{cid}"))
    await _install(session, ids)
    created = await client.post(
        "/api/plugins/connections",
        json={"plugin_id": PLUGIN, "integration": "files", "values": {"bundle": BUNDLE, "password": "hunter22"}},
        headers=auth_headers,
    )
    connection_id = created.json()["connection"]["id"]
    assert queued == [f"routines:{connection_id}"]
    synced = await client.post(f"/api/plugins/connections/{connection_id}/sync", headers=auth_headers)
    assert synced.status_code == 409


@pytest.mark.asyncio
async def test_the_first_round_runs_every_routine_of_a_new_connection(session, ids, installed, monkeypatch):
    await _install(session, ids)
    connection = await _connect(session, ids)
    monkeypatch.setattr(plugin_tasks, "_session_maker", lambda: (_Engine(), lambda: _Same(session)))
    monkeypatch.setattr(plugin_tasks, "close_redis", _noop)
    await plugin_tasks._run_routines_of(str(connection.id))
    rows = await connections.list_routines(session, [connection.id])
    assert all(r.last_run_at is not None for r in rows)
    assert connection.status == "active"


class _Engine:
    async def dispose(self) -> None:
        return None


class _Same:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *exc) -> None:
        return None


async def _noop() -> None:
    return None
