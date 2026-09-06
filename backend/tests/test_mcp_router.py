"""The MCP surface moved onto a router so two deployments can share it.

The dedicated mcp-server container runs `uvicorn mcp_server.main:app`; the
all-in-one image has no such container and includes the same router in the
API process. Both must expose `POST /mcp` at exactly that path — it is the
contract the Helm ingress and the agent runtime are wired to, and mounting
the app as a sub-application would have made it /mcp/mcp.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mcp_server.main import app as standalone_app
from mcp_server.main import router as mcp_router


def _paths(router) -> set[str]:
    return {route.path for route in router.routes}


def test_router_exposes_the_absolute_paths():
    assert _paths(mcp_router) == {"/health", "/mcp"}


def test_standalone_app_still_serves_health():
    """`uvicorn mcp_server.main:app` must be unaffected by the refactor."""
    response = TestClient(standalone_app).get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_standalone_app_still_rejects_unauthenticated_calls():
    response = TestClient(standalone_app).post(
        "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"}
    )
    assert response.status_code == 401


def test_router_keeps_its_paths_when_included_elsewhere():
    """Including it in the API process must not shift /mcp under a prefix."""
    host = FastAPI()
    host.include_router(mcp_router)
    client = TestClient(host)

    assert client.get("/health").status_code == 200
    assert client.post("/mcp", json={"jsonrpc": "2.0", "id": 1}).status_code == 401
    # And nothing appeared one level down.
    assert client.post("/mcp/mcp", json={}).status_code == 404


def test_mcp_routes_stay_out_of_the_public_schema():
    """The API publishes /api/openapi.json; the MCP JSON-RPC surface is not part of it."""
    host = FastAPI(openapi_url="/api/openapi.json")
    host.include_router(mcp_router)
    assert TestClient(host).get("/api/openapi.json").json()["paths"] == {}
