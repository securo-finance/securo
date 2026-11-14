"""The library choices: a theme per user, the dashboard and reports arranged per workspace."""
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access_token_scopes import required_scope
from app.models.workspace import Workspace, WorkspaceMember

pytestmark = pytest.mark.asyncio

DASHBOARD = [{"id": "overview"}, {"id": "balance-flow"}, {"id": "spending-by-category"}]


def _in(workspace: Workspace, headers: dict) -> dict:
    return {**headers, "X-Workspace-Id": str(workspace.id)}


async def test_nothing_saved_means_the_default(client: AsyncClient, auth_headers, test_workspace):
    response = await client.get("/api/ui/layouts", headers=_in(test_workspace, auth_headers))
    assert response.status_code == 200
    assert response.json() == {"dashboard": None, "reports": None}


async def test_save_replace_and_reset(client: AsyncClient, auth_headers, test_workspace):
    headers = _in(test_workspace, auth_headers)
    saved = await client.put("/api/ui/layouts/dashboard", json={"items": DASHBOARD}, headers=headers)
    assert saved.status_code == 200
    assert saved.json() == DASHBOARD

    reordered = list(reversed(DASHBOARD))
    await client.put("/api/ui/layouts/dashboard", json={"items": reordered}, headers=headers)
    await client.put("/api/ui/layouts/reports", json={"items": [{"id": "cash_flow"}]}, headers=headers)
    layouts = (await client.get("/api/ui/layouts", headers=headers)).json()
    assert layouts == {"dashboard": reordered, "reports": [{"id": "cash_flow"}]}

    assert (await client.delete("/api/ui/layouts/dashboard", headers=headers)).status_code == 204
    layouts = (await client.get("/api/ui/layouts", headers=headers)).json()
    assert layouts == {"dashboard": None, "reports": [{"id": "cash_flow"}]}


async def test_an_empty_dashboard_is_a_choice_not_the_default(client: AsyncClient, auth_headers, test_workspace):
    headers = _in(test_workspace, auth_headers)
    await client.put("/api/ui/layouts/dashboard", json={"items": []}, headers=headers)
    assert (await client.get("/api/ui/layouts", headers=headers)).json()["dashboard"] == []


async def test_each_workspace_has_its_own_arrangement(
    client: AsyncClient, session: AsyncSession, auth_headers, test_user, test_workspace
):
    other = Workspace(id=uuid.uuid4(), name="Other", kind="personal", created_by_user_id=test_user.id)
    session.add(other)
    await session.flush()
    session.add(WorkspaceMember(id=uuid.uuid4(), workspace_id=other.id, user_id=test_user.id, role="owner"))
    await session.commit()

    await client.put("/api/ui/layouts/dashboard", json={"items": DASHBOARD}, headers=_in(test_workspace, auth_headers))
    layouts = (await client.get("/api/ui/layouts", headers=_in(other, auth_headers))).json()
    assert layouts["dashboard"] is None


async def test_personal_even_for_a_viewer(client: AsyncClient, auth_headers, viewer_auth_headers, test_workspace):
    """A viewer can't change the workspace, but can arrange their own view of it."""
    mine = [{"id": "goals"}]
    assert (await client.put("/api/ui/layouts/dashboard", json={"items": mine}, headers=viewer_auth_headers)).status_code == 200
    assert (await client.get("/api/ui/layouts", headers=viewer_auth_headers)).json()["dashboard"] == mine
    # The owner's view of the same workspace is untouched.
    owner = (await client.get("/api/ui/layouts", headers=_in(test_workspace, auth_headers))).json()
    assert owner["dashboard"] is None


@pytest.mark.parametrize(
    "body",
    [
        {"items": [{"id": "goals"}, {"id": "goals"}]},
        {"items": [{"id": "Not An Id"}]},
        {"items": [{"id": ""}]},
        {"items": [{"id": "goals", "size": "full"}]},
        {"items": [{"id": "goals", "span": 5}]},
        {"items": [{"id": f"item-{n}"} for n in range(41)]},
    ],
    ids=["duplicate", "malformed", "blank", "unknown field", "odd span", "too many"],
)
async def test_refuses_what_isnt_a_layout(client: AsyncClient, auth_headers, test_workspace, body):
    response = await client.put("/api/ui/layouts/dashboard", json=body, headers=_in(test_workspace, auth_headers))
    assert response.status_code == 422


async def test_only_known_surfaces(client: AsyncClient, auth_headers, test_workspace):
    headers = _in(test_workspace, auth_headers)
    assert (await client.put("/api/ui/layouts/sidebar", json={"items": []}, headers=headers)).status_code == 422
    assert (await client.delete("/api/ui/layouts/sidebar", headers=headers)).status_code == 422


async def test_plugin_ids_fit(client: AsyncClient, auth_headers, test_workspace):
    items = [{"id": "plugin:maria/home-and-pets:pet-costs"}]
    response = await client.put("/api/ui/layouts/dashboard", json={"items": items}, headers=_in(test_workspace, auth_headers))
    assert response.status_code == 200


async def test_needs_a_session():
    """An access token never reaches someone's view settings."""
    assert required_scope("GET", "/api/ui/layouts") is None
    assert required_scope("PUT", "/api/ui/layouts/dashboard") is None


async def test_theme_is_kept_per_user(client: AsyncClient, auth_headers):
    assert (await client.get("/api/users/me", headers=auth_headers)).json()["theme"] is None

    saved = await client.patch("/api/users/me", json={"theme": "ocean"}, headers=auth_headers)
    assert saved.status_code == 200
    assert saved.json()["theme"] == "ocean"
    assert (await client.get("/api/users/me", headers=auth_headers)).json()["theme"] == "ocean"

    # Saving other preferences leaves the theme alone.
    await client.patch("/api/users/me", json={"preferences": {"language": "en"}}, headers=auth_headers)
    assert (await client.get("/api/users/me", headers=auth_headers)).json()["theme"] == "ocean"

    reset = await client.patch("/api/users/me", json={"theme": None}, headers=auth_headers)
    assert reset.json()["theme"] is None


@pytest.mark.parametrize("theme", ["Ocean", "<script>", "a" * 161, "../x"])
async def test_theme_must_be_an_id(client: AsyncClient, auth_headers, theme):
    response = await client.patch("/api/users/me", json={"theme": theme}, headers=auth_headers)
    assert response.status_code == 422


async def test_keeps_widths(client: AsyncClient, auth_headers, test_workspace):
    headers = _in(test_workspace, auth_headers)
    items = [{"id": "overview", "span": 8}, {"id": "goals", "span": 4}, {"id": "transactions"}]
    saved = await client.put("/api/ui/layouts/dashboard", json={"items": items}, headers=headers)
    assert saved.status_code == 200
    assert (await client.get("/api/ui/layouts", headers=headers)).json()["dashboard"] == [
        {"id": "overview", "span": 8},
        {"id": "goals", "span": 4},
        {"id": "transactions"},
    ]
