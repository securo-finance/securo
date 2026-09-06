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

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

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


@pytest.fixture
def client(dist: pathlib.Path) -> TestClient:
    """An app wired the way `app.main` wires it when the dist path is set."""
    app = FastAPI()

    @app.get("/api/health")
    async def health():
        return {"status": "healthy"}

    app.add_middleware(FrontendCacheHeadersMiddleware)
    app.frontend("/", directory=str(dist), check_dir=False)
    return TestClient(app)


# ---- the routing contract --------------------------------------------------


def test_api_routes_still_win(client: TestClient):
    assert client.get("/api/health").json() == {"status": "healthy"}


def test_unknown_api_path_is_still_a_json_404(client: TestClient):
    """The regression a root StaticFiles mount would have introduced.

    axios' response interceptor parses JSON; handing it the SPA shell with a
    200 would turn every 404 into a confusing render bug.
    """
    response = client.get("/api/nope", headers=AXIOS_ACCEPT)
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


def test_wrong_method_on_an_api_route_is_still_405(client: TestClient):
    assert client.post("/api/health", headers=AXIOS_ACCEPT).status_code == 405


def test_browser_navigation_gets_the_shell(client: TestClient):
    """A deep link like /transactions has no route; the SPA router handles it."""
    response = client.get("/transactions", headers=BROWSER_ACCEPT)
    assert response.status_code == 200
    assert 'id="root"' in response.text


def test_root_is_the_shell_whatever_the_accept_header(client: TestClient):
    response = client.get("/", headers={"Accept": "*/*"})
    assert response.status_code == 200
    assert 'id="root"' in response.text


def test_non_navigation_request_for_a_spa_route_is_a_404(client: TestClient):
    """Documented caveat, not a bug — but surprising enough to pin down.

    curl sends `Accept: */*`, so it gets a 404 where nginx's `try_files`
    returned the shell. Uptime probes must target /api/health instead.
    """
    assert client.get("/transactions", headers={"Accept": "*/*"}).status_code == 404


def test_hashed_assets_are_served(client: TestClient):
    response = client.get("/static/app-a1b2c3.js")
    assert response.status_code == 200
    assert response.text == "console.log(1)"


# ---- caching ---------------------------------------------------------------


def test_shell_is_never_cached(client: TestClient):
    """A cached shell keeps requesting asset hashes a deploy has replaced."""
    for path, headers in (("/", {"Accept": "*/*"}), ("/transactions", BROWSER_ACCEPT)):
        assert client.get(path, headers=headers).headers["cache-control"] == "no-store"


def test_hashed_assets_are_cached_forever(client: TestClient):
    response = client.get("/static/app-a1b2c3.js")
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_api_responses_are_left_alone(client: TestClient):
    """The middleware must not start dictating caching for the API."""
    assert "cache-control" not in client.get("/api/health").headers


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
