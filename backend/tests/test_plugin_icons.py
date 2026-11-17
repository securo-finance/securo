"""Plugin icons: copied into the catalog under their checksum, fetched from its host, served by Securo."""
from __future__ import annotations

import hashlib
import logging

import httpx
import pytest
from httpx import AsyncClient

from app.plugins import index
from tests.test_data_plugins import INDEX_URL, PLUGIN, Catalog

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 24
ICONS_URL = "https://plugins.usesecuro.com/v1/icons/"


def _name(icon: bytes, extension: str = "png") -> str:
    """The icon's file name in the catalog: its sha256 and what it is."""
    return f"{hashlib.sha256(icon).hexdigest()}.{extension}"


class IconCatalog(Catalog):
    """The fake catalog, with icon copies under `icons/`, recording what's fetched."""

    def __init__(self) -> None:
        super().__init__()
        self.icons: dict[str, bytes] = {}
        self.requests: list[str] = []
        self.publish_icon(PNG)

    def publish_icon(self, icon: bytes, extension: str = "png") -> str:
        """Copy an icon into the catalog and name it in the plugin's entry; its file name."""
        name = _name(icon, extension)
        self.icons[name] = icon
        self.extra = {"icon": f"icons/{name}"}
        return name

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        if not self.down and url.startswith(ICONS_URL) and url.removeprefix(ICONS_URL) in self.icons:
            return httpx.Response(200, content=self.icons[url.removeprefix(ICONS_URL)])
        return super().handle(request)


@pytest.fixture
def catalog(monkeypatch) -> IconCatalog:
    fake = IconCatalog()
    monkeypatch.setattr(index, "http_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(fake.handle)))
    index._manifests.clear()
    index._readmes.clear()
    index._icons.clear()
    monkeypatch.setenv("PLUGINS_ENABLED", "true")
    return fake


def _route(name: str) -> str:
    return f"/api/plugins/icons/{name}"


# --- The catalog -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_catalog_rows_say_where_the_icon_is_served(client: AsyncClient, auth_headers, catalog):
    (row,) = (await client.get("/api/plugins/catalog", headers=auth_headers)).json()
    assert row["icon_url"] == _route(_name(PNG))
    assert "icon" not in row
    assert catalog.requests == [INDEX_URL]  # listing the catalog is still that one request

    detail = (await client.get(f"/api/plugins/{PLUGIN}", headers=auth_headers)).json()
    assert detail["plugin"]["icon_url"] == _route(_name(PNG))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"icon": "icons/maria--casa--aaaa.png"},  # not named by its checksum: nothing to check it against
        {"icon": f"https://evil.example.com/icons/{_name(PNG)}"},
        {"icon": f"icons/{_name(PNG, 'svg')}"},
    ],
)
async def test_a_plugin_without_a_checksum_named_icon_has_no_icon_url(client: AsyncClient, auth_headers, catalog, extra):
    catalog.extra = extra
    (row,) = (await client.get("/api/plugins/catalog", headers=auth_headers)).json()
    assert row["id"] == PLUGIN and row["icon_url"] is None  # still listed, with the generated badge


# --- Serving it ------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("image, extension, content_type", [(PNG, "png", "image/png"), (WEBP, "webp", "image/webp")])
async def test_the_icon_is_fetched_from_the_catalog_and_served_by_its_name(
    client: AsyncClient, catalog, image, extension, content_type
):
    name = catalog.publish_icon(image, extension)
    resp = await client.get(_route(name))  # an <img> source: no sign-in
    assert resp.status_code == 200 and resp.content == image
    assert resp.headers["content-type"] == content_type
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in resp.headers["content-security-policy"]
    assert "immutable" in resp.headers["cache-control"]
    assert catalog.requests == [f"{ICONS_URL}{name}"]  # the catalog's host, that one file, nothing else

    assert (await client.get(_route(name))).content == image
    assert len(catalog.requests) == 1  # kept in memory by its name, as READMEs are


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name",
    [
        "not-a-checksum.png",
        "0" * 64,  # no extension
        f"{'0' * 64}.svg",
        f"{'A' * 64}.png",
        f"..%2F..%2Fmanifests%2F{'a' * 40}.json",
        f"{'0' * 64}.png%3Fx=1",
    ],
)
async def test_only_a_checksum_name_reaches_the_catalog(client: AsyncClient, catalog, name, caplog):
    caplog.set_level(logging.INFO, logger="app.plugins")
    assert (await client.get(_route(name))).status_code == 404
    assert catalog.requests == []
    assert not caplog.records  # anyone can ask for any name: it isn't worth a log line


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["tampered", "not an image", "too large", "missing", "catalog down"])
async def test_an_icon_that_fails_a_check_is_not_served(client: AsyncClient, catalog, case):
    name = _name(PNG)
    if case == "tampered":
        catalog.icons[name] = PNG + b" "  # no longer hashes to its name
    elif case == "not an image":
        name = catalog.publish_icon(b"<svg xmlns='http://www.w3.org/2000/svg'/>")
    elif case == "too large":
        name = catalog.publish_icon(PNG + b"\x00" * index.MAX_ICON_BYTES)
    elif case == "missing":
        name = _name(WEBP, "webp")
    else:
        catalog.down = True

    resp = await client.get(_route(name))
    assert resp.status_code == 404
    assert resp.headers["cache-control"] == "no-store"  # it may be there next time

    fetched = len(catalog.requests)
    await client.get(_route(name))
    assert len(catalog.requests) == fetched + 1  # a refusal isn't remembered either


@pytest.mark.asyncio
async def test_icons_answer_404_while_plugins_are_off(client: AsyncClient, catalog, monkeypatch):
    monkeypatch.setenv("PLUGINS_ENABLED", "false")
    assert (await client.get(_route(_name(PNG)))).status_code == 404
    assert catalog.requests == []
