"""Installed plugins on the runner's volume, and calling them.

    <data dir>/registry.json                 root only: what is installed, as which user
    <data dir>/plugins/                      root, 0711
        <owner>__<name>/                     root, 0711: a plugin passes through, can't list or write
            commits/<commit>/                root, read-only to the plugin: its source and deps/
            home/                            the plugin's own (0700): the only place it may write
            .install/<random>/               root, 0711: where a commit is put together

Nothing the plugin can write is ever walked, chmod-ed or chown-ed by the
runner, which runs as root: the plugin owns only `home/`, created once and
checked with lstat before every call. Its code and wheels belong to root, so
a plugin can't rewrite them after the checksum and the owner's consent.

The backend hands over the archive it downloaded and checked against the
catalog; the runner checks the sha256 again, unpacks it without following
links or leaving the target, and installs the lockfile with pip: hashes
required, wheels only (no sdist, so nothing of the plugin's runs while
installing), no dependencies beyond the list, as a separate installer user,
through the egress proxy with a pass for the package index only.

Several commits of a plugin can be installed at once: each workspace runs the
commit its owner agreed to, and a commit is removed when no workspace uses it.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import os
import re
import shutil
import stat
import sys
import tarfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

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


def _mkdir_root(path: Path, mode: int = 0o711) -> None:
    """A folder owned by the runner, never by a plugin."""
    path.mkdir(parents=True, exist_ok=True)
    info = os.lstat(path)
    if not stat.S_ISDIR(info.st_mode) or (sandbox.isolated() and info.st_uid != 0):
        raise StoreError(f"{path.name} is not a folder of the runner")
    os.chmod(path, mode)


class Store:
    def __init__(self, settings: RunnerSettings):
        self.settings = settings
        self.root = settings.data_dir
        self.plugins_dir = self.root / "plugins"
        self.registry_path = self.root / "registry.json"
        self._calls = asyncio.Semaphore(settings.max_calls)
        self._locks: dict[str, asyncio.Lock] = {}
        self._call_locks: dict[str, asyncio.Lock] = {}
        self._registry_lock = asyncio.Lock()
        # pip runs as one installer user, and each run ends by killing that
        # user's processes: two at once would kill each other.
        self._pip_lock = asyncio.Lock()

    @property
    def installer_uid(self) -> int:
        """Runs pip; no plugin code ever runs as this user."""
        return self.settings.first_uid - 1

    # Registry ------------------------------------------------------------

    def _read_registry(self) -> dict[str, Any]:
        try:
            registry = json.loads(self.registry_path.read_text())
        except FileNotFoundError:
            registry = {}
        registry.setdefault("next_uid", self.settings.first_uid)
        registry.setdefault("plugins", {})
        return registry

    def _write_registry(self, registry: dict[str, Any]) -> None:
        _mkdir_root(self.root)
        tmp = self.registry_path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as out:
            out.write(json.dumps(registry, indent=2, sort_keys=True))
        os.replace(tmp, self.registry_path)

    def _entries(self, plugin_id: str, entry: dict[str, Any]) -> list[Installed]:
        uid = int(entry["uid"])
        if uid < self.settings.first_uid:
            raise StoreError(f"{plugin_id} has an invalid user in the registry")
        return [
            Installed(plugin_id, commit, c["entrypoint"], list(c["hosts"]), uid, float(c["installed_at"]))
            for commit, c in entry.get("commits", {}).items()
        ]

    def installed(self) -> list[Installed]:
        registry = self._read_registry()
        return [i for pid, entry in registry["plugins"].items() for i in self._entries(pid, entry)]

    def get(self, plugin_id: str, commit: str | None = None) -> Installed:
        entry = self._read_registry()["plugins"].get(plugin_id)
        found = self._entries(plugin_id, entry) if entry else []
        if commit is not None:
            found = [i for i in found if i.commit == commit]
        if not found:
            at = f" at {commit[:12]}" if commit else ""
            raise NotInstalled(f"{plugin_id} is not installed{at}")
        return max(found, key=lambda i: i.installed_at)

    def _lock(self, plugin_id: str) -> asyncio.Lock:
        return self._locks.setdefault(plugin_id, asyncio.Lock())

    # Folders --------------------------------------------------------------

    def _base(self, plugin_id: str) -> Path:
        _mkdir_root(self.root)
        _mkdir_root(self.plugins_dir)
        base = self.plugins_dir / _slug(plugin_id)
        _mkdir_root(base)
        _mkdir_root(base / "commits")
        return base

    def _home(self, base: Path, uid: int) -> Path:
        """The plugin's only writable folder. Created once; refused if it isn't a plain folder of its own."""
        home = base / "home"
        if not os.path.lexists(home):
            # The parent is root's, so nothing can swap `home` between these calls.
            os.mkdir(home, 0o700)
            if sandbox.isolated():
                os.chown(home, uid, uid, follow_symlinks=False)
        info = os.lstat(home)
        if not stat.S_ISDIR(info.st_mode) or (sandbox.isolated() and info.st_uid != uid):
            raise StoreError("the plugin's home folder was tampered with")
        return home

    # Install and remove ---------------------------------------------------

    async def install(self, request: InstallRequest, archive: bytes) -> Installed:
        hosts = _validate(request)
        if len(archive) > self.settings.archive_limit_mb * 1024 * 1024:
            raise StoreError("the archive is too large")
        if hashlib.sha256(archive).hexdigest() != request.sha256.lower():
            raise StoreError("the archive doesn't match its sha256")

        async with self._lock(request.plugin_id):
            uid = await self._uid(request.plugin_id)
            base = self._base(request.plugin_id)
            _mkdir_root(base / ".install")
            staging = base / ".install" / uuid.uuid4().hex
            try:
                _mkdir_root(staging, 0o755)
                await asyncio.to_thread(unpack, archive, staging)
                if request.lockfile:
                    lockfile = _inside(staging, request.lockfile)
                    if not lockfile.is_file() or lockfile.is_symlink():
                        raise StoreError(f"the lockfile {request.lockfile} is not in the archive")
                    _check_lockfile(lockfile)
                    if _has_requirements(lockfile):
                        await self._pip(lockfile, staging, request.plugin_id)
                await asyncio.to_thread(_seal, staging)
                target = base / "commits" / request.commit
                # Swapped while no call of this plugin runs from the folder.
                async with self._call_lock(request.plugin_id):
                    if os.path.lexists(target):
                        await asyncio.to_thread(shutil.rmtree, target)
                    staging.rename(target)
            finally:
                if os.path.lexists(staging):
                    await asyncio.to_thread(shutil.rmtree, staging, True)
            self._home(base, uid)

            installed = Installed(request.plugin_id, request.commit, request.entrypoint, hosts, uid, time.time())
            async with self._registry_lock:
                registry = self._read_registry()
                entry = registry["plugins"].setdefault(request.plugin_id, {"uid": uid, "commits": {}})
                entry["uid"] = uid
                entry.setdefault("commits", {})[request.commit] = {
                    "entrypoint": request.entrypoint,
                    "hosts": hosts,
                    "installed_at": installed.installed_at,
                }
                self._write_registry(registry)
            logger.info("installed %s at %s", request.plugin_id, request.commit)
            return installed

    async def remove(self, plugin_id: str, commit: str | None = None) -> None:
        """One commit, or with none given the plugin and its home."""
        async with self._lock(plugin_id), self._call_lock(plugin_id):
            async with self._registry_lock:
                registry = self._read_registry()
                entry = registry["plugins"].get(plugin_id)
                commits = entry.get("commits", {}) if entry else {}
                if not entry or (commit is not None and commit not in commits):
                    raise NotInstalled(f"{plugin_id} is not installed")
                if commit is not None:
                    commits.pop(commit)
                gone = commit is None or not commits
                if gone:
                    registry["plugins"].pop(plugin_id)
                self._write_registry(registry)
            base = self.plugins_dir / _slug(plugin_id)
            # rmtree unlinks symlinks instead of following them, so `home` is safe to drop.
            target = base if gone else base / "commits" / str(commit)
            await asyncio.to_thread(shutil.rmtree, target, True)
            logger.info("removed %s%s", plugin_id, "" if gone else f" at {commit}")

    async def _uid(self, plugin_id: str) -> int:
        """The plugin's own user, kept for good: a reinstall gets the same one."""
        async with self._registry_lock:
            registry = self._read_registry()
            uids = registry.setdefault("uids", {})
            if plugin_id not in uids:
                uids[plugin_id] = max(int(registry["next_uid"]), self.settings.first_uid)
                registry["next_uid"] = uids[plugin_id] + 1
                self._write_registry(registry)
            return int(uids[plugin_id])

    async def _pip(self, lockfile: Path, staging: Path, plugin_id: str) -> None:
        """Wheels into `staging/deps`, written by the installer user, then handed to root."""
        work = staging / ".pip"
        deps = staging / "deps"
        for folder in (work, deps):
            os.mkdir(folder, 0o700)
            if sandbox.isolated():
                os.chown(folder, self.installer_uid, self.installer_uid, follow_symlinks=False)
        argv = [
            sys.executable, "-I", "-m", "pip", "install",
            "--isolated", "--no-input", "--disable-pip-version-check", "--no-cache-dir",
            "--require-hashes", "--only-binary=:all:", "--no-deps",
            "--index-url", self.settings.package_index,
            "--target", str(deps),
            "-r", str(lockfile),
        ]
        env = self._env(work, plugin_id, list(self.settings.package_hosts), self.settings.install_timeout)
        async with self._pip_lock:
            result = await self._run_pip(argv, work, env)
        shutil.rmtree(work, True)
        if result.returncode != 0:
            tail = result.stderr.decode(errors="replace").strip().splitlines()[-5:]
            logger.warning("pip failed for %s: %s", plugin_id, "\n".join(tail))
            raise StoreError("installing the plugin's dependencies failed: " + (tail[-1] if tail else "pip error"))

    async def _run_pip(self, argv: list[str], work: Path, env: dict[str, str]) -> sandbox.Result:
        try:
            return await sandbox.run(
                argv,
                cwd=str(work),
                env=env,
                limits=sandbox.Limits(timeout=self.settings.install_timeout, memory_mb=max(self.settings.memory_mb, 1024)),
                uid=self.installer_uid,
            )
        finally:
            sandbox.kill_user(self.installer_uid)

    # Calls ------------------------------------------------------------------

    def _call_lock(self, plugin_id: str) -> asyncio.Lock:
        return self._call_locks.setdefault(plugin_id, asyncio.Lock())

    async def call(
        self,
        plugin_id: str,
        method: str,
        params: dict[str, Any],
        *,
        secrets: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
        securo: dict[str, Any] | None = None,
        commit: str | None = None,
        timeout: int | None = None,
    ) -> Any:
        plugin = self.get(plugin_id, commit)
        timeout = min(timeout or self.settings.call_timeout, self.settings.call_timeout)
        securo = securo or {}
        request = json.dumps(
            {"method": method, "params": params, "secrets": secrets or {}, "config": config or {}, "securo": securo}
        ).encode()
        # The deadline counts from now, waiting included: the caller gives up
        # after `timeout`, so a call that waited too long must not start late.
        deadline = time.monotonic() + timeout
        try:
            await asyncio.wait_for(self._admit(plugin_id), timeout)
        except TimeoutError as exc:
            raise PluginFailed("Timeout", f"waited {timeout}s for the plugin to be free") from exc
        try:
            remaining = max(int(deadline - time.monotonic()), 1)
            return await self._run_call(plugin_id, plugin, method, request, securo, remaining)
        finally:
            self._call_lock(plugin_id).release()
            self._calls.release()

    async def _admit(self, plugin_id: str) -> None:
        """A slot, then the plugin: one call of a plugin at a time."""
        await self._calls.acquire()
        try:
            await self._call_lock(plugin_id).acquire()
        except BaseException:
            self._calls.release()
            raise

    async def _run_call(
        self, plugin_id: str, plugin: Installed, method: str, request: bytes, securo: dict[str, Any], timeout: int
    ) -> Any:
        # Afterwards every process of its user is killed, so nothing it started outlives the call.
        base = self._base(plugin_id)
        home = self._home(base, plugin.uid)
        try:
            result = await sandbox.run(
                [sys.executable, "-I", "-S", HOST_SCRIPT, str(base / "commits" / plugin.commit), plugin.entrypoint],
                cwd=str(home),
                env=self._env(home, plugin_id, plugin.hosts, timeout, api_url=securo.get("api_url")),
                limits=sandbox.Limits(timeout=timeout, memory_mb=self.settings.memory_mb),
                uid=plugin.uid,
                stdin=request,
            )
        except sandbox.CallTimeout as exc:
            raise PluginFailed("Timeout", str(exc)) from exc
        except sandbox.SandboxError as exc:
            raise PluginFailed("SandboxError", str(exc)) from exc
        finally:
            sandbox.kill_user(plugin.uid)
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

    def _env(
        self, home: Path, plugin_id: str, hosts: list[str], ttl: int, *, api_url: str | None = None
    ) -> dict[str, str]:
        """No inherited environment: only a path, a home and, with hosts, the proxy.

        Securo's own API is reached directly over the internal network, not
        through the proxy (which refuses private addresses).
        """
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
            api_host = urlsplit(api_url).hostname if api_url else None
            if api_host:
                env |= {"NO_PROXY": api_host, "no_proxy": api_host}
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


def _check_lockfile(lockfile: Path) -> None:
    """Pinned packages and their hashes only.

    pip reads options from a requirements file too: `--no-binary` would
    re-enable source builds (code running at install), `-r`/`-c`/`-e` or an
    index option would pull from elsewhere. Only `--hash` is allowed, and no
    direct URL or path references.
    """
    for number, raw in enumerate(lockfile.read_text(errors="replace").splitlines(), 1):
        line = raw.split("#", 1)[0].strip().rstrip("\\").strip()
        if not line:
            continue
        for token in line.split():
            if token.startswith("-") and not token.startswith("--hash="):
                raise StoreError(f"the lockfile may only pin packages with hashes (line {number}: {token})")
        if "://" in line or " @ " in line or line.startswith((".", "/")):
            raise StoreError(f"the lockfile may only pin packages from the index (line {number})")


def _has_requirements(lockfile: Path) -> bool:
    """pip refuses a file with nothing to install; a plugin may need nothing."""
    for line in lockfile.read_text(errors="replace").splitlines():
        line = line.split("#", 1)[0].strip()
        if line and not line.startswith("-"):
            return True
    return False


def _inside(root: Path, relative: str) -> Path:
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts:
        raise StoreError(f"not a path inside the plugin: {relative}")
    return root.joinpath(*path.parts)


def _seal(folder: Path) -> None:
    """Hand a staged commit to root, read-only for everyone else.

    Only the installer user (pip) wrote in it, and no plugin process could
    reach it; links are dropped rather than followed all the same.
    """
    for dirpath, dirnames, filenames in os.walk(folder, followlinks=False):
        for name in list(dirnames) + filenames:
            path = os.path.join(dirpath, name)
            info = os.lstat(path)
            if stat.S_ISLNK(info.st_mode) or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                os.unlink(path)
                if name in dirnames:
                    dirnames.remove(name)
                continue
            if sandbox.isolated():
                os.lchown(path, 0, 0)
            executable = stat.S_ISDIR(info.st_mode) or info.st_mode & 0o111
            os.chmod(path, 0o755 if executable else 0o644)
        if sandbox.isolated():
            os.lchown(dirpath, 0, 0)
        os.chmod(dirpath, 0o755)


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
        members = []
        # Counted while reading, not after: a small gzip can hold millions of headers.
        while (member := tar.next()) is not None:
            members.append(member)
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
