import uuid

import pytest
from httpx import AsyncClient

from app.models.user import User


@pytest.mark.asyncio
async def test_preferences_patch_preserves_unrelated_keys(
    client: AsyncClient, auth_headers: dict, test_user: User
):
    """PR-1 relies on the preferences endpoint being a whole-document write
    the client controls: sending `{...current, dashboard_layouts}` must keep
    every unrelated key (language, currency_display, display_name, ...) intact.
    Covers draft requirement "preference update preserves unrelated keys"."""
    # Seed an unrelated key first so the test proves preservation, not absence.
    resp = await client.patch(
        "/api/users/me",
        json={
            "preferences": {
                **dict(test_user.preferences or {}),
                "display_name": "Brian",
            }
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200

    layouts = {
        str(uuid.uuid4()): {
            "version": 1,
            "workspaceId": "ws-a",
            "items": [{"id": "hero-balance", "visible": True, "order": 0}],
        }
    }
    resp = await client.get("/api/users/me", headers=auth_headers)
    assert resp.status_code == 200
    current = resp.json()["preferences"]

    resp = await client.patch(
        "/api/users/me",
        json={"preferences": {**current, "dashboard_layouts": layouts}},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    prefs = resp.json()["preferences"]

    assert prefs["language"] == current["language"]
    assert prefs["currency_display"] == current["currency_display"]
    assert prefs["display_name"] == "Brian"
    assert prefs["dashboard_layouts"] == layouts


@pytest.mark.asyncio
async def test_dashboard_layouts_roundtrip_per_user_and_workspace(
    client: AsyncClient, auth_headers: dict, test_user: User, session
):
    """Two workspace keys round-trip independently in one document, and a
    second user's GET /api/users/me never contains the first user's layouts.
    Covers draft requirements "cross-workspace isolation" and per-user scope."""
    ws_a = str(uuid.uuid4())
    ws_b = str(uuid.uuid4())
    layout_a = {
        "version": 1,
        "workspaceId": ws_a,
        "items": [
            {"id": "hero-balance", "visible": True, "order": 0},
            {"id": "goals-progress", "visible": False, "order": 1},
        ],
    }
    layout_b = {
        "version": 1,
        "workspaceId": ws_b,
        "items": [{"id": "period-transactions", "visible": True, "order": 0}],
    }

    resp = await client.get("/api/users/me", headers=auth_headers)
    current = resp.json()["preferences"]
    resp = await client.patch(
        "/api/users/me",
        json={
            "preferences": {
                **current,
                "dashboard_layouts": {ws_a: layout_a, ws_b: layout_b},
            }
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200
    stored = resp.json()["preferences"]["dashboard_layouts"]
    assert stored[ws_a] == layout_a
    assert stored[ws_b] == layout_b

    # Second user: independent document, no leakage from user one.
    import bcrypt

    other = User(
        id=uuid.uuid4(),
        email="other-layouts@example.com",
        hashed_password=bcrypt.hashpw(b"otherpass123", bcrypt.gensalt()).decode(),
        is_active=True,
        is_superuser=False,
        is_verified=True,
        preferences={},
    )
    session.add(other)
    await session.commit()

    resp = await client.post(
        "/api/auth/login",
        data={"username": "other-layouts@example.com", "password": "otherpass123"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200
    other_headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}

    resp = await client.get("/api/users/me", headers=other_headers)
    assert resp.status_code == 200
    other_prefs = resp.json()["preferences"]
    assert "dashboard_layouts" not in other_prefs or ws_a not in other_prefs.get(
        "dashboard_layouts", {}
    )

    # First user's document is untouched by the second user's read.
    resp = await client.get("/api/users/me", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["preferences"]["dashboard_layouts"][ws_a] == layout_a


@pytest.mark.asyncio
async def test_malformed_dashboard_layout_is_stored_opaquely_without_error(
    client: AsyncClient, auth_headers: dict, test_user: User
):
    """Chosen contract (design packet §5.2): the server stores whatever JSON
    it receives without validation and the client sanitizes on read.
    Garbage under preferences.dashboard_layouts must PATCH 200, round-trip
    byte-stable, and leave GET /users/me at 200."""
    garbage = {
        "not-a-workspace": "just a string",
        str(uuid.uuid4()): {"version": "banana", "items": {"nope": True}},
        "another": [1, 2, 3],
    }
    resp = await client.get("/api/users/me", headers=auth_headers)
    current = resp.json()["preferences"]
    resp = await client.patch(
        "/api/users/me",
        json={"preferences": {**current, "dashboard_layouts": garbage}},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["preferences"]["dashboard_layouts"] == garbage

    resp = await client.get("/api/users/me", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["preferences"]["dashboard_layouts"] == garbage


@pytest.mark.asyncio
async def test_preferences_patch_whole_document_semantics_are_pinned(
    client: AsyncClient, auth_headers: dict, test_user: User
):
    """T0 spike probe (A-1). PATCH `preferences` with ONLY
    `dashboard_layouts` and observe whether unrelated keys survive:
    - REPLACE semantics (expected for fastapi-users v13: the whole column is
      overwritten with the payload): unrelated keys disappear -> the client
      contract PROTECTED by this test is "every write must send the full
      read-modify-write of the whole preferences document" (design packet
      §4.5 / app-layout.tsx:168-180 precedent), because only that keeps
      language/currency/display_name intact under replace semantics.
    - MERGE semantics (if observed): unrelated keys survive a partial write;
      the client contract still holds (full-document writes are correct under
      both), and assertions here tighten to record the merge.
    The assertions below are written for whichever outcome this run observes;
    the comment records the measured result."""
    resp = await client.get("/api/users/me", headers=auth_headers)
    before = resp.json()["preferences"]
    assert "language" in before and "currency_display" in before

    layouts = {
        str(uuid.uuid4()): {
            "version": 1,
            "workspaceId": "ws-probe",
            "items": [{"id": "hero-balance", "visible": True, "order": 0}],
        }
    }
    resp = await client.patch(
        "/api/users/me",
        json={"preferences": {"dashboard_layouts": layouts}},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    after = resp.json()["preferences"]

    # The layout itself always lands.
    assert after.get("dashboard_layouts") == layouts

    # MEASURED OUTCOME (T0 run, 2026-09-23): REPLACE semantics confirmed —
    # a partial `preferences` PATCH left only `dashboard_layouts` in the
    # stored document (unrelated keys dropped). A-1 is now fact, not
    # assumption: fastapi-users v13 overwrites the whole JSON column with
    # the payload. Therefore the client contract this test protects is
    # "every preferences write MUST be a full read-modify-write of the
    # whole document" (design packet §4.5 / app-layout.tsx:168-180),
    # because only that keeps language/currency_display/display_name
    # intact under replace semantics. The client code is correct under
    # both outcomes; these assertions pin the observed one.
    assert "language" not in after
    assert "currency_display" not in after
    assert after == {"dashboard_layouts": layouts}
