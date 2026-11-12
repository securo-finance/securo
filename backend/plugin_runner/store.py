"""Installed plugins on the runner's volume, and calling them.

    <data dir>/registry.json            what is installed, at which commit, as which user
    <data dir>/plugins/<owner>__<name>/
        <commit>/                      the plugin's source, as released
        <commit>/deps/                 the wheels its lockfile pins
        home/                          the only place the plugin may write

The backend hands over the archive it downloaded and checked against the
catalog; the runner checks the sha256 again, unpacks it without following
links or leaving the target, and installs the lockfile with pip: hashes
required, wheels only (no sdist, so nothing of the plugin's runs while
installing), no dependencies beyond the list, through the egress proxy with a
pass for the package index only. The new commit replaces the old one only
once all of that worked.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import io
import json
import logging
import os
import re
import shutil
import sys
import tarfile
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from plugin_runner import egress_token, sandbox
from plugin_runner.config import RunnerSettings

logger = logging.getLogger("plugin_runner")

PLUGIN_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,38}/[a-z0-9][a-z0-9-]{0,63}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
ENTRYPOINT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*:[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
HOST_SCRIPT = str(Path(__file__).with_name("host.py"))
UNPACKED_LIMIT = 200 * 1024 * 1024
MEMBER_LIMIT = 10000


class StoreError(Exception):
    """The request can't be carried out; the message is safe to show."""


class NotInstalled(StoreError):
    pass


class PluginFailed(Exception):
    """The plugin ran and reported an error (or crashed)."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message


@dataclass(frozen=True)
class Installed:
    plugin_id: str
    commit: str
    entrypoint: str
    hosts: list[str]
    uid: int
    installed_at: float


@dataclass(frozen=True)
class InstallRequest:
    plugin_id: str
    commit: str
    sha256: str
    entrypoint: str
    lockfile: str | None
    hosts: list[str]


def _slug(plugin_id: str) -> str:
    return plugin_id.replace("/", "__")


class Store:
    def __init__(self, settings: RunnerSettings):
        self.settings = settings
        self.root = settings.data_dir
        self.plugins_dir = self.root / "plugins"
        self.registry_path = self.root / "registry.json"
        self._calls = asyncio.Semaphore(settings.max_calls)
        self._locks: dict[str, asyncio.Lock] = {}
        self._registry_lock = asyncio.Lock()

    # Registry ------------------------------------------------------------

    def _read_registry(self) -> dict[str, Any]:
        try:
            return json.loads(self.registry_path.read_text())
        except FileNotFoundError:
            return {"next_uid": self.settings.first_uid, "plugins": {}}

    def _write_registry(self, registry: dict[str, Any]) -> None:
        self._prepare_root()
        tmp = self.registry_path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as out:
            out.write(json.dumps(registry, indent=2, sort_keys=True))
        os.replace(tmp, self.registry_path)

    def _prepare_root(self) -> None:
        """Plugins may pass through the data folders to reach their own, but not list or read them."""
        for folder in (self.root, self.plugins_dir):
            folder.mkdir(parents=True, exist_ok=True)
            os.chmod(folder, 0o711)

    def _plugin_dir(self, plugin_id: str, uid: int) -> Path:
        """The plugin's folder: its own, and closed to every other plugin."""
        self._prepare_root()
        base = self.plugins_dir / _slug(plugin_id)
        base.mkdir(exist_ok=True)
        os.chmod(base, 0o700)
        if sandbox.isolated():
            os.chown(base, uid, uid)
        return base

    def installed(self) -> list[Installed]:
        return [Installed(**entry) for entry in self._read_registry()["plugins"].values()]

    def get(self, plugin_id: str) -> Installed:
        entry = self._read_registry()["plugins"].get(plugin_id)
        if entry is None:
            raise NotInstalled(f"{plugin_id} is not installed")
        return Installed(**entry)

    def _lock(self, plugin_id: str) -> asyncio.Lock:
        return self._locks.setdefault(plugin_id, asyncio.Lock())

    # Install and remove ---------------------------------------------------

    async def install(self, request: InstallRequest, archive: bytes) -> Installed:
        hosts = _validate(request)
        if len(archive) > self.settings.archive_limit_mb * 1024 * 1024:
            raise StoreError("the archive is too large")
        if hashlib.sha256(archive).hexdigest() != request.sha256.lower():
            raise StoreError("the archive doesn't match its sha256")

        async with self._lock(request.plugin_id):
            uid = await self._uid(request.plugin_id)
            base = self._plugin_dir(request.plugin_id, uid)
            staging = base / f".staging-{uuid.uuid4().hex}"
            try:
                staging.mkdir(parents=True)
                await asyncio.to_thread(unpack, archive, staging)
                if request.lockfile:
                    lockfile = _inside(staging, request.lockfile)
                    if not lockfile.is_file():
                        raise StoreError(f"the lockfile {request.lockfile} is not in the archive")
                    home = self._home(base, uid)
                    _own(staging, uid)
                    await self._pip(lockfile, staging / "deps", home, uid, request.plugin_id)
                _own(staging, uid)
                target = base / request.commit
                if target.exists():
                    await asyncio.to_thread(shutil.rmtree, target)
                staging.rename(target)
            finally:
                if staging.exists():
                    await asyncio.to_thread(shutil.rmtree, staging, True)

            installed = Installed(
                request.plugin_id, request.commit, request.entrypoint, hosts, uid, time.time()
            )
            async with self._registry_lock:
                registry = self._read_registry()
                previous = registry["plugins"].get(request.plugin_id)
                registry["plugins"][request.plugin_id] = asdict(installed)
                self._write_registry(registry)
            if previous and previous["commit"] != request.commit:
                await asyncio.to_thread(shutil.rmtree, base / previous["commit"], True)
            logger.info("installed %s at %s", request.plugin_id, request.commit)
            return installed

    async def remove(self, plugin_id: str) -> None:
        async with self._lock(plugin_id):
            async with self._registry_lock:
                registry = self._read_registry()
                if registry["plugins"].pop(plugin_id, None) is None:
                    raise NotInstalled(f"{plugin_id} is not installed")
                self._write_registry(registry)
            await asyncio.to_thread(shutil.rmtree, self.plugins_dir / _slug(plugin_id), True)
            logger.info("removed %s", plugin_id)

    async def _uid(self, plugin_id: str) -> int:
        """The plugin's own user, kept for good: a reinstall gets the same one."""
        async with self._registry_lock:
            registry = self._read_registry()
            uids = registry.setdefault("uids", {})
            if plugin_id not in uids:
                uids[plugin_id] = registry["next_uid"]
                registry["next_uid"] += 1
                self._write_registry(registry)
            return uids[plugin_id]

    def _home(self, base: Path, uid: int) -> Path:
        home = base / "home"
        home.mkdir(exist_ok=True)
        os.chmod(home, 0o700)
        _own(home, uid)
        return home

    async def _pip(self, lockfile: Path, target: Path, home: Path, uid: int, plugin_id: str) -> None:
        argv = [
            sys.executable, "-I", "-m", "pip", "install",
            "--isolated", "--no-input", "--disable-pip-version-check", "--no-cache-dir",
            "--require-hashes", "--only-binary=:all:", "--no-deps",
            "--index-url", self.settings.package_index,
            "--target", str(target),
            "-r", str(lockfile),
        ]
        env = self._env(home, plugin_id, list(self.settings.package_hosts), self.settings.install_timeout)
        result = await sandbox.run(
            argv,
            cwd=str(lockfile.parent),
            env=env,
            limits=sandbox.Limits(timeout=self.settings.install_timeout, memory_mb=max(self.settings.memory_mb, 1024)),
            uid=uid,
        )
        if result.returncode != 0:
            tail = result.stderr.decode(errors="replace").strip().splitlines()[-5:]
            logger.warning("pip failed for %s: %s", plugin_id, "\n".join(tail))
            raise StoreError("installing the plugin's dependencies failed: " + (tail[-1] if tail else "pip error"))

    # Calls ------------------------------------------------------------------

    async def call(
        self,
        plugin_id: str,
        method: str,
        params: dict[str, Any],
        *,
        secrets: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
        timeout: int | None = None,
    ) -> Any:
        plugin = self.get(plugin_id)
        timeout = min(timeout or self.settings.call_timeout, self.settings.call_timeout)
        base = self._plugin_dir(plugin_id, plugin.uid)
        home = self._home(base, plugin.uid)
        request = json.dumps(
            {"method": method, "params": params, "secrets": secrets or {}, "config": config or {}}
        ).encode()
        async with self._calls:
            try:
                result = await sandbox.run(
                    [sys.executable, "-I", "-S", HOST_SCRIPT, str(base / plugin.commit), plugin.entrypoint],
                    cwd=str(home),
                    env=self._env(home, plugin_id, plugin.hosts, timeout),
                    limits=sandbox.Limits(timeout=timeout, memory_mb=self.settings.memory_mb),
                    uid=plugin.uid,
                    stdin=request,
                )
            except sandbox.CallTimeout as exc:
                raise PluginFailed("Timeout", str(exc)) from exc
            except sandbox.SandboxError as exc:
                raise PluginFailed("SandboxError", str(exc)) from exc
        if result.stderr:
            logger.info("%s.%s: %s", plugin_id, method, result.stderr.decode(errors="replace")[-2000:])
        try:
            reply = json.loads(result.stdout)
        except ValueError:
            raise PluginFailed("Crashed", f"the plugin exited with code {result.returncode}") from None
        if not reply.get("ok"):
            error = reply.get("error") or {}
            raise PluginFailed(str(error.get("type", "Error")), str(error.get("message", "")))
        return reply.get("result")

    def _env(self, home: Path, plugin_id: str, hosts: list[str], ttl: int) -> dict[str, str]:
        """No inherited environment: only a path, a home and, with hosts, the proxy."""
        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": str(home),
            "TMPDIR": str(home),
            "LANG": "C.UTF-8",
        }
        if hosts:
            token = egress_token.mint(self.settings.egress_secret, plugin_id, hosts, ttl + 30)
            proxy = f"http://plugin:{token}@{self.settings.egress_proxy}"
            env |= {"HTTPS_PROXY": proxy, "HTTP_PROXY": proxy, "https_proxy": proxy, "http_proxy": proxy}
        return env


def _validate(request: InstallRequest) -> list[str]:
    if not PLUGIN_ID.match(request.plugin_id):
        raise StoreError("not a plugin id")
    if not COMMIT.match(request.commit):
        raise StoreError("not a commit")
    if not ENTRYPOINT.match(request.entrypoint):
        raise StoreError("the entrypoint must look like package.module:attribute")
    if not re.match(r"^[0-9a-fA-F]{64}$", request.sha256):
        raise StoreError("not a sha256")
    try:
        return sorted({egress_token.normalize_host(h) for h in request.hosts})
    except ValueError as exc:
        raise StoreError(str(exc)) from exc


def _inside(root: Path, relative: str) -> Path:
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts:
        raise StoreError(f"not a path inside the plugin: {relative}")
    return root.joinpath(*path.parts)


def _own(path: Path, uid: int) -> None:
    if not sandbox.isolated():
        return
    for dirpath, dirnames, filenames in os.walk(path):
        os.chown(dirpath, uid, uid)
        for name in dirnames + filenames:
            with contextlib.suppress(FileNotFoundError):
                os.lchown(os.path.join(dirpath, name), uid, uid)


def unpack(archive: bytes, target: Path) -> None:
    """A release tarball into `target`, dropping its single top folder.

    Regular files and folders only: links, devices and anything that would
    land outside `target` are refused, as are archives that unpack too big.
    """
    try:
        tar = tarfile.open(fileobj=io.BytesIO(archive), mode="r:*")
    except tarfile.TarError as exc:
        raise StoreError("the archive is not a tarball") from exc
    with tar:
        members = tar.getmembers()
        if len(members) > MEMBER_LIMIT:
            raise StoreError("the archive has too many files")
        tops = {PurePosixPath(m.name).parts[0] for m in members if PurePosixPath(m.name).parts}
        strip = len(tops) == 1 and all(m.isdir() for m in members if m.name.rstrip("/") in tops)
        total = 0
        for member in members:
            parts = PurePosixPath(member.name).parts
            if strip:
                parts = parts[1:]
            if not parts:
                continue
            if member.name.startswith("/") or ".." in parts:
                raise StoreError(f"unsafe path in the archive: {member.name}")
            if not (member.isfile() or member.isdir()):
                raise StoreError(f"links and special files aren't allowed: {member.name}")
            destination = target.joinpath(*parts)
            if member.isdir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            total += member.size
            if total > UNPACKED_LIMIT:
                raise StoreError("the archive unpacks too large")
            destination.parent.mkdir(parents=True, exist_ok=True)
            source = tar.extractfile(member)
            if source is None:
                raise StoreError(f"can't read {member.name}")
            with source, open(destination, "wb") as out:
                shutil.copyfileobj(source, out)
            os.chmod(destination, 0o755 if member.mode & 0o111 else 0o644)
