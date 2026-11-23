"""File fields and routines of a plugin's integration (ADR 0035, 0038)."""
from __future__ import annotations

import base64
import copy
import uuid
from datetime import timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import select, update

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
        if context.secrets["password"] != "hunter22":
            raise type("AuthError", (Exception,), {})("the key was refused")
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

    assert await connections.run_routine(session, connection, send) == "ran"
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
    assert await connections.run_routine(session, connection, send) == "busy"
    assert send.last_run_at is None


@pytest.mark.asyncio
async def test_a_routine_reads_the_connection_again_under_the_lock(session, ids, installed):
    await _install(session, ids)
    connection = await _connect(session, ids)
    send = await _routine(session, connection, "send")
    # Another run ran it meanwhile, behind these copies' backs: they still say it never ran.
    stale = {"synchronize_session": False}
    await session.execute(
        update(PluginConnectionRoutine).where(PluginConnectionRoutine.id == send.id)
        .values(last_run_at=connections._now(), state={"runs": 1}).execution_options(**stale)
    )
    assert send.last_run_at is None
    # A scheduled run reads it again under the lock and finds it no longer due; running it now still runs.
    assert await connections.run_routine(session, connection, send, scheduled=True) == "not_due"
    assert send.state == {"runs": 1}
    assert await connections.run_routine(session, connection, send) == "ran"
    assert send.state["runs"] == 2

    # Another run found the sign-in gone meanwhile, and this copy doesn't know.
    await session.execute(
        update(PluginConnection).where(PluginConnection.id == connection.id)
        .values(status="needs_auth").execution_options(**stale)
    )
    assert connection.status != "needs_auth"
    assert await connections.run_routine(session, connection, send) == "needs_auth"
    assert send.state["runs"] == 2


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
    # The real dispatch, down to the task it queues; only the queue itself is replaced.
    queued: list[str] = []
    monkeypatch.setattr(plugin_tasks.sync_plugin_connection, "delay", lambda cid: queued.append(f"sync:{cid}"))
    monkeypatch.setattr(plugin_tasks.run_plugin_routines, "delay", lambda cid, force=False: queued.append(f"routines:{cid}"))
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


@pytest.mark.asyncio
async def test_renaming_a_connection_keeps_it_as_it_was(session, ids, installed):
    await _install(session, ids)
    connection = await _connect(session, ids)
    assert await connections.run_routine(session, connection, await _routine(session, connection, "send")) == "ran"
    await session.refresh(connection)
    assert connection.status == "active"

    renamed, changed = await connections.update_values(session, connection, name="Payroll", values={})
    assert renamed.name == "Payroll"
    assert renamed.status == "active"
    assert changed is False
    # The same key again isn't a change either.
    _, changed = await connections.update_values(session, connection, name=None, values={"password": "hunter22"})
    assert changed is False
    _, changed = await connections.update_values(session, connection, name=None, values={"password": "hunter23"})
    assert changed is True


@pytest.mark.asyncio
async def test_renaming_a_pending_connection_queues_nothing(
    client: AsyncClient, auth_headers: dict, session, ids, installed, monkeypatch
):
    queued: list[tuple[str, bool]] = []
    monkeypatch.setattr(plugin_tasks.run_plugin_routines, "delay", lambda cid, force=False: queued.append((cid, force)))
    monkeypatch.setattr(plugin_tasks.sync_plugin_connection, "delay", lambda cid: queued.append(("sync", cid)))
    await _install(session, ids)
    connection = await _connect(session, ids)
    assert connection.status == "pending"

    renamed = await client.put(
        f"/api/plugins/connections/{connection.id}/values", json={"name": "Payroll", "values": {}}, headers=auth_headers
    )
    assert renamed.status_code == 200 and renamed.json()["status"] == "pending"
    # Still pending from connecting, whose first round was already queued: a rename adds no work.
    assert queued == []


@pytest.mark.asyncio
async def test_saving_new_keys_runs_the_routines_at_once(
    client: AsyncClient, auth_headers: dict, session, ids, installed, monkeypatch
):
    # The dispatch is real down to the task it queues; only the queue is replaced.
    queued: list[tuple[str, bool]] = []
    monkeypatch.setattr(plugin_tasks.run_plugin_routines, "delay", lambda cid, force=False: queued.append((cid, force)))
    monkeypatch.setattr(plugin_tasks.sync_plugin_connection, "delay", lambda cid: queued.append(("sync", cid)))
    await _install(session, ids)
    connection = await _connect(session, ids)
    connection.status = "active"
    await session.commit()

    renamed = await client.put(
        f"/api/plugins/connections/{connection.id}/values", json={"name": "Payroll", "values": {}}, headers=auth_headers
    )
    assert renamed.status_code == 200 and renamed.json()["status"] == "active"
    # A rename alone asks for nothing to run.
    assert queued == []

    updated = await client.put(
        f"/api/plugins/connections/{connection.id}/values", json={"values": {"password": "hunter23"}}, headers=auth_headers
    )
    assert updated.status_code == 200 and updated.json()["status"] == "pending"
    # New keys: the routines run now, not at each own next window.
    assert queued == [(str(connection.id), True)]


@pytest.mark.asyncio
async def test_new_keys_run_every_routine_even_when_none_is_due(session, ids, installed, monkeypatch):
    await _install(session, ids)
    connection = await _connect(session, ids)
    for key in ("send", "collect"):
        await connections.run_routine(session, connection, await _routine(session, connection, key))
    rows = await connections.list_routines(session, [connection.id])
    before = {r.routine: r.last_run_at for r in rows}
    assert all(not connections.routine_due(r, connection) for r in rows if r.routine in ("send", "collect"))

    monkeypatch.setattr(plugin_tasks, "_session_maker", lambda: (_Engine(), lambda: _Same(session)))
    monkeypatch.setattr(plugin_tasks, "close_redis", _noop)
    await plugin_tasks._run_routines_of(str(connection.id), force=True)

    rows = await connections.list_routines(session, [connection.id])
    for row in rows:
        if row.routine in ("send", "collect"):
            # It ran again even though its own window hadn't come.
            assert row.state["runs"] == 2 and row.last_run_at != before[row.routine]


@pytest.mark.asyncio
async def test_a_forced_round_that_finds_the_connection_busy_tries_again(
    session, ids, installed, sync_locks, monkeypatch
):
    await _install(session, ids)
    connection = await _connect(session, ids)
    for key in ("send", "collect"):
        await connections.run_routine(session, connection, await _routine(session, connection, key))
    monkeypatch.setattr(plugin_tasks, "_session_maker", lambda: (_Engine(), lambda: _Same(session)))
    monkeypatch.setattr(plugin_tasks, "close_redis", _noop)
    lock = f"plugin-sync:{connection.id}"

    # Busy: nothing ran, and every routine that didn't is handed back to try again.
    sync_locks.add(lock)
    busy = await plugin_tasks._run_routines_of(str(connection.id), force=True)
    rows = await connections.list_routines(session, [connection.id])
    assert sorted(busy) == sorted(r.routine for r in rows)
    assert all(r.state["runs"] == 1 for r in rows if r.routine in ("send", "collect"))
    # An unforced round leaves a busy routine to the schedule, where it is still due.
    assert await plugin_tasks._run_routines_of(str(connection.id)) == []

    # Once the lock is free, the retry runs exactly those, even though none is due.
    sync_locks.discard(lock)
    assert await plugin_tasks._run_routines_of(str(connection.id), force=True, only=["send"]) == []
    rows = {r.routine: r for r in await connections.list_routines(session, [connection.id])}
    assert rows["send"].state["runs"] == 2
    assert rows["collect"].state["runs"] == 1


def test_the_task_requeues_busy_routines_and_then_gives_up(monkeypatch):
    connection_id = str(uuid.uuid4())
    monkeypatch.setattr(plugin_tasks, "_enabled", lambda: True)
    queued: list[tuple] = []
    monkeypatch.setattr(
        plugin_tasks.run_plugin_routines, "apply_async", lambda args, countdown: queued.append((args, countdown))
    )

    async def still_busy(cid, *, force=False, only=None):
        return ["send"]

    monkeypatch.setattr(plugin_tasks, "_run_routines_of", still_busy)
    assert plugin_tasks.run_plugin_routines.run(connection_id, True)["status"] == "retrying"
    assert queued == [((connection_id, True, ["send"], 1), plugin_tasks.FORCED_RETRY_SECONDS)]
    # After the last attempt it stops and the schedule takes over.
    last = plugin_tasks.run_plugin_routines.run(connection_id, True, ["send"], plugin_tasks.FORCED_RETRIES)
    assert last == {"status": "busy", "routines": ["send"]}
    assert len(queued) == 1

    async def needs_auth(cid, *, force=False, only=None):
        return []

    monkeypatch.setattr(plugin_tasks, "_run_routines_of", needs_auth)
    assert plugin_tasks.run_plugin_routines.run(connection_id, True) == {"status": "ok"}
    assert len(queued) == 1


@pytest.mark.asyncio
async def test_a_refused_key_is_fixed_by_new_keys_that_run_the_routines(
    client: AsyncClient, auth_headers: dict, session, ids, installed, monkeypatch
):
    # The queue is a no-op here: this test runs what it would have run.
    monkeypatch.setattr(plugin_tasks.run_plugin_routines, "delay", lambda cid, force=False: None)
    await _install(session, ids)
    connection = await _connect(session, ids, bundle=BUNDLE, password="refused")
    connection_id = connection.id
    assert await connections.run_routine(session, connection, await _routine(session, connection, "send")) == "ran"
    await session.refresh(connection)
    assert connection.status == "needs_auth"
    # Nothing runs while the key is refused.
    refused = await client.post(f"/api/plugins/connections/{connection_id}/routines/send/run", headers=auth_headers)
    assert refused.status_code == 409

    fixed = await client.put(
        f"/api/plugins/connections/{connection_id}/values", json={"values": {"password": "hunter22"}}, headers=auth_headers
    )
    assert fixed.status_code == 200 and fixed.json()["status"] == "pending"

    # What the API queued runs every routine at once and the connection is working.
    session.expire_all()
    monkeypatch.setattr(plugin_tasks, "_session_maker", lambda: (_Engine(), lambda: _Same(session)))
    monkeypatch.setattr(plugin_tasks, "close_redis", _noop)
    await plugin_tasks._run_routines_of(str(connection_id), force=True)
    await session.refresh(connection)
    assert connection.status == "active"
    send = await _routine(session, connection, "send")
    assert send.last_error is None and send.state["runs"] == 1


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
