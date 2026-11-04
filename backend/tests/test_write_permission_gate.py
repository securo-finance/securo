"""A read-only member cannot write, proved over HTTP.

These assert **behaviour, not plumbing**. "A viewer cannot create a
transaction" stays true if the gate is later expressed per action, per
module or per view; "this route depends on `current_writable_workspace`"
would have to be rewritten by that change, and costs the same to write.

Why they did not exist before: the gate ships as a dependency wrapper
(`current_writable_workspace` → `ctx.require_write()`), so the string
`require_write` appears in exactly one file. An audit that grepped for it
concluded no route was gated, when 85 of them were. Service-level tests of
`require_membership` existed, but nothing exercised the wiring between a
route, the requester's role, and the 403 — which is the part that was
doubted.
"""
import uuid

import pytest
from httpx import AsyncClient

#: One representative mutation per resource family. Not exhaustive on
#: purpose: the meta-test in `test_write_permission_coverage.py` is what
#: guarantees breadth, and duplicating it here would be the same assertion
#: written a hundred times.
MUTATIONS = [
    ("transaction", "POST", "/api/transactions", {
        "account_id": None, "date": "2026-08-01", "description": "Should not exist",
        "amount": 10, "type": "expense",
    }),
    ("account", "POST", "/api/accounts", {
        "name": "Should not exist", "type": "checking", "currency": "BRL",
    }),
    ("category", "POST", "/api/categories", {"name": "Should not exist", "type": "expense"}),
    ("budget", "POST", "/api/budgets", {
        "category_id": None, "amount": 100, "period": "monthly",
        "start_date": "2026-08-01",
    }),
]


@pytest.mark.parametrize(
    "family,method,path,payload", MUTATIONS, ids=[m[0] for m in MUTATIONS]
)
async def test_a_viewer_is_refused_on_a_mutation(
    client: AsyncClient, viewer_auth_headers, test_account, test_categories,
    family, method, path, payload,
):
    body = dict(payload)
    if body.get("account_id", "absent") is None:
        body["account_id"] = str(test_account.id)
    if body.get("category_id", "absent") is None:
        body["category_id"] = str(test_categories[0].id)

    resp = await client.request(method, path, headers=viewer_auth_headers, json=body)
    assert resp.status_code == 403, (
        f"a viewer got {resp.status_code} creating a {family}: {resp.text}"
    )


async def test_the_refusal_says_why(client: AsyncClient, viewer_auth_headers):
    """A 403 with no reason sends the user to support. The role is the reason."""
    resp = await client.post(
        "/api/categories", headers=viewer_auth_headers,
        json={"name": "Should not exist", "type": "expense"},
    )
    assert resp.status_code == 403
    assert "read-only" in resp.json()["detail"].lower()


async def test_a_viewer_can_still_read(client: AsyncClient, viewer_auth_headers):
    """The gate must refuse writes without turning into a lockout — otherwise
    a 403 on everything would pass the tests above for the wrong reason."""
    for path in ("/api/transactions", "/api/accounts", "/api/categories", "/api/budgets"):
        resp = await client.get(path, headers=viewer_auth_headers)
        assert resp.status_code == 200, f"a viewer could not read {path}: {resp.text}"


async def test_the_write_gate_is_about_role_not_identity(
    client: AsyncClient, viewer_auth_headers, auth_headers
):
    """The same request the viewer was refused succeeds for the owner, so the
    403 is the role talking and not something wrong with the payload."""
    body = {"name": f"Owner may write {uuid.uuid4().hex[:6]}", "type": "expense"}
    refused = await client.post("/api/categories", headers=viewer_auth_headers, json=body)
    allowed = await client.post("/api/categories", headers=auth_headers, json=body)
    assert refused.status_code == 403
    assert allowed.status_code == 201, allowed.text


async def test_previewing_an_import_is_not_a_write(
    client: AsyncClient, viewer_auth_headers
):
    """The one deliberate exception, pinned so a later tightening is a
    decision rather than an accident: previewing parses an upload and
    persists nothing, so a read-only member may do it."""
    resp = await client.post(
        "/api/transactions/import/preview",
        headers=viewer_auth_headers,
        files={"file": ("t.csv", b"date,description,amount\n2026-08-01,Coffee,-5\n", "text/csv")},
    )
    assert resp.status_code != 403, "previewing was gated as a write"
