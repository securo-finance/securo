"""Fixtures for the API contract sweep.

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
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import AsyncClient

from tests.api_contract.world import build_world

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


@pytest_asyncio.fixture
async def world(
    client: AsyncClient,
    session,
    test_workspace,
    auth_headers,
    test_connection,
    test_agent,
    test_conversation,
) -> dict:
    # Business workspaces get every module (personal lacks invoicing), so
    # the sweep reaches the invoice/product/schedule routes.
    test_workspace.kind = BUSINESS_KIND
    await session.commit()
    return await build_world(
        client,
        auth_headers,
        {
            "connection_id": str(test_connection.id),
            "agent_id": str(test_agent.id),
            "conversation_id": str(test_conversation.id),
        },
    )


@pytest.fixture(autouse=True)
def _contract_environment(monkeypatch, tmp_path, _mock_redis):
    """Close the gaps between the shared test setup and a real deployment.

    The shared `_mock_redis` patches the modules that existed when it was
    written; OIDC keeps its login state in Redis too. Knowledge uploads
    write under /app/data, which only exists in the container.
    """
    from app.agents.config import get_agent_settings

    async def _fake_get_redis():
        return _mock_redis

    monkeypatch.setattr("app.api.oidc_auth.get_redis", _fake_get_redis)
    monkeypatch.setattr(get_agent_settings(), "knowledge_storage_path", str(tmp_path / "knowledge"))


@pytest.fixture(autouse=True)
def _no_outbound_network(monkeypatch):
    real_connect = socket.socket.connect

    def guarded(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            raise OSError("outbound network is disabled in the API contract sweep")
        return real_connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)
