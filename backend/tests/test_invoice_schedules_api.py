"""Recurring invoices over HTTP.

The service tests own the rules; these own the wiring: that every route
is gated exactly like `/api/invoices`, that a viewer can read and never
write, and that the journeys a person actually takes (write a retainer,
see its invoices arrive, raise the price, link history, end it) hold
together end to end.
"""
import uuid
from datetime import date, timedelta

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payee import Payee
from app.models.workspace import WorkspaceMember


@pytest_asyncio.fixture
async def business_ws(client: AsyncClient, auth_headers) -> dict:
    resp = await client.post(
        "/api/workspaces",
        headers=auth_headers,
        json={"name": "Estudio", "kind": "business", "self_membership": True},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest_asyncio.fixture
async def biz_headers(auth_headers, business_ws) -> dict:
    return {**auth_headers, "X-Workspace-Id": business_ws["id"]}


@pytest_asyncio.fixture
async def personal_headers(session: AsyncSession, auth_headers, test_user) -> dict:
    from sqlalchemy import select

    from app.models.workspace import Workspace

    result = await session.execute(
        select(Workspace)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == test_user.id, Workspace.kind == "personal")
        .limit(1)
    )
    return {**auth_headers, "X-Workspace-Id": str(result.scalar_one().id)}


@pytest_asyncio.fixture
async def client_payee(session: AsyncSession, business_ws, test_user) -> Payee:
    payee = Payee(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=uuid.UUID(business_ws["id"]),
        name="Cliente Alpha",
        source="manual",
    )
    session.add(payee)
    await session.commit()
    return payee


@pytest_asyncio.fixture
async def viewer_headers(session: AsyncSession, client: AsyncClient, business_ws) -> dict:
    """A viewer of the business workspace: may read, may not write."""
    import bcrypt

    from app.models.user import User

    user = User(
        id=uuid.uuid4(),
        email="viewer-schedules@example.com",
        hashed_password=bcrypt.hashpw(b"viewerpass123", bcrypt.gensalt()).decode(),
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    session.add(user)
    await session.flush()
    session.add(
        WorkspaceMember(
            id=uuid.uuid4(), workspace_id=uuid.UUID(business_ws["id"]), user_id=user.id, role="viewer"
        )
    )
    await session.commit()
    resp = await client.post(
        "/api/auth/login",
        data={"username": "viewer-schedules@example.com", "password": "viewerpass123"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200, resp.text
    return {
        "Authorization": f"Bearer {resp.json()['access_token']}",
        "X-Workspace-Id": business_ws["id"],
    }


TODAY = date.today()
NEXT_MONTH = (TODAY.replace(day=1) + timedelta(days=32)).replace(day=1)


async def _create(client, headers, **overrides) -> dict:
    payload = {
        "name": "Retainer",
        "frequency": "monthly",
        "start_date": str(NEXT_MONTH),
        "lines": [{"description": "Retainer", "quantity": 1, "unit_price": "3000.00"}],
    }
    payload.update(overrides)
    resp = await client.post("/api/invoice-schedules", headers=headers, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Gating
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_personal_workspace_cannot_reach_any_route(client: AsyncClient, personal_headers):
    """Every route, not a sample: one ungated route is the whole hole."""
    fake = str(uuid.uuid4())
    routes = [
        ("get", "/api/invoice-schedules", None),
        ("get", "/api/invoice-schedules/summary", None),
        ("post", "/api/invoice-schedules", {"name": "x", "frequency": "monthly", "lines": [{"description": "a", "unit_price": "1"}]}),
        ("get", f"/api/invoice-schedules/{fake}", None),
        ("patch", f"/api/invoice-schedules/{fake}", {"name": "y"}),
        ("delete", f"/api/invoice-schedules/{fake}", None),
        ("get", f"/api/invoice-schedules/{fake}/invoices", None),
        ("get", f"/api/invoice-schedules/{fake}/periods", None),
        ("post", f"/api/invoice-schedules/{fake}/pause", None),
        ("post", f"/api/invoice-schedules/{fake}/resume", None),
        ("post", f"/api/invoice-schedules/{fake}/end", {"reason": "other"}),
        ("post", f"/api/invoice-schedules/{fake}/generate", None),
        ("post", f"/api/invoice-schedules/{fake}/terms", {"effective_from": str(TODAY), "lines": [{"description": "a", "unit_price": "1"}]}),
        ("patch", f"/api/invoice-schedules/{fake}/terms/{fake}", {"discount": "1"}),
        ("delete", f"/api/invoice-schedules/{fake}/terms/{fake}", None),
        ("post", f"/api/invoice-schedules/{fake}/link", {"invoice_id": fake, "period_start": str(TODAY)}),
        ("post", f"/api/invoices/{fake}/make-recurring", {"frequency": "monthly"}),
        ("delete", f"/api/invoices/{fake}/schedule", None),
    ]
    for method, url, body in routes:
        call = getattr(client, method)
        resp = await call(url, headers=personal_headers, **({"json": body} if body else {}))
        assert resp.status_code == 404, f"{method.upper()} {url} -> {resp.status_code}"


@pytest.mark.asyncio
async def test_viewer_reads_and_never_writes(client: AsyncClient, biz_headers, viewer_headers):
    created = await _create(client, biz_headers)
    sid = created["id"]

    for url in ("/api/invoice-schedules", "/api/invoice-schedules/summary", f"/api/invoice-schedules/{sid}",
                f"/api/invoice-schedules/{sid}/invoices", f"/api/invoice-schedules/{sid}/periods"):
        assert (await client.get(url, headers=viewer_headers)).status_code == 200, url

    writes = [
        ("post", "/api/invoice-schedules", {"name": "x", "frequency": "monthly", "lines": [{"description": "a", "unit_price": "1"}]}),
        ("patch", f"/api/invoice-schedules/{sid}", {"name": "y"}),
        ("delete", f"/api/invoice-schedules/{sid}", None),
        ("post", f"/api/invoice-schedules/{sid}/pause", None),
        ("post", f"/api/invoice-schedules/{sid}/resume", None),
        ("post", f"/api/invoice-schedules/{sid}/end", {"reason": "other"}),
        ("post", f"/api/invoice-schedules/{sid}/generate", None),
        ("post", f"/api/invoice-schedules/{sid}/terms", {"effective_from": str(NEXT_MONTH), "lines": [{"description": "a", "unit_price": "1"}]}),
        ("post", f"/api/invoice-schedules/{sid}/link", {"invoice_id": sid, "period_start": str(NEXT_MONTH)}),
    ]
    for method, url, body in writes:
        call = getattr(client, method)
        resp = await call(url, headers=viewer_headers, **({"json": body} if body else {}))
        assert resp.status_code == 403, f"{method.upper()} {url} -> {resp.status_code}"


@pytest.mark.asyncio
async def test_a_schedule_never_crosses_workspaces(client: AsyncClient, auth_headers, biz_headers):
    created = await _create(client, biz_headers)
    other = await client.post(
        "/api/workspaces", headers=auth_headers,
        json={"name": "Outra", "kind": "business", "self_membership": True},
    )
    other_headers = {**auth_headers, "X-Workspace-Id": other.json()["id"]}
    assert (await client.get(f"/api/invoice-schedules/{created['id']}", headers=other_headers)).status_code == 404
    assert (await client.get("/api/invoice-schedules", headers=other_headers)).json() == []
    resp = await client.post(
        f"/api/invoice-schedules/{created['id']}/link", headers=other_headers,
        json={"invoice_id": str(uuid.uuid4()), "period_start": str(NEXT_MONTH)},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Journeys
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_create_reads_back_with_derived_figures(client: AsyncClient, biz_headers, client_payee):
    created = await _create(
        client, biz_headers, payee_id=str(client_payee.id), frequency="quarterly",
        lines=[{"description": "Hosting", "quantity": 1, "unit_price": "900.00", "tax_rate": "10"}],
        discount="50.00", payment_terms_days=10,
    )
    assert created["status"] == "active"
    assert created["payee"]["name"] == "Cliente Alpha"
    assert created["next_sequence"] == 1
    assert created["next_period_start"] == str(NEXT_MONTH)
    assert created["current_term"] is None  # not in force until it starts
    assert created["next_term"]["total"] == "940.00"  # 900 - 50 + 90
    assert created["invoice_count"] == 0
    assert len(created["terms"]) == 1

    summary = (await client.get("/api/invoice-schedules/summary", headers=biz_headers)).json()
    assert summary["active_count"] == 1
    # The agreement has not started, so nothing recurs yet; it is active
    # but worth zero a month until its first term is in force.
    assert summary["by_currency"][0]["monthly_recurring"] == "0.00"


@pytest.mark.asyncio
async def test_generate_now_emits_an_ordinary_invoice_that_remembers_its_period(
    client: AsyncClient, biz_headers, client_payee
):
    created = await _create(client, biz_headers, payee_id=str(client_payee.id))
    sid = created["id"]
    resp = await client.post(f"/api/invoice-schedules/{sid}/generate", headers=biz_headers)
    assert resp.status_code == 201, resp.text
    [invoice] = resp.json()
    assert invoice["status"] == "open"
    assert invoice["number"] is not None
    assert invoice["schedule_id"] == sid
    assert invoice["schedule"]["name"] == "Retainer"
    assert invoice["sequence"] == 1
    assert invoice["period_start"] == str(NEXT_MONTH)
    assert invoice["issue_date"] == str(NEXT_MONTH)
    assert invoice["total"] == "3000.00"
    assert [line["description"] for line in invoice["lines"]] == ["Retainer"]

    # It is a normal invoice: on the list, in the detail, filterable by agreement.
    listed = (await client.get("/api/invoices", headers=biz_headers, params={"schedule_id": sid})).json()
    assert [i["id"] for i in listed] == [invoice["id"]]
    listed = (await client.get(f"/api/invoice-schedules/{sid}/invoices", headers=biz_headers)).json()
    assert [i["id"] for i in listed] == [invoice["id"]]

    schedule = (await client.get(f"/api/invoice-schedules/{sid}", headers=biz_headers)).json()
    assert schedule["next_sequence"] == 2
    assert schedule["invoice_count"] == 1
    assert schedule["amount_invoiced"] == "3000.00"
    assert schedule["amount_paid"] == "0.00"
    assert schedule["last_generated_at"] is not None

    periods = (await client.get(f"/api/invoice-schedules/{sid}/periods", headers=biz_headers)).json()
    assert periods[0] == {
        "sequence": 1, "period_start": str(NEXT_MONTH),
        "period_end": periods[0]["period_end"], "taken": True,
    }
    assert [p["taken"] for p in periods[1:]] == [False] * (len(periods) - 1)


@pytest.mark.asyncio
async def test_a_raise_is_recorded_ahead_and_history_stays_put(client: AsyncClient, biz_headers):
    created = await _create(client, biz_headers)
    sid = created["id"]
    await client.post(f"/api/invoice-schedules/{sid}/generate", headers=biz_headers)

    later = NEXT_MONTH + timedelta(days=40)
    resp = await client.post(
        f"/api/invoice-schedules/{sid}/terms", headers=biz_headers,
        json={"effective_from": str(later), "lines": [{"description": "Retainer", "unit_price": "3500.00"}]},
    )
    assert resp.status_code == 201, resp.text
    schedule = resp.json()
    assert len(schedule["terms"]) == 2
    new_term = next(t for t in schedule["terms"] if t["effective_from"] == str(later))

    # Touching the term the first invoice was issued under is refused.
    first_term = next(t for t in schedule["terms"] if t["effective_from"] == str(NEXT_MONTH))
    resp = await client.patch(
        f"/api/invoice-schedules/{sid}/terms/{first_term['id']}", headers=biz_headers,
        json={"lines": [{"description": "Retainer", "unit_price": "1.00"}]},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "term_in_use"

    # The future one is free to change, move and go.
    resp = await client.patch(
        f"/api/invoice-schedules/{sid}/terms/{new_term['id']}", headers=biz_headers,
        json={"discount": "100.00", "effective_from": str(later + timedelta(days=1))},
    )
    assert resp.status_code == 200, resp.text
    moved = next(t for t in resp.json()["terms"] if t["id"] == new_term["id"])
    assert moved["total"] == "3400.00"
    resp = await client.delete(f"/api/invoice-schedules/{sid}/terms/{new_term['id']}", headers=biz_headers)
    assert resp.status_code == 200
    assert len(resp.json()["terms"]) == 1


def _months_back(day: date, months: int) -> date:
    month_index = day.month - 1 - months
    year = day.year + month_index // 12
    return date(year, month_index % 12 + 1, day.day)


@pytest.mark.asyncio
async def test_make_recurring_from_an_invoice_and_link_history(client: AsyncClient, biz_headers, client_payee):
    """The two doors that are not "new agreement": an invoice that
    already exists starts one, and older invoices join it."""
    this_month = TODAY.replace(day=5)
    march = _months_back(this_month, 6)
    older = await client.post(
        "/api/invoices", headers=biz_headers,
        json={"payee_id": str(client_payee.id), "total": "3000.00", "issue_date": str(march),
              "due_date": str(march + timedelta(days=15))},
    )
    assert older.status_code == 201, older.text
    latest = await client.post(
        "/api/invoices", headers=biz_headers,
        json={"payee_id": str(client_payee.id), "issue_date": str(this_month),
              "due_date": str(this_month + timedelta(days=20)),
              "lines": [{"description": "Consultoria", "quantity": 10, "unit": "h", "unit_price": "300.00"}]},
    )
    assert latest.status_code == 201, latest.text

    resp = await client.post(
        f"/api/invoices/{latest.json()['id']}/make-recurring", headers=biz_headers,
        json={"frequency": "monthly", "start_date": str(march)},
    )
    assert resp.status_code == 201, resp.text
    schedule = resp.json()
    assert schedule["name"] == "Consultoria"
    assert schedule["start_date"] == str(march)
    assert schedule["payment_terms_days"] == 20
    assert schedule["invoice_count"] == 1
    assert schedule["monthly_amount"] == "3000.00"
    started = (await client.get(f"/api/invoices/{latest.json()['id']}", headers=biz_headers)).json()
    assert started["schedule_id"] == schedule["id"] and started["sequence"] == 7

    # The periods picker says which dates are boundaries and which are free.
    periods = (await client.get(f"/api/invoice-schedules/{schedule['id']}/periods", headers=biz_headers)).json()
    assert periods[0]["period_start"] == str(march) and periods[0]["taken"] is False
    assert periods[6]["period_start"] == str(this_month) and periods[6]["taken"] is True

    resp = await client.post(
        f"/api/invoice-schedules/{schedule['id']}/link", headers=biz_headers,
        json={"invoice_id": older.json()["id"], "period_start": str(march + timedelta(days=1))},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "not_a_period"

    linked = await client.post(
        f"/api/invoice-schedules/{schedule['id']}/link", headers=biz_headers,
        json={"invoice_id": older.json()["id"], "period_start": str(march)},
    )
    assert linked.status_code == 200, linked.text
    assert linked.json()["schedule_id"] == schedule["id"]
    assert linked.json()["sequence"] == 1
    detail = (await client.get(f"/api/invoice-schedules/{schedule['id']}", headers=biz_headers)).json()
    assert detail["invoice_count"] == 2
    assert detail["amount_invoiced"] == "6000.00"

    # Unlinking gives the invoice back as a one-off.
    resp = await client.delete(f"/api/invoices/{older.json()['id']}/schedule", headers=biz_headers)
    assert resp.status_code == 200
    assert resp.json()["schedule_id"] is None

    # A second make-recurring on the same invoice is refused.
    resp = await client.post(
        f"/api/invoices/{latest.json()['id']}/make-recurring", headers=biz_headers,
        json={"frequency": "monthly"},
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_lifecycle_over_http(client: AsyncClient, biz_headers):
    created = await _create(client, biz_headers)
    sid = created["id"]

    resp = await client.post(f"/api/invoice-schedules/{sid}/pause", headers=biz_headers)
    assert resp.json()["status"] == "paused" and resp.json()["pause_reason"] == "manual"
    resp = await client.post(f"/api/invoice-schedules/{sid}/generate", headers=biz_headers)
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "schedule_not_active"
    resp = await client.post(f"/api/invoice-schedules/{sid}/resume", headers=biz_headers)
    assert resp.json()["status"] == "active"

    resp = await client.patch(
        f"/api/invoice-schedules/{sid}", headers=biz_headers,
        json={"name": "Plano Pro", "end_type": "after_count", "end_count": 2},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "Plano Pro"
    assert resp.json()["end_count"] == 2

    resp = await client.post(
        f"/api/invoice-schedules/{sid}/end", headers=biz_headers,
        json={"reason": "canceled_by_client", "ended_at": str(TODAY)},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["status"], body["end_reason"], body["ended_at"]) == ("ended", "canceled_by_client", str(TODAY))
    assert body["next_period_start"] is None

    listed = (await client.get("/api/invoice-schedules", headers=biz_headers, params={"status": "ended"})).json()
    assert [s["id"] for s in listed] == [sid]
    summary = (await client.get("/api/invoice-schedules/summary", headers=biz_headers)).json()
    assert summary["ended_count"] == 1 and summary["active_count"] == 0

    # Nothing was billed, so it may still be deleted.
    assert (await client.delete(f"/api/invoice-schedules/{sid}", headers=biz_headers)).status_code == 204
    assert (await client.get(f"/api/invoice-schedules/{sid}", headers=biz_headers)).status_code == 404


@pytest.mark.asyncio
async def test_delete_refuses_once_billed_and_errors_carry_codes(client: AsyncClient, biz_headers):
    created = await _create(client, biz_headers)
    sid = created["id"]
    await client.post(f"/api/invoice-schedules/{sid}/generate", headers=biz_headers)
    resp = await client.delete(f"/api/invoice-schedules/{sid}", headers=biz_headers)
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "schedule_has_invoices"

    resp = await client.patch(f"/api/invoice-schedules/{sid}", headers=biz_headers, json={"frequency": "weekly"})
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "schedule_locked"

    resp = await client.post(
        "/api/invoice-schedules", headers=biz_headers,
        json={"name": "x", "frequency": "monthly", "end_type": "on_date",
              "lines": [{"description": "a", "unit_price": "1"}]},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "end_date_required"

    resp = await client.post(
        "/api/invoice-schedules", headers=biz_headers,
        json={"name": "x", "frequency": "monthly", "lines": [{"description": "a", "unit_price": "0"}]},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "term_empty"
