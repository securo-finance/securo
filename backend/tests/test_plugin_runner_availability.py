"""Plugin runner reachability: catalog availability, TTL cache and 503 vs 409 responses (#1877)."""
from __future__ import annotations

import copy
import hashlib
import io
import json
import logging
import tarfile
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from httpx import AsyncClient

from app.main import _check_plugin_runner
from app.plugins import index, runner
from tests import test_data_plugins as data_plugins

CODE_PLUGIN = "acme/ledger"
CODE_REPO = "https://github.com/acme/securo-ledger"
DATA_PLUGIN = data_plugins.PLUGIN
REPORT_PLUGIN = "acme/code-report"
INDEX_URL = data_plugins.INDEX_URL
V1 = "1" * 40
RUNNER_TOKEN = "runner-token-for-tests-0123456789abcdef"

CODE_MANIFEST: dict[str, Any] = {
    "id": CODE_PLUGIN,
    "name": "Ledger",
    "description": "Brings in what happens on Ledger.",
    "version": "1.0.0",
    "repository": CODE_REPO,
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
                "fields": [{"key": "api_key", "label": "API key", "secret": True}],
                "sync_minutes": 60,
            }
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


class MixedCatalog:
    """Fake catalog serving a code integration plugin, a code report plugin, and a data plugin."""

    def __init__(self) -> None:
        self.code_manifest = copy.deepcopy(CODE_MANIFEST)
        self.code_archive = _tarball("class Plugin:\n    pass\nplugin = Plugin()\n")
        self.data_manifest = copy.deepcopy(data_plugins.MANIFEST)
        self.report_manifest: dict[str, Any] = {
            **copy.deepcopy(CODE_MANIFEST),
            "id": REPORT_PLUGIN,
            "name": "Code Report",
            "repository": "https://github.com/acme/securo-code-report",
            "api_scopes": [{"scope": "transactions:read", "purpose": "Reads spending"}],
            "contributes": {
                "reports": [
                    {
                        "key": "insights",
                        "name": {"en": "Insights"},
                        "code": True,
                        "inputs": ["transactions"],
                    }
                ]
            },
        }
        self.report_archive = _tarball("class Plugin:\n    pass\nplugin = Plugin()\n")
        self.code_extra: dict[str, Any] = {}

    def _entry(
        self,
        plugin_id: str,
        manifest: dict[str, Any],
        archive: bytes | None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = json.dumps(manifest).encode()
        entry: dict[str, Any] = {
            "id": plugin_id,
            "repository": manifest["repository"],
            "release": f"v{manifest['version']}",
            "commit": V1,
            "manifest": f"manifests/{plugin_id}.json",
            "manifest_sha256": hashlib.sha256(body).hexdigest(),
            "name": manifest["name"],
            "description": manifest["description"],
            "version": manifest["version"],
            "license": manifest["license"],
            "min_securo_version": manifest["min_securo_version"],
            "contributes": {k: len(v) for k, v in manifest["contributes"].items()},
        }
        if archive is not None:
            entry["archive"] = f"archives/{plugin_id}.tar.gz"
            entry["archive_sha256"] = hashlib.sha256(archive).hexdigest()
        if extra:
            entry.update(extra)
        return entry

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == INDEX_URL:
            return httpx.Response(
                200,
                json={
                    "plugins": [
                        self._entry(CODE_PLUGIN, self.code_manifest, self.code_archive, self.code_extra),
                        self._entry(REPORT_PLUGIN, self.report_manifest, self.report_archive),
                        self._entry(DATA_PLUGIN, self.data_manifest, None),
                    ]
                },
            )
        manifests = {
            CODE_PLUGIN: self.code_manifest,
            REPORT_PLUGIN: self.report_manifest,
            DATA_PLUGIN: self.data_manifest,
        }
        archives = {
            CODE_PLUGIN: self.code_archive,
            REPORT_PLUGIN: self.report_archive,
        }
        for plugin_id, manifest in manifests.items():
            if url.endswith(f"/manifests/{plugin_id}.json"):
                return httpx.Response(200, content=json.dumps(manifest).encode())
        for plugin_id, archive in archives.items():
            if url.endswith(f"/archives/{plugin_id}.tar.gz"):
                return httpx.Response(200, content=archive)
        return httpx.Response(404)


@pytest.fixture(autouse=True)
def _reset_cache():
    runner.reset_reachability_cache()
    yield
    runner.reset_reachability_cache()


@pytest.fixture
def mixed_catalog(monkeypatch) -> MixedCatalog:
    fake = MixedCatalog()
    monkeypatch.setattr(
        index, "http_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(fake.handle))
    )
    index._manifests.clear()
    index._readmes.clear()
    monkeypatch.setattr(index, "_last_archive", None)
    monkeypatch.setenv("PLUGINS_ENABLED", "true")
    return fake


def _configure_settings(monkeypatch, *, url: str = "http://plugins-runner:8090", token: str = RUNNER_TOKEN):
    fake_settings = SimpleNamespace(plugins_runner_url=url, plugins_runner_token=token)
    monkeypatch.setattr(runner, "get_settings", lambda: fake_settings)
    return fake_settings


def _make_toggleable_runner(monkeypatch, *, down: bool = False) -> dict[str, Any]:
    """Attach a mock HTTP runner whose reachability can be toggled on the same client class."""
    installed: list[dict[str, Any]] = []
    state: dict[str, Any] = {
        "down": down,
        "health_calls": 0,
        "timeouts": [],
        "installed": installed,
        "install_status": None,
    }

    def handle(request: httpx.Request) -> httpx.Response:
        if state["down"]:
            raise httpx.ConnectError("connection refused")
        if request.method == "GET" and request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "isolated": True})
        if request.method == "GET" and request.url.path == "/plugins":
            return httpx.Response(200, json=installed)
        if request.method == "PUT" and request.url.path.startswith("/plugins/"):
            if state["install_status"] is not None:
                return httpx.Response(state["install_status"], json={"detail": "install refused"})
            plugin_id = request.url.path.removeprefix("/plugins/")
            row = {"id": plugin_id, "commit": V1, "hosts": ["api.ledger.example:443"]}
            installed.append(row)
            return httpx.Response(200, json=row)
        if request.method == "DELETE" and request.url.path.startswith("/plugins/"):
            installed.clear()
            return httpx.Response(204)
        return httpx.Response(404)

    transport = httpx.MockTransport(handle)

    class ToggleClient(runner.RunnerClient):
        def __init__(self, transport_: httpx.AsyncBaseTransport | None = None):
            super().__init__(transport=transport)

        async def health(self, *, timeout: float = 3.0) -> dict[str, Any]:
            state["health_calls"] += 1
            state["timeouts"].append(timeout)
            return await super().health(timeout=timeout)

    _configure_settings(monkeypatch)
    monkeypatch.setattr(runner, "RunnerClient", ToggleClient)
    return state


# --- Runner reachability probe & TTL cache ------------------------------------


@pytest.mark.asyncio
async def test_reachable_returns_false_without_io_when_unconfigured(monkeypatch):
    _configure_settings(monkeypatch, url="", token="")

    class ShouldNotBeCalled:
        def __init__(self, *args, **kwargs):
            raise AssertionError("RunnerClient must not be constructed when unconfigured")

    monkeypatch.setattr(runner, "RunnerClient", ShouldNotBeCalled)
    assert await runner.reachable() is False


@pytest.mark.asyncio
async def test_reachable_ttl_cache_reset_force_and_config_key(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(runner.time, "monotonic", lambda: now[0])
    state = _make_toggleable_runner(monkeypatch, down=True)

    # First probe hits /health with the short 3.0s timeout and caches False.
    assert await runner.reachable() is False
    assert state["health_calls"] == 1
    assert state["timeouts"] == [3.0]

    # Second call within the TTL reuses the cached result without another probe.
    now[0] += runner._REACHABILITY_TTL - 1.0
    assert await runner.reachable() is False
    assert state["health_calls"] == 1

    # force=True bypasses the cache.
    assert await runner.reachable(force=True) is False
    assert state["health_calls"] == 2

    # reset_reachability_cache() clears the entry so the next probe runs immediately.
    state["down"] = False
    runner.reset_reachability_cache()
    assert await runner.reachable() is True
    assert state["health_calls"] == 3

    # Advancing past the TTL expires the cached True and sees the runner go down again.
    state["down"] = True
    now[0] += runner._REACHABILITY_TTL + 0.1
    assert await runner.reachable() is False
    assert state["health_calls"] == 4

    # Changing runner URL or token invalidates the cache key immediately.
    state["down"] = False
    _configure_settings(monkeypatch, url="http://other-runner:8090", token=RUNNER_TOKEN)
    assert await runner.reachable() is True
    assert state["health_calls"] == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [401, 500, 503])
async def test_reachable_returns_false_on_error_responses(monkeypatch, status_code: int):
    _configure_settings(monkeypatch)
    transport = httpx.MockTransport(lambda req: httpx.Response(status_code, json={"detail": "error"}))

    class ErrorClient(runner.RunnerClient):
        def __init__(self, transport_=None):
            super().__init__(transport=transport)

    monkeypatch.setattr(runner, "RunnerClient", ErrorClient)
    assert await runner.reachable() is False


@pytest.mark.asyncio
async def test_reachable_returns_false_on_timeout_and_non_json_health(monkeypatch):
    _configure_settings(monkeypatch)

    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    class TimeoutClient(runner.RunnerClient):
        def __init__(self, transport_=None):
            super().__init__(transport=httpx.MockTransport(timeout_handler))

    monkeypatch.setattr(runner, "RunnerClient", TimeoutClient)
    assert await runner.reachable() is False

    runner.reset_reachability_cache()

    class NonJsonClient(runner.RunnerClient):
        def __init__(self, transport_=None):
            super().__init__(
                transport=httpx.MockTransport(
                    lambda req: httpx.Response(200, content=b"<html>ok</html>", headers={"content-type": "text/html"})
                )
            )

    monkeypatch.setattr(runner, "RunnerClient", NonJsonClient)
    assert await runner.reachable() is False


# --- Catalog, details, plan and apply behavior --------------------------------


@pytest.mark.asyncio
async def test_catalog_and_details_mark_code_plugins_unsupported_when_runner_unreachable(
    client: AsyncClient, auth_headers: dict, mixed_catalog: MixedCatalog, monkeypatch
):
    _make_toggleable_runner(monkeypatch, down=True)

    resp = await client.get("/api/plugins/catalog", headers=auth_headers)
    assert resp.status_code == 200
    by_id = {entry["id"]: entry for entry in resp.json()}
    assert by_id[CODE_PLUGIN]["supported"] is False
    assert by_id[REPORT_PLUGIN]["supported"] is False
    assert by_id[DATA_PLUGIN]["supported"] is True

    code_detail = await client.get(f"/api/plugins/{CODE_PLUGIN}", headers=auth_headers)
    assert code_detail.status_code == 200
    assert code_detail.json()["plugin"]["supported"] is False
    assert code_detail.json()["contents"] is None

    data_detail = await client.get(f"/api/plugins/{DATA_PLUGIN}", headers=auth_headers)
    assert data_detail.status_code == 200
    assert data_detail.json()["plugin"]["supported"] is True
    assert data_detail.json()["contents"] is not None


@pytest.mark.asyncio
async def test_plan_and_apply_return_503_when_runner_unreachable(
    client: AsyncClient, auth_headers: dict, mixed_catalog: MixedCatalog, monkeypatch
):
    _make_toggleable_runner(monkeypatch, down=True)

    plan_resp = await client.get(
        f"/api/plugins/{CODE_PLUGIN}/plan", params={"action": "install"}, headers=auth_headers
    )
    assert plan_resp.status_code == 503
    assert "runner" in plan_resp.json()["detail"].lower()

    apply_resp = await client.post(
        f"/api/plugins/{CODE_PLUGIN}/apply",
        json={"action": "install", "commit": V1, "token": "0" * 64},
        headers=auth_headers,
    )
    assert apply_resp.status_code == 503

    # Data plugins stay installable while the runner is down.
    data_plan = await client.get(
        f"/api/plugins/{DATA_PLUGIN}/plan", params={"action": "install"}, headers=auth_headers
    )
    assert data_plan.status_code == 200


@pytest.mark.asyncio
async def test_apply_returns_503_and_resets_cache_when_runner_drops_after_plan(
    client: AsyncClient, auth_headers: dict, mixed_catalog: MixedCatalog, monkeypatch
):
    state = _make_toggleable_runner(monkeypatch, down=False)

    # Plan succeeds while runner is healthy; reachability cache now holds True.
    plan_resp = await client.get(
        f"/api/plugins/{CODE_PLUGIN}/plan", params={"action": "install"}, headers=auth_headers
    )
    assert plan_resp.status_code == 200
    plan = plan_resp.json()
    assert state["health_calls"] == 1

    # Runner goes down before apply while cache still says True.
    state["down"] = True
    apply_resp = await client.post(
        f"/api/plugins/{CODE_PLUGIN}/apply",
        json={"action": "install", "commit": plan["commit"], "token": plan["token"]},
        headers=auth_headers,
    )
    assert apply_resp.status_code == 503

    # The failed install cleared the reachability cache, so catalog immediately re-probes and sees False.
    catalog_resp = await client.get("/api/plugins/catalog", headers=auth_headers)
    assert catalog_resp.status_code == 200
    by_id = {entry["id"]: entry for entry in catalog_resp.json()}
    assert by_id[CODE_PLUGIN]["supported"] is False
    assert state["health_calls"] == 2


@pytest.mark.asyncio
async def test_409_preserved_for_unconfigured_stale_plan_revoked_and_incompatible(
    client: AsyncClient, auth_headers: dict, mixed_catalog: MixedCatalog, monkeypatch
):
    # 1. Runner not configured -> 409 (not 503).
    _configure_settings(monkeypatch, url="", token="")
    unconfigured = await client.get(
        f"/api/plugins/{CODE_PLUGIN}/plan", params={"action": "install"}, headers=auth_headers
    )
    assert unconfigured.status_code == 409

    # 2. Stale plan token with healthy runner -> 409 (not 503).
    state = _make_toggleable_runner(monkeypatch, down=False)
    plan = (
        await client.get(
            f"/api/plugins/{CODE_PLUGIN}/plan", params={"action": "install"}, headers=auth_headers
        )
    ).json()
    stale = await client.post(
        f"/api/plugins/{CODE_PLUGIN}/apply",
        json={"action": "install", "commit": plan["commit"], "token": "0" * 64},
        headers=auth_headers,
    )
    assert stale.status_code == 409

    # 3. Revoked code plugin -> 409 even if runner is down.
    state["down"] = True
    runner.reset_reachability_cache()
    mixed_catalog.code_extra = {"revoked": True, "revoked_reason": "security issue"}
    revoked = await client.get(
        f"/api/plugins/{CODE_PLUGIN}/plan", params={"action": "install"}, headers=auth_headers
    )
    assert revoked.status_code == 409

    # 4. Incompatible Securo version -> 409 even if runner is down.
    mixed_catalog.code_extra = {"min_securo_version": "99.0.0"}
    incompatible = await client.get(
        f"/api/plugins/{CODE_PLUGIN}/plan", params={"action": "install"}, headers=auth_headers
    )
    assert incompatible.status_code == 409


@pytest.mark.asyncio
async def test_healthy_runner_allows_catalog_details_plan_and_apply(
    client: AsyncClient, auth_headers: dict, mixed_catalog: MixedCatalog, monkeypatch
):
    state = _make_toggleable_runner(monkeypatch, down=False)

    catalog_resp = await client.get("/api/plugins/catalog", headers=auth_headers)
    assert catalog_resp.status_code == 200
    by_id = {entry["id"]: entry for entry in catalog_resp.json()}
    assert by_id[CODE_PLUGIN]["supported"] is True
    assert by_id[REPORT_PLUGIN]["supported"] is True
    assert by_id[DATA_PLUGIN]["supported"] is True

    detail_resp = await client.get(f"/api/plugins/{CODE_PLUGIN}", headers=auth_headers)
    assert detail_resp.status_code == 200
    assert detail_resp.json()["plugin"]["supported"] is True
    assert detail_resp.json()["contents"] is not None

    plan_resp = await client.get(
        f"/api/plugins/{CODE_PLUGIN}/plan", params={"action": "install"}, headers=auth_headers
    )
    assert plan_resp.status_code == 200
    plan = plan_resp.json()

    apply_resp = await client.post(
        f"/api/plugins/{CODE_PLUGIN}/apply",
        json={"action": "install", "commit": plan["commit"], "token": plan["token"]},
        headers=auth_headers,
    )
    assert apply_resp.status_code == 200
    assert len(state["installed"]) == 1


@pytest.mark.asyncio
async def test_startup_check_logs_warning_when_runner_unreachable(monkeypatch, caplog):
    state = _make_toggleable_runner(monkeypatch, down=True)

    with caplog.at_level(logging.WARNING, logger="app.main"):
        await _check_plugin_runner()
    assert any("plugin runner" in record.getMessage().lower() for record in caplog.records)

    caplog.clear()
    state["down"] = False
    with caplog.at_level(logging.WARNING, logger="app.main"):
        await _check_plugin_runner()
    assert caplog.records == []


@pytest.mark.asyncio
async def test_apply_422_refusal_returns_409_without_resetting_cache_and_5xx_resets(
    client: AsyncClient, auth_headers: dict, mixed_catalog: MixedCatalog, monkeypatch
):
    state = _make_toggleable_runner(monkeypatch, down=False)

    plan_resp = await client.get(
        f"/api/plugins/{CODE_PLUGIN}/plan", params={"action": "install"}, headers=auth_headers
    )
    assert plan_resp.status_code == 200
    plan = plan_resp.json()
    assert state["health_calls"] == 1

    # 4xx RunnerRefused (e.g. 422 lockfile/archive error) is a package refusal, not a dead runner.
    state["install_status"] = 422
    refused_422 = await client.post(
        f"/api/plugins/{CODE_PLUGIN}/apply",
        json={"action": "install", "commit": plan["commit"], "token": plan["token"]},
        headers=auth_headers,
    )
    assert refused_422.status_code == 409
    assert "install refused" in refused_422.json()["detail"].lower()

    # Reachability cache stayed intact, so catalog reuses it without probing /health again.
    catalog_resp = await client.get("/api/plugins/catalog", headers=auth_headers)
    assert catalog_resp.status_code == 200
    assert state["health_calls"] == 1

    # 5xx RunnerRefused (e.g. 502 from a proxy) IS treated as runner unavailability and resets cache.
    state["install_status"] = 502
    refused_502 = await client.post(
        f"/api/plugins/{CODE_PLUGIN}/apply",
        json={"action": "install", "commit": plan["commit"], "token": plan["token"]},
        headers=auth_headers,
    )
    assert refused_502.status_code == 503

    catalog_after_502 = await client.get("/api/plugins/catalog", headers=auth_headers)
    assert catalog_after_502.status_code == 200
    assert state["health_calls"] == 2


@pytest.mark.asyncio
async def test_remove_succeeds_and_update_returns_503_when_runner_unreachable(
    client: AsyncClient, auth_headers: dict, mixed_catalog: MixedCatalog, monkeypatch
):
    state = _make_toggleable_runner(monkeypatch, down=False)

    # Install v1 while the runner is healthy.
    install_plan = (
        await client.get(
            f"/api/plugins/{CODE_PLUGIN}/plan", params={"action": "install"}, headers=auth_headers
        )
    ).json()
    install_resp = await client.post(
        f"/api/plugins/{CODE_PLUGIN}/apply",
        json={"action": "install", "commit": install_plan["commit"], "token": install_plan["token"]},
        headers=auth_headers,
    )
    assert install_resp.status_code == 200

    # Publish v2 in the catalog, then take the runner down.
    v2 = "2" * 40
    mixed_catalog.code_manifest["version"] = "1.1.0"
    mixed_catalog.code_extra = {"commit": v2}
    state["down"] = True
    runner.reset_reachability_cache()

    # Updating a code plugin while the runner is down returns 503 on both plan and apply.
    update_plan = await client.get(
        f"/api/plugins/{CODE_PLUGIN}/plan", params={"action": "update"}, headers=auth_headers
    )
    assert update_plan.status_code == 503
    update_apply = await client.post(
        f"/api/plugins/{CODE_PLUGIN}/apply",
        json={"action": "update", "commit": v2, "token": "0" * 64},
        headers=auth_headers,
    )
    assert update_apply.status_code == 503

    # Removing an installed code plugin still succeeds when the runner is down.
    remove_plan_resp = await client.get(
        f"/api/plugins/{CODE_PLUGIN}/plan", params={"action": "remove"}, headers=auth_headers
    )
    assert remove_plan_resp.status_code == 200
    remove_plan = remove_plan_resp.json()
    remove_apply = await client.post(
        f"/api/plugins/{CODE_PLUGIN}/apply",
        json={"action": "remove", "commit": remove_plan["commit"], "token": remove_plan["token"]},
        headers=auth_headers,
    )
    assert remove_apply.status_code == 200

    installed_resp = await client.get("/api/plugins", headers=auth_headers)
    assert installed_resp.status_code == 200
    assert installed_resp.json() == []


@pytest.mark.asyncio
async def test_catalog_skips_runner_probe_when_no_entry_requires_runner(
    client: AsyncClient, auth_headers: dict, monkeypatch
):
    data_only = MixedCatalog()

    def data_only_handle(request: httpx.Request) -> httpx.Response:
        if str(request.url) == INDEX_URL:
            return httpx.Response(
                200,
                json={"plugins": [data_only._entry(DATA_PLUGIN, data_only.data_manifest, None)]},
            )
        return data_only.handle(request)

    monkeypatch.setattr(
        index, "http_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(data_only_handle))
    )
    monkeypatch.setenv("PLUGINS_ENABLED", "true")
    _configure_settings(monkeypatch)

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("runner.reachable() must not be called when no catalog entry requires_runner")

    monkeypatch.setattr(runner, "reachable", fail_if_called)

    resp = await client.get("/api/plugins/catalog", headers=auth_headers)
    assert resp.status_code == 200
    entries = resp.json()
    assert len(entries) == 1
    assert entries[0]["id"] == DATA_PLUGIN
    assert entries[0]["supported"] is True
