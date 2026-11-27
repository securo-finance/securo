"""Theme plugins: the manifest's `themes`, installing, the library's list and removing."""
from __future__ import annotations

import copy
import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from pydantic import ValidationError

from app.plugins.manifest import contrast_ratio, parse_manifest
from tests.test_data_plugins import PLUGIN, V1, catalog  # noqa: F401  (fixture)

NEON: dict[str, Any] = {
    "key": "neon",
    "name": {"en": "Neon", "pt-BR": "Neon"},
    "dark": {
        "background": "#05010D",
        "foreground": "#F5E9FF",
        "card": "#0E0620",
        "primary": "#FF2BD6",
        "primary-foreground": "#1A0016",
        "muted-foreground": "#B79BD9",
    },
    "light": {"background": "#FFF5FD", "foreground": "#24002B", "muted-foreground": "#5C3A66"},
    "radius": 0,
    "font": "mono",
}


def _manifest(**changes) -> dict[str, Any]:
    from tests.test_data_plugins import MANIFEST

    manifest = copy.deepcopy(MANIFEST)
    manifest["contributes"] = {"themes": [copy.deepcopy(NEON)]}
    manifest.update(changes)
    return manifest


def test_a_theme_pack_is_a_valid_plugin():
    manifest = parse_manifest(_manifest())
    theme = manifest.contributes.themes[0]
    assert (theme.key, theme.font, theme.radius) == ("neon", "mono", 0)
    assert not manifest.has_code


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda t: t["dark"].update(background="url(https://evil.example/x.png)"), "#RRGGBB"),
        (lambda t: t["dark"].update(background="var(--primary)"), "#RRGGBB"),
        (lambda t: t["dark"].update(sidebar="#000000"), "unknown theme tokens"),
        (lambda t: t["dark"].update(foreground="#0A0A0A"), "hard to read"),
        (lambda t: t.update(font="Comic Sans"), "font"),
        (lambda t: t.update(radius=3), "radius"),
        (lambda t: (t.update(light={}), t.update(dark={})), "colors nothing"),
        # White text kept on Securo's own light background: the inherited pair is unreadable.
        (lambda t: t.update(light={"foreground": "#FFFFFF"}), "hard to read"),
    ],
    ids=["url", "var", "unknown-token", "unreadable", "font", "radius", "no-colors", "inherited-background"],
)
def test_themes_that_break_the_rules_are_rejected(change, message):
    manifest = _manifest()
    change(manifest["contributes"]["themes"][0])
    with pytest.raises(ValidationError, match=message):
        parse_manifest(manifest)


def test_contrast_matches_the_frontend_formula():
    assert round(contrast_ratio("#000000", "#FFFFFF"), 1) == 21.0
    assert round(contrast_ratio("#767676", "#FFFFFF"), 2) == 4.54


@pytest.mark.asyncio
async def test_api_installs_a_theme_pack_and_lists_its_themes(client: AsyncClient, auth_headers: dict, catalog):  # noqa: F811
    catalog.manifests[V1] = _manifest()
    entries = (await client.get("/api/plugins/catalog", headers=auth_headers)).json()
    assert entries[0]["contributes"] == {"themes": 1} and entries[0]["supported"] is True

    plan = (await client.get(f"/api/plugins/{PLUGIN}/plan", params={"action": "install"}, headers=auth_headers)).json()
    assert plan["themes"] == {"added": [{"key": "neon", "name": {"en": "Neon", "pt-BR": "Neon"}}], "removed": []}
    assert plan["code"] is None
    body = {"action": "install", "commit": plan["commit"], "token": plan["token"]}
    assert (await client.post(f"/api/plugins/{PLUGIN}/apply", json=body, headers=auth_headers)).status_code == 200

    catalog.down = True  # the library reads what was installed, never the catalog
    installed = (await client.get("/api/plugins", headers=auth_headers)).json()
    theme = installed[0]["themes"][0]
    assert theme["key"] == "neon" and theme["font"] == "mono" and theme["dark"]["primary"] == "#FF2BD6"

    catalog.down = False
    plan = (await client.get(f"/api/plugins/{PLUGIN}/plan", params={"action": "remove"}, headers=auth_headers)).json()
    assert [t["key"] for t in plan["themes"]["removed"]] == ["neon"] and plan["themes"]["added"] == []
    body = {"action": "remove", "commit": None, "token": plan["token"]}
    assert (await client.post(f"/api/plugins/{PLUGIN}/apply", json=body, headers=auth_headers)).status_code == 200
    assert (await client.get("/api/plugins", headers=auth_headers)).json() == []


@pytest.mark.asyncio
async def test_details_show_the_themes_inside(client: AsyncClient, auth_headers: dict, catalog):  # noqa: F811
    catalog.manifests[V1] = _manifest()
    detail = (await client.get(f"/api/plugins/{PLUGIN}", headers=auth_headers)).json()
    assert [t["key"] for t in detail["contents"]["themes"]] == ["neon"]


@pytest.mark.asyncio
async def test_a_user_can_pick_a_plugin_theme(client: AsyncClient, auth_headers: dict):
    resp = await client.patch("/api/users/me", json={"theme": f"plugin:{PLUGIN}:neon"}, headers=auth_headers)
    assert resp.status_code == 200 and resp.json()["theme"] == f"plugin:{PLUGIN}:neon"


async def _apply(client, headers, action):
    plan = (await client.get(f"/api/plugins/{PLUGIN}/plan", params={"action": action}, headers=headers)).json()
    body = {"action": action, "commit": plan["commit"], "token": plan["token"]}
    resp = await client.post(f"/api/plugins/{PLUGIN}/apply", json=body, headers=headers)
    assert resp.status_code == 200, resp.text
    return plan


@pytest.mark.asyncio
async def test_removing_the_plugin_brings_back_securos_theme(client: AsyncClient, auth_headers: dict, catalog):  # noqa: F811
    catalog.manifests[V1] = _manifest()
    await _apply(client, auth_headers, "install")
    await client.patch("/api/users/me", json={"theme": f"plugin:{PLUGIN}:neon"}, headers=auth_headers)
    await _apply(client, auth_headers, "remove")
    assert (await client.get("/api/users/me", headers=auth_headers)).json()["theme"] is None


@pytest.mark.asyncio
async def test_an_update_that_drops_a_theme_lists_it_and_releases_it(
    client: AsyncClient, auth_headers: dict, catalog  # noqa: F811
):
    from tests.test_data_plugins import V2

    two = _manifest()
    two["contributes"]["themes"].append(dict(copy.deepcopy(NEON), key="dusk", name="Dusk"))
    catalog.manifests[V1] = two
    await _apply(client, auth_headers, "install")
    await client.patch("/api/users/me", json={"theme": f"plugin:{PLUGIN}:dusk"}, headers=auth_headers)

    v2 = _manifest(version="1.1.0")
    v2["contributes"]["themes"].append(dict(copy.deepcopy(NEON), key="dawn", name="Dawn"))
    catalog.manifests[V2] = v2
    catalog.commit = V2
    plan = await _apply(client, auth_headers, "update")
    assert [t["key"] for t in plan["themes"]["added"]] == ["dawn"]
    assert [t["key"] for t in plan["themes"]["removed"]] == ["dusk"]
    assert (await client.get("/api/users/me", headers=auth_headers)).json()["theme"] is None


@pytest.mark.asyncio
async def test_a_theme_still_installed_elsewhere_is_kept(session, test_user, test_workspace, catalog):  # noqa: F811
    from app.models.user import User
    from app.models.workspace import Workspace, WorkspaceMember
    from app.models.workspace_plugin import WorkspacePlugin
    from app.plugins import service

    other = uuid.uuid4()
    session.add(Workspace(id=other, name="Other", kind="personal", default_currency="USD", locale="en"))
    await session.flush()
    session.add(WorkspaceMember(id=uuid.uuid4(), workspace_id=other, user_id=test_user.id, role="owner"))
    session.add(WorkspacePlugin(workspace_id=other, plugin_id=PLUGIN, name={"en": "x"}, version="1.0.0", commit=V1))
    user = await session.get(User, test_user.id)
    assert user is not None
    user.theme = f"plugin:{PLUGIN}:neon"
    await session.commit()
    await service._release_themes(session, test_workspace.id, PLUGIN, {"neon"})
    await session.commit()
    await session.refresh(user)
    assert user.theme == f"plugin:{PLUGIN}:neon"
