"""API contract sweep: every route, every method, exact expected status.

Expected statuses live in baseline.json. When a contract case fails, the
route's behaviour changed: either the change is intentional (regenerate the
baseline in the same PR so the diff documents it) or it is a regression (fix
the code). See tests/api_contract/README.md.

This file holds the checks on the sweep itself; the cases run from the
test_contract_*.py shards.
"""
import pytest

from tests.api_contract.cases import DELIBERATE_500, OPERATIONS, POSTGRES_ONLY, VARIANT_OK, VARIANTS
from tests.api_contract.conftest import updating_baseline
from tests.api_contract.harness import BASELINE, UNHANDLED


@pytest.mark.asyncio
async def test_world_builds(world):
    failures = "\n".join(f"{key}: {why}" for key, why in world["_failed"].items())
    assert not world["_failed"], f"world steps failed:\n{failures}"


def test_baseline_covers_every_operation():
    """A route added or removed without updating the baseline fails here."""
    if updating_baseline():
        pytest.skip("baseline is being regenerated")
    expected = {op.key for op in OPERATIONS if (op.method, op.path) not in POSTGRES_ONLY}
    recorded = set(BASELINE.get(VARIANT_OK, {})) - {f"{m} {p}" for m, p in POSTGRES_ONLY}
    assert expected - recorded == set(), f"routes with no baseline: {sorted(expected - recorded)}"
    assert recorded - expected == set(), f"baseline routes that no longer exist: {sorted(recorded - expected)}"


def test_baseline_has_no_unhandled_errors():
    """The baseline describes correct behaviour, so it may never expect a 500."""
    if updating_baseline():
        pytest.skip("baseline is being regenerated")
    bad = {
        f"{variant}:{key}": status
        for variant in VARIANTS
        for key, status in BASELINE.get(variant, {}).items()
        if status == UNHANDLED and tuple(key.split(" ", 1)) not in DELIBERATE_500
    }
    assert bad == {}, f"baseline expects unhandled errors: {bad}"
