"""GET /api/info: the build identity plus the feature flags the UI reads.

The public demo leans on this endpoint to prove which version it serves
without anyone reading the branch on the host, so the shape is a contract.
"""
import pytest
from httpx import AsyncClient

from app.core.config import get_settings

pytestmark = pytest.mark.asyncio


async def test_info_reports_a_version_and_no_revision_by_default(client: AsyncClient):
    response = await client.get("/api/info")

    assert response.status_code == 200
    body = response.json()
    version = body["version"]
    assert isinstance(version, str) and version
    # A release version (or the source-tree placeholder); never empty.
    assert version.count(".") >= 2
    assert body["revision"] is None


async def test_info_reports_the_revision_the_deployment_baked_in(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(get_settings(), "app_revision", "52f83db")

    body = (await client.get("/api/info")).json()

    assert body["revision"] == "52f83db"


async def test_info_still_reports_the_feature_flags(client: AsyncClient):
    body = (await client.get("/api/info")).json()

    assert {"agents", "demo", "tesouro_direto", "plugins"} <= set(body["features"])
