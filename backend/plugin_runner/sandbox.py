"""Starting a plugin's process: its own user, limits, no secrets in the environment.

When the runner runs as root (in its container), each plugin gets a Unix user
of its own, so one plugin can't read another's files or the environment of
another's process. Outside a container (tests, a developer's laptop) the
process keeps the runner's user and `isolated()` is False.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import resource
import signal
import sys
from dataclasses import dataclass

OUTPUT_LIMIT = 16 * 1024 * 1024
LOG_LIMIT = 64 * 1024


class SandboxError(Exception):
    pass


class CallTimeout(SandboxError):
    pass


@dataclass(frozen=True)
class Limits:
    timeout: int
    memory_mb: int
    open_files: int = 256
    processes: int = 128
    """Per user: also stops a fork bomb from starving the container."""


@dataclass(frozen=True)
class Result:
    returncode: int
    stdout: bytes
    stderr: bytes


def isolated() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def _prepare(limits: Limits, uid: int | None):
    memory = limits.memory_mb * 1024 * 1024
    wanted = [
        (resource.RLIMIT_AS, (memory, memory)),
        (resource.RLIMIT_CPU, (limits.timeout, limits.timeout + 5)),
        (resource.RLIMIT_NOFILE, (limits.open_files, limits.open_files)),
        (resource.RLIMIT_CORE, (0, 0)),
    ]
    if uid is not None:
        # Counted per user, so only for a plugin's own user (never the runner's or a developer's).
        wanted.append((resource.RLIMIT_NPROC, (limits.processes, limits.processes)))

    def apply() -> None:  # pragma: no cover - runs in the child
        if uid is not None:
            # Done here rather than with `user=`, which uvloop doesn't take.
            os.setgroups([])
            os.setgid(uid)
            os.setuid(uid)
        for kind, value in wanted:
            try:
                resource.setrlimit(kind, value)
            except (ValueError, OSError):
                # macOS refuses some limits; the container (Linux) never does.
                if sys.platform == "linux":
                    raise
        os.umask(0o077)

    return apply


async def run(
    argv: list[str],
    *,
    cwd: str,
    env: dict[str, str],
    limits: Limits,
    uid: int | None,
    stdin: bytes = b"",
) -> Result:
    """Run `argv` to completion; kills the whole process group when done or on timeout."""
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        env=env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
        preexec_fn=_prepare(limits, uid if isolated() else None),
    )
    try:
        stdout, stderr, _ = await asyncio.wait_for(
            asyncio.gather(
                _read(process.stdout, OUTPUT_LIMIT, strict=True),
                _read(process.stderr, LOG_LIMIT, strict=False),
                _feed(process.stdin, stdin),
            ),
            limits.timeout,
        )
        await asyncio.wait_for(process.wait(), limits.timeout)
    except TimeoutError as exc:
        raise CallTimeout(f"timed out after {limits.timeout}s") from exc
    finally:
        # Also takes down whatever the plugin left behind in its group, and
        # anything of its user that left the group and could hold the pipes.
        _kill(process.pid)
        if uid is not None:
            kill_user(uid)
        if process.returncode is None:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(process.wait(), 5)
    return Result(process.returncode or 0, stdout, stderr)


async def _feed(pipe: asyncio.StreamWriter | None, data: bytes) -> None:
    if pipe is None:
        return
    with contextlib.suppress(ConnectionError):
        pipe.write(data)
        await pipe.drain()
    pipe.close()


async def _read(pipe: asyncio.StreamReader | None, limit: int, *, strict: bool) -> bytes:
    """Up to `limit` bytes; past it a reply is refused, and a log is cut short."""
    if pipe is None:
        return b""
    chunks: list[bytes] = []
    size = 0
    while chunk := await pipe.read(65536):
        size += len(chunk)
        if size > limit:
            if strict:
                raise SandboxError("the plugin's reply is too large")
            continue  # keep draining, so the plugin isn't stuck on a full pipe
        chunks.append(chunk)
    return b"".join(chunks)


def kill_user(uid: int) -> int:
    """Kill every process of a plugin's user, including any that left the call's group.

    Only inside the container (as root), and never for root itself.
    """
    if not isolated() or uid == 0 or not os.path.isdir("/proc"):
        return 0
    killed = 0
    for _ in range(3):  # a process may fork while the first pass runs
        found = 0
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            try:
                with open(f"/proc/{entry}/status") as status:
                    line = next(row for row in status if row.startswith("Uid:"))
                if int(line.split()[1]) == uid:
                    os.kill(int(entry), signal.SIGKILL)
                    found += 1
            except (OSError, StopIteration, ValueError):
                continue
        killed += found
        if not found:
            break
    return killed


def _kill(pid: int) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, signal.SIGKILL)
