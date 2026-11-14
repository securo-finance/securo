"""The plugin catalog: a static index, fetched only when someone opens it.

The index is built outside Securo by an indexer that finds plugins by their
GitHub topic and pins each to the commit of its latest release. It carries
what the catalog shows (name, description, contents, stars, dates) and, per
plugin, where a copy of its manifest is and that copy's sha256. Listing the
catalog is that one request; a manifest is fetched only to plan an install
or update, from the index's own host, and is refused unless its sha256
matches. A plugin's README is copied there too and fetched, on the same
terms, only when someone opens that plugin. Requests never follow redirects
and are capped in size.
"""
from __future__ import annotations

import hashlib
import json
import logging
import tomllib
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.config import get_settings
from app.plugins.manifest import (
    CODE_KINDS,
    DATA_KINDS,
    UI_KINDS,
    ID_PATTERN,
    REPOSITORY_PATTERN,
    VERSION_PATTERN,
    Manifest,
    Text,
    parse_manifest,
    version_tuple,
)

logger = logging.getLogger(__name__)

_TIMEOUT = 10.0
_MAX_INDEX_BYTES = 4 << 20
_MAX_MANIFEST_BYTES = 2 << 20
_MAX_README_BYTES = 256 << 10
_MAX_ARCHIVE_BYTES = 20 << 20
_MANIFEST_CACHE_SIZE = 256

def supported_contributions() -> frozenset[str]:
    """What this instance can apply. Plugins with anything else are listed but
    can't be installed: code needs a plugin runner to run in."""
    from app.plugins import runner

    base = DATA_KINDS | UI_KINDS
    return base | CODE_KINDS if runner.configured() else base


class PluginIndexError(Exception):
    """The catalog or a manifest couldn't be fetched or isn't valid."""


class IndexEntry(BaseModel):
    """One plugin as the index describes it."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    id: str = Field(pattern=ID_PATTERN)
    repository: str = Field(pattern=REPOSITORY_PATTERN)
    release: str = Field(min_length=1, max_length=64)
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    manifest: str = Field(min_length=1, max_length=512, description="The manifest copy, relative to the index.")
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    readme: str | None = Field(default=None, max_length=512, description="The README copy, relative to the index.")
    readme_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    archive: str | None = Field(
        default=None, max_length=512, description="The release's source tarball, relative to the index (plugins with code)."
    )
    archive_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    name: Text
    description: Text
    version: str = Field(pattern=VERSION_PATTERN)
    license: str = Field(min_length=2, max_length=64)
    min_securo_version: str = Field(pattern=VERSION_PATTERN)
    contributes: dict[str, int] = Field(description="How many items of each kind the plugin brings.")
    locales: list[str] = []
    topics: list[str] = []
    stars: int = Field(default=0, ge=0)
    stars_7d: int = Field(default=0, ge=0, description="Stars gained in the last 7 days.")
    added_at: datetime | None = None
    updated_at: datetime | None = None
    revoked: bool = False
    revoked_reason: str | None = Field(default=None, max_length=500)

    @property
    def owner(self) -> str:
        return self.repository.rstrip("/").split("/")[-2]

    @property
    def supported(self) -> bool:
        """Everything it contributes is something this Securo can apply."""
        return {kind for kind, count in self.contributes.items() if count} <= supported_contributions()

    @property
    def compatible(self) -> bool:
        return is_compatible(self.min_securo_version)


def http_client() -> httpx.AsyncClient:
    """The client every catalog request goes through (tests swap it)."""
    return httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False)


async def _get(client: httpx.AsyncClient, url: str, limit: int) -> bytes:
    try:
        async with client.stream("GET", url) as response:
            if response.status_code != 200:
                raise PluginIndexError(f"{url} answered {response.status_code}")
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body += chunk
                if len(body) > limit:
                    raise PluginIndexError(f"{url} is larger than {limit} bytes")
    except httpx.HTTPError as exc:
        raise PluginIndexError(f"{url} couldn't be fetched: {exc.__class__.__name__}") from exc
    return bytes(body)


async def fetch_index(client: httpx.AsyncClient) -> list[IndexEntry]:
    """The catalog's plugins. An entry that isn't valid is skipped, not fatal."""
    try:
        data = json.loads(await _get(client, get_settings().plugins_index_url, _MAX_INDEX_BYTES))
    except ValueError as exc:
        raise PluginIndexError("the plugin index isn't JSON") from exc
    if not isinstance(data, dict) or not isinstance(data.get("plugins"), list):
        raise PluginIndexError("the plugin index has no plugin list")
    entries: dict[str, IndexEntry] = {}
    for raw in data["plugins"]:
        try:
            entry = IndexEntry.model_validate(raw)
        except ValidationError:
            logger.warning("Skipping an invalid plugin index entry: %s", str(raw)[:200])
            continue
        entries.setdefault(entry.id, entry)
    return list(entries.values())


_manifests: dict[str, Manifest] = {}
_readmes: dict[str, str] = {}


def _on_index_host(entry: IndexEntry, path: str, what: str) -> str:
    """The URL of a copy the index points at: on the index's own host, nowhere else."""
    index_url = get_settings().plugins_index_url
    url = urljoin(index_url, path)
    index, target = urlsplit(index_url), urlsplit(url)
    if (target.scheme, target.netloc) != (index.scheme, index.netloc):
        raise PluginIndexError(f"{entry.id}: its {what} isn't on the catalog's host")
    return url


def manifest_url(entry: IndexEntry) -> str:
    return _on_index_host(entry, entry.manifest, "manifest")


def _remember(cache: dict, key: str, value) -> None:
    if len(cache) >= _MANIFEST_CACHE_SIZE:
        cache.pop(next(iter(cache)))
    cache[key] = value


async def fetch_manifest(client: httpx.AsyncClient, entry: IndexEntry) -> Manifest:
    """The manifest the index pins: its sha256 must match, and it must be that plugin."""
    manifest = _manifests.get(entry.manifest_sha256)  # addressed by content: a hit can't be stale
    if manifest is None:
        body = await _get(client, manifest_url(entry), _MAX_MANIFEST_BYTES)
        if hashlib.sha256(body).hexdigest() != entry.manifest_sha256:
            raise PluginIndexError(f"{entry.id}: the manifest doesn't match the catalog's checksum")
        try:
            manifest = parse_manifest(json.loads(body))
        except ValueError as exc:
            reason = exc.errors()[0]["msg"] if isinstance(exc, ValidationError) else "not JSON"
            raise PluginIndexError(f"{entry.id}: invalid manifest ({str(reason).removeprefix('Value error, ')})") from exc
        _remember(_manifests, entry.manifest_sha256, manifest)
    # Checked on every read: another entry may point at content already cached.
    if (manifest.id, manifest.repository, manifest.version) != (entry.id, entry.repository, entry.version):
        raise PluginIndexError(f"{entry.id}: the manifest isn't the plugin the catalog lists")
    return manifest


async def fetch_readme(client: httpx.AsyncClient, entry: IndexEntry) -> str | None:
    """The plugin's README as Markdown, if the index has one; its sha256 must match."""
    if not entry.readme or not entry.readme_sha256:
        return None
    if entry.readme_sha256 in _readmes:
        return _readmes[entry.readme_sha256]
    body = await _get(client, _on_index_host(entry, entry.readme, "README"), _MAX_README_BYTES)
    if hashlib.sha256(body).hexdigest() != entry.readme_sha256:
        raise PluginIndexError(f"{entry.id}: the README doesn't match the catalog's checksum")
    try:
        readme = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PluginIndexError(f"{entry.id}: the README isn't UTF-8") from exc
    _remember(_readmes, entry.readme_sha256, readme)
    return readme


async def fetch_archive(client: httpx.AsyncClient, entry: IndexEntry) -> tuple[bytes, str]:
    """The source tarball of a plugin with code, and its sha256, checked against the index."""
    if not entry.archive or not entry.archive_sha256:
        raise PluginIndexError(f"{entry.id}: the catalog has no copy of its code")
    body = await _get(client, _on_index_host(entry, entry.archive, "code"), _MAX_ARCHIVE_BYTES)
    if hashlib.sha256(body).hexdigest() != entry.archive_sha256:
        raise PluginIndexError(f"{entry.id}: the code doesn't match the catalog's checksum")
    return body, entry.archive_sha256


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


def is_compatible(min_securo_version: str) -> bool:
    """This Securo is at least that version (unknown counts as yes)."""
    running = securo_version()
    return running is None or version_tuple(running) >= version_tuple(min_securo_version)
