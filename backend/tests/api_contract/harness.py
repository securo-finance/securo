"""Shared case runner for the contract test files.

The sweep is split over several small test files on purpose: CI runs with
`--dist loadfile`, which keeps a whole file on one worker, so one big file
would serialize the whole sweep. `params_for` hands each file its shard.
"""
import pytest
from httpx import AsyncClient

from tests.api_contract.cases import (
    ADMIN_PREFIXES,
    DELIBERATE_500,
    KNOWN_BUGS,
    OPERATIONS,
    POSTGRES_ONLY,
    VARIANT_ANON,
    VARIANT_EMPTY,
    VARIANT_OK,
    VARIANT_UNKNOWN_ID,
    VARIANT_VIEWER,
    Operation,
    fill_path,
    request_kwargs,
    variants_for,
)
from tests.api_contract.conftest import load_baseline, record, updating_baseline

# 500 is the one status no route may answer by design: it means an
# unhandled exception. 502/503 are deliberate "provider unavailable" answers.
UNHANDLED = 500
NO_CONTENT = 204
BASELINE = load_baseline()


def params_for(variants: tuple[str, ...], shard: int = 0, shards: int = 1) -> list:
    params = []
    for index, op in enumerate(OPERATIONS):
        if index % shards != shard:
            continue
        for variant in variants_for(op):
            if variant not in variants:
                continue
            marks = []
            bug = KNOWN_BUGS.get((variant, op.method, op.path))
            if bug and not updating_baseline():
                marks.append(pytest.mark.xfail(strict=True, reason=bug.reason))
            sqlite_gap = POSTGRES_ONLY.get((op.method, op.path))
            if sqlite_gap and variant == VARIANT_OK:
                marks.append(pytest.mark.skip(reason=sqlite_gap))
            params.append(pytest.param(variant, op, id=f"{variant}:{op.key}", marks=marks))
    return params


def headers_for(variant: str, op: Operation, auth: dict, admin: dict, viewer: dict | None = None) -> dict:
    if variant == VARIANT_ANON:
        return {}
    if variant == VARIANT_VIEWER:
        assert viewer is not None
        return viewer
    if op.path.startswith(ADMIN_PREFIXES):
        return admin
    return auth


async def _send(client: AsyncClient, op: Operation, path: str, headers: dict, kwargs: dict):
    """Send the request; an exception escaping the app counts as a 500."""
    try:
        resp = await client.request(op.method, path, headers=headers, **kwargs)
    except Exception as exc:  # noqa: BLE001 (any escape is the finding)
        return UNHANDLED, f"{type(exc).__name__}: {exc}", None
    return resp.status_code, resp.text[:500], resp


async def check_case(client: AsyncClient, variant: str, op: Operation, world: dict, headers: dict) -> None:
    path = fill_path(op, world, unknown_ids=variant == VARIANT_UNKNOWN_ID)
    kwargs = request_kwargs(op, world, empty_body=variant == VARIANT_EMPTY)

    status, detail, resp = await _send(client, op, path, headers, kwargs)

    if updating_baseline():
        bug = KNOWN_BUGS.get((variant, op.method, op.path))
        record(variant, op.key, bug.expected if bug else status, detail)
        return

    expected = BASELINE.get(variant, {}).get(op.key)
    assert expected is not None, (
        f"no baseline for {variant}:{op.key}; regenerate with UPDATE_API_CONTRACT=1"
    )
    if (op.method, op.path) not in DELIBERATE_500:
        assert status != UNHANDLED, f"{op.method} {path} [{variant}] unhandled error: {detail}"
    assert status == expected, (
        f"{op.method} {path} [{variant}] expected {expected}, got {status}: {detail}"
    )
    if (
        variant == VARIANT_OK
        and resp is not None
        and status < 300
        and status != NO_CONTENT
        and resp.content
        and "json" in resp.headers.get("content-type", "")
    ):
        resp.json()
