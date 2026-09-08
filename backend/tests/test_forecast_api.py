import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_get_recurring_forecast_api_empty(client: AsyncClient, auth_headers):
    response = await client.get("/api/forecast/recurring", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert "workspace_id" in data
    assert data["total_detected"] == 0
    assert data["items"] == []


@pytest.mark.asyncio
async def test_get_cashflow_forecast_api(client: AsyncClient, auth_headers):
    response = await client.get("/api/forecast/cashflow?horizon_days=30", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert "workspace_id" in data
    assert "summary" in data
    assert "daily_trajectory" in data
    assert data["summary"]["horizon_days"] == 30
    assert len(data["daily_trajectory"]) == 31
