"""Fixtures for the API contract sweep.

- The world is seeded once per worker process and restored before every
  case that asks for it, so each case starts from the same world and a
  DELETE in one case can't leak into the next.
- Outbound network is blocked, so routes that call a provider (bank sync,
  market quotes, OIDC, LLMs) answer with their own error handling instead
  of reaching the internet. That handling is part of the contract.
- With UPDATE_API_CONTRACT=1 the observed statuses are written back to
  baseline.json at the end of the run (serial runs only). Add
  API_CONTRACT_DETAILS=<file> to also dump why `ok` cases missed a 2xx.
"""
import json
import os
import socket
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

import aiosqlite
import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.core.config import get_settings
from tests.api_contract.world import build_world
from tests.conftest import engine

BUSINESS_KIND = "business"
BASELINE_PATH = Path(__file__).with_name("baseline.json")
UPDATE_ENV = "UPDATE_API_CONTRACT"
# Optional: a file path. In update mode, every `ok` case that did not reach
# a 2xx appends its response detail there, to see why a happy path fails.
DETAILS_ENV = "API_CONTRACT_DETAILS"
_recorded: dict[str, dict[str, int]] = {}


def updating_baseline() -> bool:
    return os.environ.get(UPDATE_ENV) == "1"


def load_baseline() -> dict[str, dict[str, int]]:
    if not BASELINE_PATH.exists():
        return {}
    return json.loads(BASELINE_PATH.read_text())


def record(variant: str, key: str, status: int, detail: str = "") -> None:
    _recorded.setdefault(variant, {})[key] = status
    details_path = os.environ.get(DETAILS_ENV)
    if details_path and variant == "ok" and status >= 300:
        with open(details_path, "a") as fh:
            fh.write(json.dumps({"key": key, "status": status, "detail": detail}) + "\n")


def pytest_configure(config):
    if updating_baseline() and os.environ.get("PYTEST_XDIST_WORKER"):
        raise pytest.UsageError(f"{UPDATE_ENV}=1 must run serially: add -n 0")


def pytest_sessionfinish(session, exitstatus):
    if not updating_baseline() or not _recorded:
        return
    merged = load_baseline()
    for variant, statuses in _recorded.items():
        merged.setdefault(variant, {}).update(statuses)
    ordered = {v: dict(sorted(s.items())) for v, s in sorted(merged.items())}
    BASELINE_PATH.write_text(json.dumps(ordered, indent=1) + "\n")


class WorldHeaders(NamedTuple):
    owner: dict
    admin: dict
    viewer: dict


@dataclass(frozen=True)
class WorldSnapshot:
    world: dict
    headers: WorldHeaders
    # Attachments and archived invoice PDFs live in storage, not the database.
    files: dict[Path, bytes]


@dataclass
class _WorldStore:
    image: aiosqlite.Connection
    snapshot: WorldSnapshot | None = None


@asynccontextmanager
async def _test_database() -> AsyncGenerator[aiosqlite.Connection, None]:
    async with engine.connect() as conn:
        raw = await conn.get_raw_connection()
        driver = raw.driver_connection
        assert isinstance(driver, aiosqlite.Connection)
        yield driver


def _storage_root() -> Path:
    return Path(get_settings().storage_local_path)


@pytest_asyncio.fixture(scope="session")
async def _world_store() -> AsyncGenerator[_WorldStore, None]:
    async with aiosqlite.connect(":memory:", check_same_thread=False) as image:
        yield _WorldStore(image)


@pytest_asyncio.fixture
async def _seeded_world(
    _world_store: _WorldStore,
    client: AsyncClient,
    session,
    test_workspace,
    auth_headers,
    admin_auth_headers,
    viewer_auth_headers,
    test_connection,
    test_agent,
    test_conversation,
) -> WorldSnapshot:
    # Business workspaces get every module (personal lacks invoicing), so
    # the sweep reaches the invoice/product/schedule routes.
    test_workspace.kind = BUSINESS_KIND
    await session.commit()
    world = await build_world(
        client,
        auth_headers,
        {
            "connection_id": str(test_connection.id),
            "agent_id": str(test_agent.id),
            "conversation_id": str(test_conversation.id),
        },
    )
    async with _test_database() as live:
        await live.backup(_world_store.image)
    root = _storage_root()
    files = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    headers = WorldHeaders(auth_headers, admin_auth_headers, viewer_auth_headers)
    return WorldSnapshot(world, headers, files)


@pytest.fixture
def _world_snapshot(request: pytest.FixtureRequest, _world_store: _WorldStore) -> WorldSnapshot:
    # The user, workspace and token fixtures are function-scoped, so the
    # first case on a worker seeds through them and later cases reuse the
    # result. Tokens last 24h, well past any run.
    if _world_store.snapshot is None:
        _world_store.snapshot = request.getfixturevalue("_seeded_world")
    return _world_store.snapshot


@pytest_asyncio.fixture
async def world(_world_store: _WorldStore, _world_snapshot: WorldSnapshot) -> dict:
    # The backup replaces every page of the database, sequences included, and
    # each request opens its own session, so no identity map outlives it. The
    # rest a case can touch is per test already: the Redis mock behind rate
    # limits and 2FA, the timezone cache, the provider registry, monkeypatched
    # settings, and the storage directory refilled below.
    async with _test_database() as live:
        await _world_store.image.backup(live)
    root = _storage_root()
    for relative, data in _world_snapshot.files.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_bytes(data)
    return dict(_world_snapshot.world)


@pytest.fixture
def world_headers(_world_snapshot: WorldSnapshot) -> WorldHeaders:
    return _world_snapshot.headers


@pytest.fixture(autouse=True)
def _contract_environment(monkeypatch, tmp_path, _mock_redis):
    """Close the gaps between the shared test setup and a real deployment.

    The shared `_mock_redis` patches the modules that existed when it was
    written; OIDC keeps its login state in Redis too. Knowledge uploads
    write under /app/data, which only exists in the container. Stored files
    go to a fresh directory per case, which `world` refills from the snapshot.
    """
    from app.agents.config import get_agent_settings

    async def _fake_get_redis():
        return _mock_redis

    def _broker_unreachable(*args, **kwargs):
        raise OSError("outbound network is disabled in the API contract sweep")

    monkeypatch.setattr("app.api.oidc_auth.get_redis", _fake_get_redis)
    monkeypatch.setattr(get_agent_settings(), "knowledge_storage_path", str(tmp_path / "knowledge"))
    monkeypatch.setattr(get_settings(), "storage_local_path", str(tmp_path / "storage"))
    # Celery retries the blocked Redis connection for ~19s before giving up.
    # Fail the dispatch at once: the route handles any dispatch error alike.
    monkeypatch.setattr("app.worker.celery_app.send_task", _broker_unreachable)


@pytest.fixture(autouse=True)
def _no_outbound_network(monkeypatch):
    real_connect = socket.socket.connect

    def guarded(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            raise OSError("outbound network is disabled in the API contract sweep")
        return real_connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)
