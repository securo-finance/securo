"""Runs one call of a plugin. Executed in the plugin's subprocess, not imported.

    python -I -S host.py <plugin root> <entrypoint>

`-I -S` leave only the standard library on the import path: not the runner's
packages, not the current directory, no environment variables for Python. This
file then adds the plugin's own wheels and source, imports the entrypoint
(`package.module:attribute`) and reads one request as JSON on stdin:

    {"method": "...", "params": {...}, "secrets": {...}, "config": {...}}

It calls `plugin.<method>(params, context)`, sync or async, and writes one JSON
reply to the original stdout: `{"ok": true, "result": ...}` or
`{"ok": false, "error": {"type": ..., "message": ...}}`. Anything the plugin
prints goes to stderr, so it can't corrupt the reply.

Secrets come over stdin rather than the environment or the command line, which
other processes on the machine could read. Standard library only.
"""
from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import os
import re
import sys
from types import MappingProxyType

METHOD = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
MESSAGE_LIMIT = 2000


class Context:
    """What a call gets besides its parameters. Read-only."""

    def __init__(self, secrets: dict, config: dict):
        self.secrets = MappingProxyType(dict(secrets))
        self.config = MappingProxyType(dict(config))


def _load(root: str, entrypoint: str):
    for path in (os.path.join(root, "src"), root, os.path.join(root, "deps")):
        if os.path.isdir(path):
            sys.path.insert(0, path)
    module_name, _, attribute = entrypoint.partition(":")
    target = importlib.import_module(module_name)
    for part in filter(None, attribute.split(".")):
        target = getattr(target, part)
    return target


def _run(root: str, entrypoint: str, request: dict):
    method = request.get("method")
    if not isinstance(method, str) or not METHOD.match(method):
        raise LookupError(f"not a plugin method: {method!r}")
    plugin = _load(root, entrypoint)
    handler = getattr(plugin, method, None)
    if not callable(handler):
        raise LookupError(f"the plugin has no method {method!r}")
    result = handler(request.get("params") or {}, Context(request.get("secrets") or {}, request.get("config") or {}))
    if inspect.isawaitable(result):
        result = asyncio.run(_await(result))
    return result


async def _await(awaitable):
    return await awaitable


def main(argv: list[str]) -> int:
    root, entrypoint = argv[1], argv[2]
    # Keep the real stdout for the reply and point everything else at stderr.
    reply = os.fdopen(os.dup(1), "w", encoding="utf-8")
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    try:
        request = json.loads(sys.stdin.read())
        payload = {"ok": True, "result": _run(root, entrypoint, request)}
        text = json.dumps(payload, default=str)
    except BaseException as exc:  # noqa: BLE001 - every failure is reported, not raised
        text = json.dumps(
            {"ok": False, "error": {"type": type(exc).__name__, "message": str(exc)[:MESSAGE_LIMIT]}}
        )
    reply.write(text)
    reply.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
