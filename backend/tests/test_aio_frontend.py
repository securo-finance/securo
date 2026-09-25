"""The API serving the SPA in-process, for the all-in-one image.

Two things are worth pinning down here. First, that turning this on cannot
change what the API does for anyone who leaves it off — the published backend
image has no frontend build in it and must behave exactly as before. Second,
the routing contract: the reason `app.frontend()` is used instead of mounting
StaticFiles at "/" is that a root Mount matches every path, which would answer
an unknown /api/... with index.html and HTTP 200 where it is a JSON 404 today.
That is a silent, app-breaking regression, so it gets a test rather than a
comment.
"""

import pathlib

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from app.core.spa import FrontendCacheHeadersMiddleware

#: What axios sends (see frontend/src/lib/api.ts) versus what a browser sends
#: on navigation. The frontend route's index.html fallback keys off exactly
#: this difference.
AXIOS_ACCEPT = {"Accept": "application/json, text/plain, */*"}
BROWSER_ACCEPT = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9"}


@pytest.fixture
def dist(tmp_path: pathlib.Path) -> pathlib.Path:
    """A minimal stand-in for `frontend/dist`.

    `static/` rather than `assets/` because frontend/vite.config.ts pins
    `build.assetsDir` to it (issue #295).
    """
    (tmp_path / "static").mkdir()
    (tmp_path / "index.html").write_text('<!doctype html><div id="root"></div>')
    (tmp_path / "static" / "app-a1b2c3.js").write_text("console.log(1)")
    return tmp_path


@pytest_asyncio.fixture
async def client(dist: pathlib.Path):
    """An app wired the way `app.main` wires it when the dist path is set.

    Driven through httpx's ASGI transport, like the suite's own client
    fixture — `starlette.testclient` now wants httpx2 and warns when it does
    not find it, which CI turns into a collection error.
    """
    app = FastAPI()

    @app.get("/api/health")
    async def health():
        return {"status": "healthy"}

    app.add_middleware(FrontendCacheHeadersMiddleware)
    app.frontend("/", directory=str(dist), check_dir=False)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---- the routing contract --------------------------------------------------


async def test_api_routes_still_win(client: httpx.AsyncClient):
    assert (await client.get("/api/health")).json() == {"status": "healthy"}


async def test_unknown_api_path_is_still_a_json_404(client: httpx.AsyncClient):
    """The regression a root StaticFiles mount would have introduced.

    axios' response interceptor parses JSON; handing it the SPA shell with a
    200 would turn every 404 into a confusing render bug.
    """
    response = await client.get("/api/nope", headers=AXIOS_ACCEPT)
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


async def test_wrong_method_on_an_api_route_is_still_405(client: httpx.AsyncClient):
    assert (await client.post("/api/health", headers=AXIOS_ACCEPT)).status_code == 405


async def test_browser_navigation_gets_the_shell(client: httpx.AsyncClient):
    """A deep link like /transactions has no route; the SPA router handles it."""
    response = await client.get("/transactions", headers=BROWSER_ACCEPT)
    assert response.status_code == 200
    assert 'id="root"' in response.text


async def test_root_is_the_shell_whatever_the_accept_header(client: httpx.AsyncClient):
    response = await client.get("/", headers={"Accept": "*/*"})
    assert response.status_code == 200
    assert 'id="root"' in response.text


async def test_non_navigation_request_for_a_spa_route_is_a_404(client: httpx.AsyncClient):
    """Documented caveat, not a bug — but surprising enough to pin down.

    curl sends `Accept: */*`, so it gets a 404 where nginx's `try_files`
    returned the shell. Uptime probes must target /api/health instead.
    """
    assert (await client.get("/transactions", headers={"Accept": "*/*"})).status_code == 404


async def test_hashed_assets_are_served(client: httpx.AsyncClient):
    response = await client.get("/static/app-a1b2c3.js")
    assert response.status_code == 200
    assert response.text == "console.log(1)"


# ---- caching ---------------------------------------------------------------


async def test_shell_is_never_cached(client: httpx.AsyncClient):
    """A cached shell keeps requesting asset hashes a deploy has replaced."""
    for path, headers in (("/", {"Accept": "*/*"}), ("/transactions", BROWSER_ACCEPT)):
        assert (await client.get(path, headers=headers)).headers["cache-control"] == "no-store"


async def test_hashed_assets_are_cached_forever(client: httpx.AsyncClient):
    response = await client.get("/static/app-a1b2c3.js")
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


async def test_api_responses_are_left_alone(client: httpx.AsyncClient):
    """The middleware must not start dictating caching for the API."""
    assert "cache-control" not in (await client.get("/api/health")).headers


# ---- the default: off ------------------------------------------------------


def test_the_real_app_serves_no_frontend_by_default():
    """`frontend_dist_path` defaults to empty, so the backend image is untouched.

    A default path would let the published image start serving whatever
    happened to sit there.
    """
    from app.core.config import Settings
    from app.main import app as real_app

    assert Settings().frontend_dist_path == ""
    assert not any(type(route).__name__ == "_FrontendRoute" for route in real_app.routes)
