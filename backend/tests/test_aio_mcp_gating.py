"""`AGENTS_MCP_INPROCESS` decides whether this process serves `POST /mcp`.

It is its own flag, not a rider on `AGENTS_ENABLED`. The all-in-one image has
no mcp-server container, so the API answers /mcp itself when asked for it; the
multi-container setup publishes that surface on its own port and must not gain
it silently just because the agent runtime was switched on.

Mounting happens once, at import of `app.main`, and conftest forces
`AGENTS_ENABLED=true` on the whole test process before that import. Neither
monkeypatch nor importlib.reload can undo that for a single test — the
fixtures hold a reference to the app object that was built at import — so each
combination gets its own interpreter.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]

# Ask the app what it answers, rather than walking app.routes: included
# routers are wrapped rather than flattened, so introspection has to recurse
# into `original_router` (see test_write_permission_coverage.py). The status
# code is the actual contract anyway — 401 from verify_request means the
# router is mounted, 404 means it is not. Neither path reaches the database.
PROBE = """
import asyncio

import httpx

from app.main import app


async def probe():
    # httpx over the ASGI app, like the suite's own client fixture. Not
    # starlette.testclient, which wants httpx2 now and warns when it cannot
    # find it. No lifespan either: it warms the Tesouro cache and opens
    # Redis, and an unauthenticated POST needs neither.
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1})
    return response.status_code


print("MCP_STATUS", asyncio.run(probe()))
"""

MOUNTED = 401
ABSENT = 404


def _mcp_status(agents: str, inprocess: str, secret: str = "a-real-looking-signing-secret") -> int:
    env = {
        **os.environ,
        "AGENTS_ENABLED": agents,
        "AGENTS_MCP_INPROCESS": inprocess,
        "AGENTS_MCP_JWT_SECRET": secret,
    }
    # cwd matters: mcp_server is not an installed package (pyproject's
    # packages.find includes only "app*"), it imports because the backend
    # tree is the working directory.
    proc = subprocess.run(
        [sys.executable, "-c", PROBE],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, f"probe failed:\n{proc.stderr}"
    for line in proc.stdout.splitlines():
        if line.startswith("MCP_STATUS "):
            return int(line.split()[1])
    raise AssertionError(f"probe printed no status:\n{proc.stdout}\n{proc.stderr}")


@pytest.mark.parametrize(
    ("agents", "inprocess", "expected"),
    [
        # The all-in-one deployment: the MCP surface without the agent runtime.
        ("false", "true", MOUNTED),
        ("true", "true", MOUNTED),
        # The default everywhere: nothing extra is exposed.
        ("false", "false", ABSENT),
        # Turning agents on is not consent to publish /mcp on the API's port.
        ("true", "false", ABSENT),
    ],
)
def test_mcp_mounting_follows_its_own_flag(agents, inprocess, expected):
    assert _mcp_status(agents, inprocess) == expected


@pytest.mark.parametrize(
    "secret",
    [
        "",
        "   ",
        # The default in app/agents/config.py.
        "change-me-in-production",
        # The one docker-compose.yml ships.
        "dev-mcp-secret-change-in-production",
    ],
)
def test_mcp_refuses_to_mount_without_a_real_signing_secret(secret):
    """Asking for /mcp is not enough — the tokens have to be worth verifying.

    The compose file leaves AGENTS_MCP_JWT_SECRET empty so a deployment with
    the flag off never has to invent one, which puts the check here. Mounting
    on a secret published in this repository would mean an endpoint on the
    API's own port that anyone could mint a token for.
    """
    assert _mcp_status("true", "true", secret=secret) == ABSENT
