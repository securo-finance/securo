"""PR-2 review workflow — endpoint, filter, count and invariant tests (§8).

Covers the named §8 cases in `test_transaction_review.py` plus the two
gate §15-A1 cases (`test_legacy_reviewed_rows_allow_null_provenance`,
`test_review_stamps_never_half_populated`) and the T2 exit check that
`BulkReviewRequest` rejects empty id lists with 400.

Assertions are made both on the HTTP response (what the UI sees) and —
for the stamp invariant — after `session.refresh` (what the DB holds).
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.transaction import Transaction
from app.models.user import User
from app.models.workspace import Workspace


async def _make_tx(
    session: AsyncSession,
    test_user: User,
    test_account: Account,
    test_workspace: Workspace,
    *,
    description: str,
    review_status: str = "needs_review",
    is_ignored: bool = False,
    category_id=None,
) -> Transaction:
    """A ledger row in a known review state, as ingest would leave it."""
    tx = Transaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=test_account.id,
        category_id=category_id,
        description=description,
        amount=Decimal("10.00"),
        date=date.today(),
        type="debit",
        source="import",
        review_status=review_status,
        is_ignored=is_ignored,
        created_at=datetime.now(timezone.utc),
    )
    session.add(tx)
    await session.commit()
    await session.refresh(tx)
    return tx


def _assert_stamps_paired(tx) -> None:
    """§15-A1: never exactly one provenance stamp.

    Accepts a model instance or a serialized response body (both shapes
    appear in this file).
    """
    def _get(key):
        return tx.get(key) if isinstance(tx, dict) else getattr(tx, key)
    at, by, status = _get("reviewed_at"), _get("reviewed_by_id"), _get("review_status")
    assert (at is None) == (by is None), (
        f"half-populated stamps: reviewed_at={at!r} reviewed_by_id={by!r}"
    )
    if status == "needs_review":
        assert at is None and by is None


@pytest.mark.asyncio
async def test_patch_review_endpoint_marks_reviewed_stamps(
    client: AsyncClient, auth_headers, session, test_user, test_account, test_workspace
):
    tx = await _make_tx(session, test_user, test_account, test_workspace, description="Mark me")

    resp = await client.patch(
        f"/api/transactions/{tx.id}/review",
        headers=auth_headers,
        json={"review_status": "reviewed"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["review_status"] == "reviewed"
    assert body["reviewed_at"] is not None
    assert body["reviewed_by_id"] == str(test_user.id)

    # And in the database, not just the response.
    await session.refresh(tx)
    assert tx.review_status == "reviewed"
    assert tx.reviewed_at is not None
    assert tx.reviewed_by_id == test_user.id
    _assert_stamps_paired(tx)


@pytest.mark.asyncio
async def test_patch_review_endpoint_unreview_clears(
    client: AsyncClient, auth_headers, session, test_user, test_account, test_workspace
):
    tx = await _make_tx(session, test_user, test_account, test_workspace, description="Review then clear")
    mark = await client.patch(
        f"/api/transactions/{tx.id}/review",
        headers=auth_headers,
        json={"review_status": "reviewed"},
    )
    assert mark.status_code == 200, mark.text

    resp = await client.patch(
        f"/api/transactions/{tx.id}/review",
        headers=auth_headers,
        json={"review_status": "needs_review"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["review_status"] == "needs_review"
    assert body["reviewed_at"] is None
    assert body["reviewed_by_id"] is None

    await session.refresh(tx)
    assert tx.review_status == "needs_review"
    assert tx.reviewed_at is None and tx.reviewed_by_id is None


@pytest.mark.asyncio
async def test_review_toggle_is_idempotent(
    client: AsyncClient, auth_headers, session, test_user, test_account, test_workspace
):
    """Re-marking an already-reviewed row must not restamp `reviewed_at` —
    otherwise the stamp stops meaning "when this was reviewed" and every
    rule re-run would rewrite history."""
    tx = await _make_tx(session, test_user, test_account, test_workspace, description="Idempotent")
    first = await client.patch(
        f"/api/transactions/{tx.id}/review",
        headers=auth_headers,
        json={"review_status": "reviewed"},
    )
    assert first.status_code == 200, first.text
    stamp_after_first = first.json()["reviewed_at"]
    assert stamp_after_first is not None

    second = await client.patch(
        f"/api/transactions/{tx.id}/review",
        headers=auth_headers,
        json={"review_status": "reviewed"},
    )
    assert second.status_code == 200, second.text
    body = second.json()
    assert body["review_status"] == "reviewed"
    assert body["reviewed_at"] == stamp_after_first, "re-marking restamped reviewed_at"


@pytest.mark.asyncio
async def test_bulk_review_endpoint_mixed_states(
    client: AsyncClient, auth_headers, session, test_user, test_account, test_workspace
):
    """Bulk both directions over a mixed selection; per-row stamps and the
    response counts must both be honest (§8)."""
    a = await _make_tx(session, test_user, test_account, test_workspace, description="A needs")
    b = await _make_tx(session, test_user, test_account, test_workspace, description="B needs")
    c = await _make_tx(
        session, test_user, test_account, test_workspace,
        description="C already reviewed", review_status="reviewed",
    )
    # Give C an actor stamp so "no restamp" is observable. Refresh after
    # commit: `reviewed_at` round-trips through the DB as a timezone-less
    # timestamp, so the captured value must be the stored shape.
    c.reviewed_at = datetime.now(timezone.utc)
    c.reviewed_by_id = test_user.id
    await session.commit()
    await session.refresh(c)
    original_c_stamp = c.reviewed_at
    assert original_c_stamp is not None

    resp = await client.patch(
        "/api/transactions/bulk-review",
        headers=auth_headers,
        json={
            "transaction_ids": [str(a.id), str(b.id), str(c.id)],
            "review_status": "reviewed",
        },
    )
    assert resp.status_code == 200, resp.text
    counts = resp.json()
    assert counts["updated"] == 2  # a, b transition; c was already there
    assert counts["matched"] == 3

    for row in (a, b):
        await session.refresh(row)
        assert row.review_status == "reviewed"
        assert row.reviewed_at is not None
        assert row.reviewed_by_id == test_user.id
        _assert_stamps_paired(row)
    await session.refresh(c)
    assert c.reviewed_at == original_c_stamp, "bulk restamped an already-reviewed row"

    # Back the other way.
    unreview = await client.patch(
        "/api/transactions/bulk-review",
        headers=auth_headers,
        json={
            "transaction_ids": [str(a.id), str(b.id), str(c.id)],
            "review_status": "needs_review",
        },
    )
    assert unreview.status_code == 200, unreview.text
    assert unreview.json() == {"updated": 3, "matched": 3}
    for row in (a, b, c):
        await session.refresh(row)
        assert row.review_status == "needs_review"
        _assert_stamps_paired(row)


@pytest.mark.asyncio
async def test_bulk_review_requires_write_role(
    client: AsyncClient, viewer_auth_headers, session, test_user, test_account, test_workspace
):
    """Viewer → 403 (pattern: tests/test_write_permission_gate.py)."""
    tx = await _make_tx(session, test_user, test_account, test_workspace, description="Viewer tries")
    resp = await client.patch(
        "/api/transactions/bulk-review",
        headers=viewer_auth_headers,
        json={
            "transaction_ids": [str(tx.id)],
            "review_status": "reviewed",
        },
    )
    assert resp.status_code == 403, resp.text

    single = await client.patch(
        f"/api/transactions/{tx.id}/review",
        headers=viewer_auth_headers,
        json={"review_status": "reviewed"},
    )
    assert single.status_code == 403, single.text


@pytest.mark.asyncio
async def test_bulk_review_rejects_empty_ids(client: AsyncClient, auth_headers):
    """T2 exit check: an empty id list is a client error (400), not a
    successful no-op behind a toast."""
    resp = await client.patch(
        "/api/transactions/bulk-review",
        headers=auth_headers,
        json={"transaction_ids": [], "review_status": "reviewed"},
    )
    assert resp.status_code == 400, resp.text


@pytest.mark.asyncio
async def test_generic_patch_review_status_stamps_invariant(
    client: AsyncClient, auth_headers, session, test_user, test_account, test_workspace
):
    """The generic PATCH /transactions/{id} must maintain §4.2 through the
    chokepoint, never the generic setattr loop (risk R4)."""
    tx = await _make_tx(session, test_user, test_account, test_workspace, description="Generic patch")

    up = await client.patch(
        f"/api/transactions/{tx.id}",
        headers=auth_headers,
        json={"review_status": "reviewed"},
    )
    assert up.status_code == 200, up.text
    body = up.json()
    assert body["review_status"] == "reviewed"
    assert body["reviewed_at"] is not None
    assert body["reviewed_by_id"] == str(test_user.id)

    down = await client.patch(
        f"/api/transactions/{tx.id}",
        headers=auth_headers,
        json={"review_status": "needs_review"},
    )
    assert down.status_code == 200, down.text
    body = down.json()
    assert body["review_status"] == "needs_review"
    assert body["reviewed_at"] is None
    assert body["reviewed_by_id"] is None

    await session.refresh(tx)
    _assert_stamps_paired(tx)


@pytest.mark.asyncio
async def test_list_filter_review_status_param(
    client: AsyncClient, auth_headers, session, test_user, test_account, test_workspace
):
    n1 = await _make_tx(session, test_user, test_account, test_workspace, description="NR1")
    await _make_tx(session, test_user, test_account, test_workspace, description="NR2")

    resp = await client.get(
        "/api/transactions?review_status=needs_review", headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] == 2
    assert {item["description"] for item in data["items"]} == {"NR1", "NR2"}
    assert all(item["review_status"] == "needs_review" for item in data["items"])

    done = await client.get(
        "/api/transactions?review_status=reviewed", headers=auth_headers
    )
    assert done.status_code == 200, done.text
    assert all(item["review_status"] == "reviewed" for item in done.json()["items"])

    # Closed set at the query layer too.
    bad = await client.get(
        "/api/transactions?review_status=bogus", headers=auth_headers
    )
    assert bad.status_code == 422

    # Sanity: filter and unfiltered list agree on the same rows.
    both = await client.get("/api/transactions", headers=auth_headers)
    assert both.json()["total"] == data["total"] + done.json()["total"]
    assert n1.review_status == "needs_review"


@pytest.mark.asyncio
async def test_needs_review_count_under_combined_filters(
    client: AsyncClient, auth_headers, session, test_user, test_account, test_workspace,
    test_categories, test_transactions,
):
    """Envelope count = needs_review rows under the CURRENT filters (the
    number a click on the chip would show), independent of the active
    review_status filter (§5.3 / Q9)."""
    cat = test_categories[0]
    await _make_tx(session, test_user, test_account, test_workspace, description="NR uncategorized")
    await _make_tx(
        session, test_user, test_account, test_workspace,
        description="NR categorized", category_id=cat.id,
    )

    base = await client.get("/api/transactions", headers=auth_headers)
    assert base.status_code == 200, base.text
    body = base.json()
    assert body["needs_review_count"] == 2
    # Fixture rows (5, reviewed by default) + the 2 created above.
    assert body["total"] == 7
    assert len(body["items"]) == 7

    filtered = await client.get(
        f"/api/transactions?category_id={cat.id}", headers=auth_headers
    )
    assert filtered.status_code == 200, filtered.text
    assert filtered.json()["needs_review_count"] == 1

    # With the review filter ON the count is still the needs-review number
    # under the other filters — i.e. what clicking the chip would show.
    reviewed_only = await client.get(
        "/api/transactions?review_status=reviewed", headers=auth_headers
    )
    assert reviewed_only.status_code == 200, reviewed_only.text
    rbody = reviewed_only.json()
    assert rbody["total"] == 5
    assert rbody["needs_review_count"] == 2


@pytest.mark.asyncio
async def test_ignore_and_review_are_orthogonal(
    client: AsyncClient, auth_headers, session, test_user, test_account, test_workspace
):
    """§4.3 property: all four is_ignored × review_status combinations
    round-trip, and review transitions change no monetary total."""
    # The design constraint, encoded as a source scan: no review field is
    # referenced by the monetary query filters.
    filters_src = (
        Path(__file__).resolve().parents[1] / "app" / "services" / "_query_filters.py"
    ).read_text(encoding="utf-8")
    assert "review" not in filters_src.lower(), (
        "_query_filters.py references a review field — review leaked into "
        "monetary computation"
    )

    combo = {}
    for ignored in (False, True):
        for status in ("needs_review", "reviewed"):
            combo[(ignored, status)] = await _make_tx(
                session, test_user, test_account, test_workspace,
                description=f"ignored={ignored} status={status}",
                review_status=status,
                is_ignored=ignored,
            )

    # Totals before any review transition.
    before = await client.get("/api/transactions", headers=auth_headers)
    assert before.status_code == 200, before.text
    summary_before = before.json()["summary"]

    # Flip every row's review state; every combination survives.
    for (ignored, status), tx in combo.items():
        target = "reviewed" if status == "needs_review" else "needs_review"
        resp = await client.patch(
            f"/api/transactions/{tx.id}/review",
            headers=auth_headers,
            json={"review_status": target},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["review_status"] == target
        assert body["is_ignored"] is ignored, "review transition changed is_ignored"
        _assert_stamps_paired(body)

    after = await client.get("/api/transactions", headers=auth_headers)
    summary_after = after.json()["summary"]
    assert summary_before == summary_after, (
        "review transition changed income/expense/net/excluded totals"
    )

    # Each row still carries its own independent combination back:
    # hide-ignored drops exactly the ignored rows, whatever their review
    # state is.
    resp = await client.get("/api/transactions?exclude_ignored=true", headers=auth_headers)
    visible = {item["description"] for item in resp.json()["items"]}
    for (ignored, status), tx in combo.items():
        desc = f"ignored={ignored} status={status}"
        assert (desc in visible) == (not ignored), desc


@pytest.mark.asyncio
async def test_flagged_toggle_independent(
    client: AsyncClient, auth_headers, session, test_user, test_account, test_workspace
):
    """The #56 flag flips without touching any review field (§1 non-goal
    boundary: bookmark only)."""
    tx = await _make_tx(session, test_user, test_account, test_workspace, description="Flag me")

    on = await client.patch(
        f"/api/transactions/{tx.id}", headers=auth_headers, json={"flagged": True}
    )
    assert on.status_code == 200, on.text
    body = on.json()
    assert body["flagged"] is True
    assert body["review_status"] == "needs_review"
    assert body["reviewed_at"] is None
    assert body["reviewed_by_id"] is None

    # Now a flagged, reviewed row: flag must not disturb the stamps.
    mark = await client.patch(
        f"/api/transactions/{tx.id}/review",
        headers=auth_headers,
        json={"review_status": "reviewed"},
    )
    stamp = mark.json()["reviewed_at"]
    off = await client.patch(
        f"/api/transactions/{tx.id}", headers=auth_headers, json={"flagged": False}
    )
    assert off.status_code == 200, off.text
    body = off.json()
    assert body["flagged"] is False
    assert body["review_status"] == "reviewed"
    assert body["reviewed_at"] == stamp
    assert body["reviewed_by_id"] == str(test_user.id)


@pytest.mark.asyncio
async def test_legacy_reviewed_rows_allow_null_provenance(
    client: AsyncClient, auth_headers, session, test_user, test_account, test_workspace
):
    """Gate §15-A1: rows that arrived `reviewed` by the DB default (legacy
    backfill, exempt synthetics) may keep BOTH stamps NULL — and we never
    fabricate a reviewer for them, even when explicitly re-marked."""
    legacy = Transaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=test_account.id,
        description="Legacy row from before PR-2",
        amount=Decimal("5.00"),
        date=date.today(),
        type="debit",
        source="manual",
        created_at=datetime.now(timezone.utc),
    )
    session.add(legacy)
    await session.commit()
    await session.refresh(legacy)
    # Model default applies: reviewed with no provenance, both NULL.
    assert legacy.review_status == "reviewed"
    _assert_stamps_paired(legacy)

    # It reads back as reviewed with NULL stamps...
    got = await client.get(f"/api/transactions/{legacy.id}", headers=auth_headers)
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["review_status"] == "reviewed"
    assert body["reviewed_at"] is None
    assert body["reviewed_by_id"] is None

    # ...and explicitly re-marking it reviewed is a stamp no-op: no
    # fabricated reviewer appears on a historic row.
    again = await client.patch(
        f"/api/transactions/{legacy.id}/review",
        headers=auth_headers,
        json={"review_status": "reviewed"},
    )
    assert again.status_code == 200, again.text
    body = again.json()
    assert body["reviewed_at"] is None
    assert body["reviewed_by_id"] is None


@pytest.mark.asyncio
async def test_review_stamps_never_half_populated(
    client: AsyncClient, auth_headers, session, test_user, test_account, test_workspace
):
    """Gate §15-A1: these HUMAN transitions write and clear stamps together.

    NULL-kind legacy half-stamps are admitted and read as unknown; malformed
    V2 half-shapes are rejected separately, not repaired by weakening the CHECK.
    Pairing here is a human-transition assertion, not a global rule invariant.
    """
    tx = await _make_tx(session, test_user, test_account, test_workspace, description="Half-state repair")

    def assert_paired(body):
        assert (body["reviewed_at"] is None) == (body["reviewed_by_id"] is None), body

    # needs_review → reviewed → needs_review → reviewed, pairing at each human hop.
    for target in ("reviewed", "needs_review", "reviewed"):
        resp = await client.patch(
            f"/api/transactions/{tx.id}/review",
            headers=auth_headers,
            json={"review_status": target},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["review_status"] == target
        assert_paired(body)
        if target == "reviewed":
            assert body["reviewed_at"] is not None
            assert body["reviewed_by_id"] == str(test_user.id)
        else:
            assert body["reviewed_at"] is None and body["reviewed_by_id"] is None

    # Simulate admitted legacy damage, not an invalid V2 human shape: clear
    # BOTH new columns because the last genuine human PATCH populated kind.
    # Refresh FIRST — the API's last PATCH went through a different session,
    # so the in-memory row is stale and a partial write would silently no-op.
    await session.refresh(tx)
    tx.reviewed_at = datetime.now(timezone.utc)
    tx.reviewed_by_id = None
    tx.review_attribution_kind = None
    tx.review_rule_id = None
    await session.commit()
    await session.refresh(tx)
    assert tx.reviewed_at is not None and tx.reviewed_by_id is None  # the corruption
    assert tx.review_attribution_kind is None and tx.review_rule_id is None
    assert tx.review_attribution == {"kind": "unknown"}

    down = await client.patch(
        f"/api/transactions/{tx.id}/review",
        headers=auth_headers,
        json={"review_status": "needs_review"},
    )
    assert down.status_code == 200, down.text
    assert_paired(down.json())
    down_body = down.json()
    assert down_body["reviewed_at"] is None and down_body["reviewed_by_id"] is None
    assert down_body["review_attribution"] is None

    up = await client.patch(
        f"/api/transactions/{tx.id}/review",
        headers=auth_headers,
        json={"review_status": "reviewed"},
    )
    assert up.status_code == 200, up.text
    assert_paired(up.json())
    up_body = up.json()
    assert up_body["reviewed_at"] is not None
    assert up_body["reviewed_by_id"] == str(test_user.id)
    assert up_body["review_attribution"] == {
        "kind": "human",
        "reviewed_at": up_body["reviewed_at"],
        "user_id": str(test_user.id),
    }

    # The real clear/re-review repaired legacy damage into paired HUMAN stamps.
    # Rule attribution has a different valid shape and is tested separately.
    await session.refresh(tx)
    _assert_stamps_paired(tx)
