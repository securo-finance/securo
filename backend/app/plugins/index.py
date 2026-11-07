"""The plugin catalog: a curated index of plugins, each pinned to a commit.

The index is one JSON file listing, per plugin, its id, its GitHub
repository, the commit Securo installs, and whether it was revoked. A
plugin's manifest is read from its repository at that commit. Nothing is
fetched in the background: only when someone opens the catalog or acts on a
plugin. Requests go only to the configured index and raw-content hosts,
never follow redirects, and are capped in size.
"""
from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.config import get_settings
from app.plugins.manifest import ID_PATTERN, REPOSITORY_PATTERN, Manifest, parse_manifest, version_tuple

MANIFEST_FILE = "securo-plugin.json"
_TIMEOUT = 10.0
_MAX_INDEX_BYTES = 1 << 20
_MAX_MANIFEST_BYTES = 2 << 20
_MANIFEST_CACHE_SIZE = 256


class PluginIndexError(Exception):
    """The catalog or a manifest couldn't be fetched or isn't valid."""


class _Entry(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    id: str = Field(pattern=ID_PATTERN)
    repository: str = Field(pattern=REPOSITORY_PATTERN)
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    revoked: bool = False
    revoked_reason: str | None = Field(default=None, max_length=500)


class _Index(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    plugins: list[_Entry]


@dataclass(frozen=True)
class IndexEntry:
    id: str
    repository: str
    commit: str
    revoked: bool = False
    revoked_reason: str | None = None


def http_client() -> httpx.AsyncClient:
    """The client every catalog request goes through (tests swap it)."""
    return httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False)


async def _get_json(client: httpx.AsyncClient, url: str, limit: int) -> object:
    try:
        async with client.stream("GET", url, headers={"Accept": "application/json"}) as response:
            if response.status_code != 200:
                raise PluginIndexError(f"{url} answered {response.status_code}")
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body += chunk
                if len(body) > limit:
                    raise PluginIndexError(f"{url} is larger than {limit} bytes")
    except httpx.HTTPError as exc:
        raise PluginIndexError(f"{url} couldn't be fetched: {exc.__class__.__name__}") from exc
    try:
        return json.loads(body)
    except ValueError as exc:
        raise PluginIndexError(f"{url} isn't JSON") from exc


async def fetch_index(client: httpx.AsyncClient) -> list[IndexEntry]:
    data = await _get_json(client, get_settings().plugins_index_url, _MAX_INDEX_BYTES)
    try:
        index = _Index.model_validate(data)
    except ValidationError as exc:
        raise PluginIndexError("the plugin index isn't valid") from exc
    ids = [entry.id for entry in index.plugins]
    if len(ids) != len(set(ids)):
        raise PluginIndexError("the plugin index lists a plugin twice")
    return [IndexEntry(**entry.model_dump()) for entry in index.plugins]


_manifests: dict[tuple[str, str], Manifest] = {}


async def fetch_manifest(client: httpx.AsyncClient, entry: IndexEntry) -> Manifest:
    """The manifest at the pinned commit; it must be the plugin the index names."""
    cache_key = (entry.repository, entry.commit)
    if cache_key in _manifests:  # a commit's content never changes
        return _manifests[cache_key]
    match = re.match(r"^https://github\.com/([^/]+)/([^/]+)$", entry.repository)
    if match is None:
        raise PluginIndexError(f"{entry.id}: unsupported repository")
    owner, repo = match.groups()
    base = get_settings().plugins_raw_base_url.rstrip("/")
    data = await _get_json(client, f"{base}/{owner}/{repo}/{entry.commit}/{MANIFEST_FILE}", _MAX_MANIFEST_BYTES)
    try:
        manifest = parse_manifest(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first["loc"])
        reason = str(first["msg"]).removeprefix("Value error, ")
        raise PluginIndexError(f"{entry.id}: invalid manifest ({f'{where}: ' if where else ''}{reason})") from exc
    if manifest.id != entry.id or manifest.repository != entry.repository:
        raise PluginIndexError(f"{entry.id}: the manifest names another plugin")
    if len(_manifests) >= _MANIFEST_CACHE_SIZE:
        _manifests.pop(next(iter(_manifests)))
    _manifests[cache_key] = manifest
    return manifest


def securo_version() -> str | None:
    """The running backend's version.

    pyproject.toml ships with the code, so it can't lag behind it; an
    editable install's package metadata keeps the version it was installed
    at, which goes stale on a long-lived development image.
    """
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    try:
        return tomllib.loads(pyproject.read_text())["project"]["version"]
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        pass
    try:
        return package_version("securo-backend")
    except PackageNotFoundError:
        return None


def is_compatible(manifest: Manifest) -> bool:
    """This Securo is at least the plugin's minimum version (unknown counts as yes)."""
    running = securo_version()
    return running is None or version_tuple(running) >= version_tuple(manifest.min_securo_version)
