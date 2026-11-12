"""Talking to the plugin runner, where plugins with code run.

The backend never imports or executes a plugin. It hands the runner the
archive it fetched and checked, asks it to call a method, or to remove the
plugin, over the internal network and with the shared runner token. Secrets a
call needs travel in its body, never in a URL.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from app.core.config import get_settings

#: The runner waits up to its own call timeout; this leaves room for it.
_CALL_MARGIN = 15.0
_INSTALL_TIMEOUT = 660.0


class RunnerUnavailable(Exception):
    """No runner is configured, or it can't be reached."""


class RunnerRefused(Exception):
    """The runner refused the request; the message says why."""


class PluginCallFailed(Exception):
    """The plugin ran and reported an error, crashed or timed out."""

    def __init__(self, kind: str, message: str):
        super().__init__(f"{kind}: {message}")
        self.kind = kind
        self.message = message


@dataclass(frozen=True)
class RunnerPlugin:
    id: str
    commit: str
    hosts: list[str]


def configured() -> bool:
    settings = get_settings()
    return bool(settings.plugins_runner_url and settings.plugins_runner_token)


def _path(plugin_id: str) -> str:
    owner, _, name = plugin_id.partition("/")
    return f"/plugins/{quote(owner, safe='')}/{quote(name, safe='')}"


class RunnerClient:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        settings = get_settings()
        if not configured():
            raise RunnerUnavailable("no plugin runner is configured")
        self._client = httpx.AsyncClient(
            base_url=settings.plugins_runner_url.rstrip("/"),
            headers={"Authorization": f"Bearer {settings.plugins_runner_token}"},
            timeout=30.0,
            follow_redirects=False,
            transport=transport,
        )

    async def __aenter__(self) -> RunnerClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.aclose()

    async def _send(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            response = await self._client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise RunnerUnavailable(f"the plugin runner can't be reached: {exc}") from exc
        if response.status_code == 401:
            raise RunnerUnavailable("the plugin runner refused the token")
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail")
            except ValueError:
                detail = None
            raise RunnerRefused(str(detail or f"runner error {response.status_code}"))
        return response

    async def health(self) -> dict[str, Any]:
        return (await self._send("GET", "/health")).json()

    async def installed(self) -> list[RunnerPlugin]:
        rows = (await self._send("GET", "/plugins")).json()
        return [RunnerPlugin(r["id"], r["commit"], list(r["hosts"])) for r in rows]

    async def install(
        self,
        plugin_id: str,
        *,
        commit: str,
        sha256: str,
        archive: bytes,
        entrypoint: str,
        lockfile: str | None,
        hosts: list[str],
    ) -> RunnerPlugin:
        metadata = {"commit": commit, "sha256": sha256, "entrypoint": entrypoint, "lockfile": lockfile, "hosts": hosts}
        response = await self._send(
            "PUT",
            _path(plugin_id),
            data={"metadata": json.dumps(metadata)},
            files={"archive": ("plugin.tar.gz", archive, "application/gzip")},
            timeout=_INSTALL_TIMEOUT,
        )
        row = response.json()
        return RunnerPlugin(row["id"], row["commit"], list(row["hosts"]))

    async def call(
        self,
        plugin_id: str,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        secrets: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
        timeout: int | None = None,
    ) -> Any:
        body = {"method": method, "params": params or {}, "secrets": secrets or {}, "config": config or {}}
        if timeout is not None:
            body["timeout"] = timeout
        response = await self._send(
            "POST",
            f"{_path(plugin_id)}/call",
            json=body,
            timeout=(timeout or 60) + _CALL_MARGIN,
        )
        reply = response.json()
        if not reply.get("ok"):
            error = reply.get("error") or {}
            raise PluginCallFailed(str(error.get("type", "Error")), str(error.get("message", "")))
        return reply.get("result")

    async def remove(self, plugin_id: str) -> None:
        await self._send("DELETE", _path(plugin_id))
