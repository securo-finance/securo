"""Logos a plugin ships in its package, served by Securo itself.

A provider's accounts name their institution's logo by a key of the
manifest's `logos`, never by URL: a URL would have every viewer's browser
reach a host the plugin didn't declare. On install the files are taken out of
the package (the same archive the runner gets, checked against the catalog's
checksum), checked to be PNG or WebP and small, and kept per commit. They are
served at `/api/plugins/logos/<sha256>`.
"""
from __future__ import annotations

import hashlib
import io
import tarfile
from collections.abc import Awaitable, Callable
from pathlib import PurePosixPath

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.plugin_logo import PluginLogo
from app.models.workspace_plugin import WorkspacePlugin
from app.plugins.manifest import Manifest

MAX_LOGO_BYTES = 256 * 1024
#: Files read from a package before giving up, as the runner counts them.
_MEMBER_LIMIT = 10_000
URL_PREFIX = "/api/plugins/logos/"


class LogoError(ValueError):
    """A logo the manifest declares is missing from the package, too large, or not a PNG or WebP."""


def url(sha256: str) -> str:
    return f"{URL_PREFIX}{sha256}"


def content_type(data: bytes) -> str | None:
    """The image type, from the file's own signature (never its name)."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def extract(archive: bytes, paths: dict[str, str]) -> dict[str, tuple[str, bytes]]:
    """Each declared logo, by key, as (content type, bytes), from a release tarball.

    The tarball's single top folder is dropped, as the runner does when it
    unpacks the same archive.
    """
    wanted: dict[str, list[str]] = {}
    for key, path in paths.items():
        wanted.setdefault(path, []).append(key)
    try:
        tar = tarfile.open(fileobj=io.BytesIO(archive), mode="r:*")
    except tarfile.TarError as exc:
        raise LogoError("the package is not a tarball") from exc
    found: dict[str, tuple[str, bytes]] = {}
    with tar:
        members = []
        while (member := tar.next()) is not None:
            members.append(member)
            if len(members) > _MEMBER_LIMIT:
                raise LogoError("the package has too many files")
        tops = {PurePosixPath(m.name).parts[0] for m in members if PurePosixPath(m.name).parts}
        strip = len(tops) == 1 and all(m.isdir() for m in members if m.name.rstrip("/") in tops)
        for member in members:
            parts = PurePosixPath(member.name).parts[1 if strip else 0 :]
            keys = wanted.get("/".join(parts))
            if not keys or not member.isfile():
                continue
            if member.size > MAX_LOGO_BYTES:
                raise LogoError(f"logo {keys[0]} is larger than {MAX_LOGO_BYTES // 1024} KB")
            source = tar.extractfile(member)
            if source is None:
                continue
            with source:
                data = source.read(MAX_LOGO_BYTES + 1)
            kind = content_type(data)
            if kind is None:
                raise LogoError(f"logo {keys[0]} is not a PNG or WebP image")
            found.update((key, (kind, data)) for key in keys)
    missing = sorted(set(paths) - set(found))
    if missing:
        raise LogoError(f"logos missing from the package: {', '.join(missing)}")
    return found


async def store(
    session: AsyncSession,
    plugin_id: str,
    commit: str,
    manifest: Manifest,
    fetch_archive: Callable[[], Awaitable[bytes]],
) -> None:
    """Keep the commit's logos, taking them out of its package unless they're kept already (not committed)."""
    if not manifest.logos:
        return
    kept = set(
        (
            await session.scalars(
                select(PluginLogo.key).where(PluginLogo.plugin_id == plugin_id, PluginLogo.commit == commit)
            )
        ).all()
    )
    if set(manifest.logos) <= kept:
        return
    for key, (kind, data) in extract(await fetch_archive(), manifest.logos).items():
        if key in kept:
            continue
        session.add(
            PluginLogo(
                plugin_id=plugin_id,
                commit=commit,
                key=key,
                content_type=kind,
                sha256=hashlib.sha256(data).hexdigest(),
                data=data,
            )
        )
    await session.flush()


async def urls(session: AsyncSession, plugin_id: str, commit: str) -> dict[str, str]:
    """The commit's logos, by key, as the address Securo serves each at."""
    rows = await session.execute(
        select(PluginLogo.key, PluginLogo.sha256).where(
            PluginLogo.plugin_id == plugin_id, PluginLogo.commit == commit
        )
    )
    return {key: url(sha256) for key, sha256 in rows.all()}


async def get(session: AsyncSession, sha256: str) -> PluginLogo | None:
    return await session.scalar(select(PluginLogo).where(PluginLogo.sha256 == sha256).limit(1))


async def release(session: AsyncSession, plugin_id: str, commit: str) -> None:
    """Drop a commit's logos once no workspace runs it (not committed)."""
    still_used = await session.scalar(
        select(WorkspacePlugin.id)
        .where(WorkspacePlugin.plugin_id == plugin_id, WorkspacePlugin.commit == commit)
        .limit(1)
    )
    if still_used is None:
        await session.execute(
            delete(PluginLogo).where(PluginLogo.plugin_id == plugin_id, PluginLogo.commit == commit)
        )
