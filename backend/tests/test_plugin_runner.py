"""The plugin runner: egress passes and proxy, unpacking, calls, its API and the backend's client."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import os
import stat
import tarfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from plugin_runner import egress_token, sandbox
from plugin_runner.config import RunnerSettings
from plugin_runner.egress import EgressProxy, is_public
from plugin_runner.main import create_app
from plugin_runner.store import InstallRequest, PluginFailed, Store, StoreError, unpack

SECRET = "egress-secret-for-tests"
TOKEN = "runner-token-for-tests"
COMMIT = "a" * 40

PLUGIN_SOURCE = '''
import asyncio, os, sys, time

class Plugin:
    def echo(self, params, context):
        print("noise on stdout")
        return {"params": params, "secret": context.secrets.get("api_token"), "config": dict(context.config)}

    async def later(self, params, context):
        await asyncio.sleep(0)
        return "async ok"

    def boom(self, params, context):
        raise ValueError("bad input")

    def slow(self, params, context):
        time.sleep(30)

    def environment(self, params, context):
        return {
            "proxy": os.environ.get("HTTPS_PROXY", ""),
            "secret_key": os.environ.get("SECRET_KEY"),
            "path": [p for p in sys.path if "site-packages" in p],
        }

    def leaves(self, params, context):
        os._exit(3)

plugin = Plugin()
'''


def tarball(files: dict[str, str | bytes], *, top: str | None = "owner-repo-abc") -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        if top:
            info = tarfile.TarInfo(top)
            info.type = tarfile.DIRTYPE
            tar.addfile(info)
        for name, content in files.items():
            data = content.encode() if isinstance(content, str) else content
            info = tarfile.TarInfo(f"{top}/{name}" if top else name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def settings(tmp_path: Path, **overrides) -> RunnerSettings:
    values: dict[str, Any] = dict(
        token=TOKEN,
        egress_secret=SECRET,
        egress_proxy="egress.test:3128",
        data_dir=tmp_path / "data",
        call_timeout=10,
        memory_mb=1024,
        max_calls=2,
    )
    values.update(overrides)
    return RunnerSettings(**values)


def request(archive: bytes, **overrides) -> InstallRequest:
    values: dict[str, Any] = dict(
        plugin_id="acme/echo",
        commit=COMMIT,
        sha256=hashlib.sha256(archive).hexdigest(),
        entrypoint="echo_plugin:plugin",
        lockfile=None,
        hosts=["api.example.com"],
    )
    values.update(overrides)
    return InstallRequest(**values)


# Egress passes -----------------------------------------------------------


def test_pass_round_trip_and_host_matching():
    token = egress_token.mint(SECRET, "acme/echo", ["API.example.com", "nfe.sefaz.test:8443"], ttl=60)
    granted = egress_token.verify(SECRET, token)
    assert granted.plugin == "acme/echo"
    assert granted.allows("api.example.com", 443)
    assert granted.allows("api.example.com.", 443)
    assert granted.allows("nfe.sefaz.test", 8443)
    assert not granted.allows("api.example.com", 80)
    assert not granted.allows("evil.example.com", 443)


def test_pass_refuses_tampering_wrong_secret_and_expiry():
    token = egress_token.mint(SECRET, "acme/echo", ["api.example.com"], ttl=60, now=1000)
    body, _, signature = token.partition(".")
    forged = base64.urlsafe_b64encode(
        json.dumps({"p": "acme/echo", "h": ["evil.com:443"], "e": 99999999999}).encode()
    ).rstrip(b"=").decode()
    with pytest.raises(egress_token.InvalidPass):
        egress_token.verify(SECRET, f"{forged}.{signature}")
    with pytest.raises(egress_token.InvalidPass):
        egress_token.verify("another-secret", token, now=1000)
    with pytest.raises(egress_token.InvalidPass):
        egress_token.verify(SECRET, token, now=1061)
    assert egress_token.verify(SECRET, token, now=1059).plugin == "acme/echo"
    with pytest.raises(egress_token.InvalidPass):
        egress_token.verify("", token)


@pytest.mark.parametrize("entry", ["*.example.com", "localhost", "10.0.0.1", "http://a.com", "a.com:0", "a.com:70000"])
def test_pass_refuses_anything_but_plain_hosts(entry):
    with pytest.raises(ValueError):
        egress_token.normalize_host(entry)


@pytest.mark.parametrize(
    ("address", "public"),
    [("8.8.8.8", True), ("10.0.0.5", False), ("172.18.0.3", False), ("127.0.0.1", False),
     ("169.254.169.254", False), ("::1", False), ("::ffff:10.0.0.1", False), ("2606:4700::1111", True)],
)
def test_private_addresses_are_not_public(address, public):
    assert is_public(address) is public


# Egress proxy ------------------------------------------------------------


async def _echo_server():
    async def echo(reader, writer):
        writer.write(await reader.read(100))
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(echo, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


async def _connect(proxy_port: int, target: str, token: str | None) -> tuple[bytes, asyncio.StreamReader, asyncio.StreamWriter]:
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
    auth = ""
    if token is not None:
        auth = "Proxy-Authorization: Basic " + base64.b64encode(f"plugin:{token}".encode()).decode() + "\r\n"
    writer.write(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n{auth}\r\n".encode())
    await writer.drain()
    status = await reader.readuntil(b"\r\n\r\n")
    return status, reader, writer


async def _proxy(resolved: list[str], *, allow_private: bool):
    async def resolver(host, port):
        return resolved

    proxy = EgressProxy(SECRET, resolver=resolver, allow_private=allow_private)
    server = await asyncio.start_server(proxy.handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


async def test_proxy_tunnels_to_a_declared_host():
    echo, echo_port = await _echo_server()
    proxy, proxy_port = await _proxy(["127.0.0.1"], allow_private=True)
    token = egress_token.mint(SECRET, "acme/echo", [f"api.example.com:{echo_port}"], ttl=60)
    async with echo, proxy:
        status, reader, writer = await _connect(proxy_port, f"api.example.com:{echo_port}", token)
        assert status.startswith(b"HTTP/1.1 200")
        writer.write(b"hello through the tunnel")
        await writer.drain()
        assert await reader.read(100) == b"hello through the tunnel"
        writer.close()


async def test_proxy_refuses_an_undeclared_host_before_resolving_or_connecting(monkeypatch):
    """A public host the plugin didn't declare: refused on the pass alone, nothing is opened."""
    looked_up, opened = [], []

    async def resolver(host, port):
        looked_up.append(host)
        return ["93.184.216.34"]

    async def never_open(*args, **kwargs):
        opened.append(args)
        raise AssertionError("no upstream connection may be attempted")

    real_open = asyncio.open_connection
    monkeypatch.setattr(asyncio, "open_connection", never_open)
    proxy = EgressProxy(SECRET, resolver=resolver, allow_private=False)
    server = await asyncio.start_server(proxy.handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    token = egress_token.mint(SECRET, "acme/echo", ["api.example.com"], ttl=60)
    async with server:
        reader, writer = await real_open("127.0.0.1", port)
        writer.write(f"CONNECT evil.example.com:443 HTTP/1.1\r\nProxy-Authorization: Basic {base64.b64encode(f'plugin:{token}'.encode()).decode()}\r\n\r\n".encode())
        await writer.drain()
        assert (await reader.readuntil(b"\r\n\r\n")).startswith(b"HTTP/1.1 403")
        writer.close()
    assert looked_up == [] and opened == []


async def test_proxy_refuses_missing_passes_and_private_addresses():
    token = egress_token.mint(SECRET, "acme/echo", ["api.example.com"], ttl=60)
    proxy, port = await _proxy(["10.1.2.3"], allow_private=False)
    async with proxy:
        status, _, writer = await _connect(port, "api.example.com:443", None)
        assert status.startswith(b"HTTP/1.1 407")
        writer.close()
        status, _, writer = await _connect(port, "api.example.com:443", "not-a-pass")
        assert status.startswith(b"HTTP/1.1 407")
        writer.close()
        # Declared, but the name points inside the network (e.g. at the database).
        status, _, writer = await _connect(port, "api.example.com:443", token)
        assert status.startswith(b"HTTP/1.1 403")
        writer.close()


async def test_proxy_refuses_plain_http_proxying():
    proxy, port = await _proxy(["8.8.8.8"], allow_private=False)
    async with proxy:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"GET http://api.example.com/ HTTP/1.1\r\nHost: api.example.com\r\n\r\n")
        await writer.drain()
        assert (await reader.readuntil(b"\r\n\r\n")).startswith(b"HTTP/1.1 405")
        writer.close()


def test_proxy_needs_a_secret():
    with pytest.raises(ValueError):
        EgressProxy("")


# Unpacking ---------------------------------------------------------------


def test_unpack_drops_the_top_folder(tmp_path):
    unpack(tarball({"echo_plugin.py": "x = 1", "pkg/mod.py": "y = 2"}), tmp_path)
    assert (tmp_path / "echo_plugin.py").read_text() == "x = 1"
    assert (tmp_path / "pkg" / "mod.py").read_text() == "y = 2"


def test_unpack_keeps_a_flat_archive(tmp_path):
    unpack(tarball({"a.py": "1", "b.py": "2"}, top=None), tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.py", "b.py"]


@pytest.mark.parametrize("kind", ["traversal", "absolute", "symlink", "hardlink", "not-a-tarball"])
def test_unpack_refuses_unsafe_archives(tmp_path, kind):
    if kind == "not-a-tarball":
        archive = b"plain bytes"
    else:
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
            info = tarfile.TarInfo({"traversal": "top/../../escape.py", "absolute": "/etc/evil.py"}.get(kind, "top/link"))
            if kind == "symlink":
                info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
            elif kind == "hardlink":
                info.type, info.linkname = tarfile.LNKTYPE, "/etc/passwd"
            tar.addfile(info, io.BytesIO(b""))
        archive = buffer.getvalue()
    with pytest.raises(StoreError):
        unpack(archive, tmp_path / "out")
    assert not (tmp_path / "escape.py").exists()


# Installing and calling ----------------------------------------------------


@pytest.fixture
def store(tmp_path):
    return Store(settings(tmp_path))


@pytest.fixture
async def installed(store):
    archive = tarball({"echo_plugin.py": PLUGIN_SOURCE})
    await store.install(request(archive), archive)
    return store


async def test_install_records_the_plugin(installed, tmp_path):
    plugin = installed.get("acme/echo")
    assert plugin.commit == COMMIT
    assert plugin.hosts == ["api.example.com:443"]
    assert plugin.uid == 20000
    assert (tmp_path / "data" / "plugins" / "acme__echo" / "commits" / COMMIT / "echo_plugin.py").is_file()


async def test_install_refuses_a_wrong_checksum_and_bad_metadata(store):
    archive = tarball({"echo_plugin.py": PLUGIN_SOURCE})
    with pytest.raises(StoreError, match="sha256"):
        await store.install(request(archive, sha256="0" * 64), archive)
    for bad in (
        {"plugin_id": "../etc"},
        {"commit": "main"},
        {"entrypoint": "os.system('x')"},
        {"hosts": ["*.example.com"]},
        {"lockfile": "../requirements.txt"},
        {"lockfile": "requirements.txt"},
    ):
        with pytest.raises(StoreError):
            await store.install(request(archive, **bad), archive)
    assert store.installed() == []


async def test_commits_live_side_by_side_under_one_user(installed, tmp_path):
    """Each workspace runs the commit its owner agreed to, so an update keeps the other one."""
    archive = tarball({"echo_plugin.py": PLUGIN_SOURCE + "\n# v2\n"})
    await installed.install(request(archive, commit="b" * 40, hosts=[]), archive)
    old, new = installed.get("acme/echo", COMMIT), installed.get("acme/echo", "b" * 40)
    assert (old.uid, new.uid) == (20000, 20000)
    assert (old.hosts, new.hosts) == (["api.example.com:443"], [])
    assert installed.get("acme/echo").commit == "b" * 40  # the latest, when no commit is named
    commits = tmp_path / "data" / "plugins" / "acme__echo" / "commits"
    assert sorted(p.name for p in commits.iterdir()) == [COMMIT, "b" * 40]
    assert await installed.call("acme/echo", "later", {}, commit=COMMIT) == "async ok"

    await installed.remove("acme/echo", COMMIT)
    assert [p.commit for p in installed.installed()] == ["b" * 40]
    assert not (commits / COMMIT).exists()
    with pytest.raises(StoreError):
        await installed.call("acme/echo", "later", {}, commit=COMMIT)


async def test_a_swapped_home_folder_is_refused(installed, tmp_path):
    """A plugin owns its home only; a link put in its place is never followed."""
    home = tmp_path / "data" / "plugins" / "acme__echo" / "home"
    home.rmdir()
    home.symlink_to(tmp_path / "data")
    with pytest.raises(StoreError, match="tampered"):
        await installed.call("acme/echo", "later", {})


async def test_a_registry_user_below_the_plugin_range_is_refused(installed, tmp_path):
    registry_path = tmp_path / "data" / "registry.json"
    registry = json.loads(registry_path.read_text())
    registry["plugins"]["acme/echo"]["uid"] = 0
    registry_path.write_text(json.dumps(registry))
    with pytest.raises(StoreError, match="invalid user"):
        await installed.call("acme/echo", "later", {})


async def test_runner_folders_are_closed_to_plugins(installed, tmp_path):
    base = tmp_path / "data" / "plugins" / "acme__echo"
    assert stat.S_IMODE((tmp_path / "data").stat().st_mode) == 0o711
    assert stat.S_IMODE(base.stat().st_mode) == 0o711
    assert stat.S_IMODE((base / "home").stat().st_mode) == 0o700
    assert stat.S_IMODE((tmp_path / "data" / "registry.json").stat().st_mode) == 0o600
    source = base / "commits" / COMMIT / "echo_plugin.py"
    assert stat.S_IMODE(source.stat().st_mode) == 0o644


async def test_call_passes_params_secrets_and_config(installed):
    result = await installed.call(
        "acme/echo", "echo", {"n": 1}, secrets={"api_token": "s3cret"}, config={"region": "eu"}
    )
    assert result == {"params": {"n": 1}, "secret": "s3cret", "config": {"region": "eu"}}
    assert await installed.call("acme/echo", "later", {}) == "async ok"


async def test_call_reports_plugin_errors(installed):
    with pytest.raises(PluginFailed) as raised:
        await installed.call("acme/echo", "boom", {})
    assert (raised.value.kind, raised.value.message) == ("ValueError", "bad input")
    with pytest.raises(PluginFailed, match="no method"):
        await installed.call("acme/echo", "missing", {})
    with pytest.raises(PluginFailed, match="not a plugin method"):
        await installed.call("acme/echo", "_private", {})
    with pytest.raises(PluginFailed) as crashed:
        await installed.call("acme/echo", "leaves", {})
    assert crashed.value.kind == "Crashed"


async def test_call_times_out_and_is_killed(installed):
    with pytest.raises(PluginFailed) as raised:
        await installed.call("acme/echo", "slow", {}, timeout=1)
    assert raised.value.kind == "Timeout"


async def test_call_environment_has_no_core_secrets_and_no_site_packages(installed, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "core-secret")
    result = await installed.call("acme/echo", "environment", {})
    assert result["secret_key"] is None
    assert result["path"] == []
    proxy = result["proxy"]
    assert proxy.startswith("http://plugin:") and proxy.endswith("@egress.test:3128")
    granted = egress_token.verify(SECRET, proxy.removeprefix("http://plugin:").split("@")[0])
    assert granted.plugin == "acme/echo" and granted.hosts == {"api.example.com:443"}


async def test_a_plugin_without_hosts_gets_no_proxy(store):
    archive = tarball({"echo_plugin.py": PLUGIN_SOURCE})
    await store.install(request(archive, hosts=[]), archive)
    assert (await store.call("acme/echo", "environment", {}))["proxy"] == ""


async def test_remove(installed, tmp_path):
    await installed.remove("acme/echo")
    assert installed.installed() == []
    assert not (tmp_path / "data" / "plugins" / "acme__echo").exists()
    with pytest.raises(StoreError):
        await installed.remove("acme/echo")


async def test_lockfile_is_installed_with_hashes_and_wheels_only(store, tmp_path):
    archive = tarball({"echo_plugin.py": PLUGIN_SOURCE, "requirements.txt": "six==1.17.0 --hash=sha256:" + "0" * 64})
    calls = []

    async def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        Path(argv[argv.index("--target") + 1]).mkdir(parents=True, exist_ok=True)
        return sandbox.Result(0, b"", b"")

    with patch("plugin_runner.store.sandbox.run", fake_run):
        await store.install(request(archive, lockfile="requirements.txt"), archive)
    argv, kwargs = calls[0]
    for flag in ("--require-hashes", "--only-binary=:all:", "--no-deps", "--isolated"):
        assert flag in argv
    granted = egress_token.verify(SECRET, kwargs["env"]["HTTPS_PROXY"].removeprefix("http://plugin:").split("@")[0])
    assert granted.hosts == {"pypi.org:443", "files.pythonhosted.org:443"}
    assert kwargs["uid"] == 19999  # the installer user, never the plugin's


async def test_a_lockfile_with_nothing_to_install_skips_pip(store):
    archive = tarball({"echo_plugin.py": PLUGIN_SOURCE, "requirements.txt": "# nothing needed\n\n"})

    async def never(argv, **kwargs):
        raise AssertionError("pip must not run")

    with patch("plugin_runner.store.sandbox.run", never):
        await store.install(request(archive, lockfile="requirements.txt"), archive)
    assert store.get("acme/echo").commit == COMMIT


async def test_a_failed_dependency_install_keeps_the_previous_version(installed):
    archive = tarball({"echo_plugin.py": "broken", "requirements.txt": "x==1"})

    async def failing(argv, **kwargs):
        return sandbox.Result(1, b"", b"ERROR: no matching distribution\n")

    with patch("plugin_runner.store.sandbox.run", failing):
        with pytest.raises(StoreError, match="no matching distribution"):
            await installed.install(request(archive, commit="c" * 40, lockfile="requirements.txt"), archive)
    assert installed.get("acme/echo").commit == COMMIT
    assert await installed.call("acme/echo", "later", {}) == "async ok"


# The runner's API ----------------------------------------------------------


def test_runner_refuses_to_start_without_secrets(tmp_path):
    with pytest.raises(RuntimeError):
        create_app(settings(tmp_path, token=""))
    with pytest.raises(RuntimeError):
        create_app(settings(tmp_path, egress_secret=""))


async def test_runner_api(tmp_path):
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(settings(tmp_path))), base_url="http://runner")
    auth = {"Authorization": f"Bearer {TOKEN}"}
    assert (await client.get("/health")).json()["status"] == "ok"
    assert (await client.get("/plugins")).status_code == 401
    assert (await client.get("/plugins", headers={"Authorization": "Bearer wrong"})).status_code == 401

    archive = tarball({"echo_plugin.py": PLUGIN_SOURCE})
    metadata = {"commit": COMMIT, "sha256": hashlib.sha256(archive).hexdigest(), "entrypoint": "echo_plugin:plugin", "hosts": []}
    response = await client.put(
        "/plugins/acme/echo", headers=auth,
        data={"metadata": json.dumps(metadata)}, files={"archive": ("p.tar.gz", archive)},
    )
    assert response.status_code == 200, response.text
    assert [p["id"] for p in (await client.get("/plugins", headers=auth)).json()] == ["acme/echo"]

    bad = await client.put(
        "/plugins/acme/echo", headers=auth,
        data={"metadata": json.dumps({**metadata, "sha256": "f" * 64})}, files={"archive": ("p.tar.gz", archive)},
    )
    assert bad.status_code == 422 and "sha256" in bad.json()["detail"]

    ok = await client.post("/plugins/acme/echo/call", headers=auth, json={"method": "echo", "params": {"a": 1}})
    assert ok.json()["ok"] is True and ok.json()["result"]["params"] == {"a": 1}
    failed = await client.post("/plugins/acme/echo/call", headers=auth, json={"method": "boom"})
    assert failed.json() == {"ok": False, "error": {"type": "ValueError", "message": "bad input"}}
    assert (await client.post("/plugins/acme/other/call", headers=auth, json={"method": "echo"})).status_code == 404

    assert (await client.delete("/plugins/acme/echo", headers=auth)).status_code == 204
    assert (await client.delete("/plugins/acme/echo", headers=auth)).status_code == 404


# The backend's client --------------------------------------------------------


def _settings(url="http://plugins-runner:8090", token=TOKEN):
    return SimpleNamespace(plugins_runner_url=url, plugins_runner_token=token)


def test_client_needs_a_configured_runner():
    from app.plugins import runner

    with patch("app.plugins.runner.get_settings", return_value=_settings(url="")):
        assert runner.configured() is False
        with pytest.raises(runner.RunnerUnavailable):
            runner.RunnerClient()


async def test_client_talks_to_the_runner(tmp_path):
    from app.plugins import runner

    transport = httpx.ASGITransport(app=create_app(settings(tmp_path)))
    archive = tarball({"echo_plugin.py": PLUGIN_SOURCE})
    with patch("app.plugins.runner.get_settings", return_value=_settings(url="http://runner")):
        async with runner.RunnerClient(transport=transport) as client:
            installed = await client.install(
                "acme/echo", commit=COMMIT, sha256=hashlib.sha256(archive).hexdigest(), archive=archive,
                entrypoint="echo_plugin:plugin", lockfile=None, hosts=["api.example.com"],
            )
            assert installed == runner.RunnerPlugin("acme/echo", COMMIT, ["api.example.com:443"])
            assert await client.installed() == [installed]
            assert (await client.call("acme/echo", "echo", {"x": 1}, secrets={"api_token": "t"}))["secret"] == "t"
            with pytest.raises(runner.PluginCallFailed) as failed:
                await client.call("acme/echo", "boom")
            assert failed.value.kind == "ValueError"
            with pytest.raises(runner.RunnerRefused):
                await client.call("acme/missing", "echo")
            await client.remove("acme/echo")
            assert await client.installed() == []

    with patch("app.plugins.runner.get_settings", return_value=_settings(url="http://runner", token="wrong")):
        async with runner.RunnerClient(transport=transport) as client:
            with pytest.raises(runner.RunnerUnavailable, match="token"):
                await client.installed()


async def test_client_reports_an_unreachable_runner():
    from app.plugins import runner

    def refuse(request):
        raise httpx.ConnectError("refused")

    with patch("app.plugins.runner.get_settings", return_value=_settings()):
        async with runner.RunnerClient(transport=httpx.MockTransport(refuse)) as client:
            with pytest.raises(runner.RunnerUnavailable):
                await client.health()


def test_host_script_is_standard_library_only():
    source = Path(__file__).parents[1].joinpath("plugin_runner", "host.py").read_text()
    imports = {line.split()[1].split(".")[0] for line in source.splitlines() if line.startswith(("import ", "from "))}
    assert imports <= {"__future__", "asyncio", "importlib", "inspect", "json", "os", "re", "sys", "types"}
    assert os.access(Path(__file__).parents[1] / "plugin_runner" / "host.py", os.R_OK)


@pytest.mark.parametrize(
    "line",
    ["--no-binary :all:", "-r other.txt", "-c constraints.txt", "-e .", "--index-url https://evil.example/simple",
     "pkg @ https://evil.example/pkg.whl --hash=sha256:" + "0" * 64, "./local.whl"],
)
async def test_a_lockfile_can_only_pin_hashed_packages(store, line):
    archive = tarball({"echo_plugin.py": PLUGIN_SOURCE, "requirements.txt": "six==1.17.0 --hash=sha256:" + "0" * 64 + "\n" + line + "\n"})

    async def never(argv, **kwargs):
        raise AssertionError("pip must not run")

    with patch("plugin_runner.store.sandbox.run", never):
        with pytest.raises(StoreError, match="lockfile"):
            await store.install(request(archive, lockfile="requirements.txt"), archive)


def test_a_lockfile_with_multiline_hashes_is_accepted(tmp_path):
    from plugin_runner.store import _check_lockfile

    lockfile = tmp_path / "requirements.txt"
    lockfile.write_text("# via uv export\nsix==1.17.0 \\\n    --hash=sha256:" + "a" * 64 + " \\\n    --hash=sha256:" + "b" * 64 + "\n")
    _check_lockfile(lockfile)


def test_unpack_stops_counting_members_early(tmp_path, monkeypatch):
    import plugin_runner.store as store_module

    monkeypatch.setattr(store_module, "MEMBER_LIMIT", 5)
    archive = tarball({f"f{i}.py": "x" for i in range(20)}, top=None)
    with pytest.raises(StoreError, match="too many files"):
        unpack(archive, tmp_path / "out")
