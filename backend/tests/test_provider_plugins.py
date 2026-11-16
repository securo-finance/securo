"""Provider plugins: integrations that return records for the bank sync pipeline (#1461).

The runner is the real one, in process, as in `test_code_plugins`; the
plugin's `sync` returns `data` and Securo writes it as a connected bank.
"""
from __future__ import annotations

import copy
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.plugins import service
from app.plugins.manifest import parse_manifest
from tests.test_code_plugins import (  # noqa: F401 - fixtures
    MANIFEST,
    PLUGIN,
    catalog,
    ids,
    plugin_runner,
    states,
    sync_locks,
)


def _provider_manifest(**integration: Any) -> dict[str, Any]:
    manifest = copy.deepcopy(MANIFEST)
    manifest["contributes"]["integrations"][0].update(
        {"provides": ["accounts", "transactions", "holdings", "trades"], **integration}
    )
    return manifest


# --- The manifest -------------------------------------------------------------


def test_an_integration_declares_what_it_provides():
    manifest = parse_manifest(_provider_manifest())
    keys, signin = manifest.contributes.integrations
    assert keys.provides == ["accounts", "transactions", "holdings", "trades"]
    assert keys.is_provider
    assert signin.provides is None and not signin.is_provider


@pytest.mark.parametrize(
    "provides",
    [[], ["transactions"], ["bills"], ["trades"], ["accounts", "accounts"], ["accounts", "payees"]],
    ids=["empty", "transactions-without-accounts", "bills-without-accounts", "trades-without-holdings",
         "duplicate", "unknown-kind"],
)
def test_provides_must_hang_together(provides):
    with pytest.raises(ValidationError):
        parse_manifest(_provider_manifest(provides=provides))


def test_provides_and_api_scopes_can_be_declared_together():
    manifest = parse_manifest(_provider_manifest())
    assert manifest.api_scopes and manifest.contributes.integrations[0].provides


@pytest.mark.asyncio
async def test_the_consent_shows_what_an_integration_provides(session: AsyncSession, ids, catalog, plugin_runner):
    catalog.manifests[catalog.commit] = _provider_manifest()
    workspace_id, _ = ids
    plan = await service.plan(session, workspace_id, PLUGIN, "install", lang="en")
    assert plan.code is not None
    by_key = {i["key"]: i for i in plan.code.integrations}
    assert by_key["keys"]["provides"] == ["accounts", "transactions", "holdings", "trades"]
    assert by_key["signin"]["provides"] is None
