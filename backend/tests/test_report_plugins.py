"""Report plugins: a query and a chart in the manifest, added up and drawn by Securo (ADR 0028)."""
from __future__ import annotations

import copy
import json
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.models.account import Account
from app.models.category import Category
from app.models.transaction import Transaction
from app.plugins.manifest import parse_manifest
from tests.test_data_plugins import MANIFEST, PLUGIN, V1, catalog  # noqa: F401  (fixture)

PET_SPENDING: dict[str, Any] = {
    "key": "pet-spending",
    "name": {"en": "Pet spending", "pt-BR": "Gastos com bichos"},
    "description": {"en": "What the pets cost, month by month."},
    "measure": "expenses",
    "group_by": "month",
    "categories": ["pets"],
    "chart": "bar",
}
BY_CATEGORY: dict[str, Any] = {
    "key": "by-category",
    "name": {"en": "Spending by category"},
    "group_by": "category",
    "chart": "pie",
    "limit": 3,
}


def _manifest(*reports: dict[str, Any]) -> dict[str, Any]:
    manifest: dict[str, Any] = copy.deepcopy(MANIFEST)
    reports_ = [copy.deepcopy(r) for r in (reports or (PET_SPENDING, BY_CATEGORY))]
    manifest["contributes"] = {**manifest["contributes"], "reports": reports_}
    return manifest


# --- The manifest ----------------------------------------------------------------


def test_a_plugin_can_declare_reports():
    reports = parse_manifest(_manifest()).contributes.reports
    assert [(r.key, r.measure, r.group_by, r.chart) for r in reports] == [
        ("pet-spending", "expenses", "month", "bar"),
        ("by-category", "expenses", "category", "pie"),
    ]


def test_a_report_only_plugin_is_valid():
    manifest = copy.deepcopy(MANIFEST)
    manifest["contributes"] = {"reports": [dict(PET_SPENDING, categories=[])]}
    assert parse_manifest(manifest).contributes.reports[0].categories == []


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda r: r.update(chart="line", group_by="category"), "needs group_by month"),
        (lambda r: r.update(chart="pie", group_by="month"), "pie"),
        (lambda r: r.update(chart="pie", group_by="payee", measure="net"), "pie"),
        (lambda r: r.update(categories=["nope"]), "unknown category"),
        (lambda r: r.update(categories=["pets", "pets"]), "unique"),
        (lambda r: r.update(group_by="tag"), "group_by"),
        (lambda r: r.update(query="SELECT 1"), "Extra inputs"),
        (lambda r: r.update(limit=50), "limit"),
    ],
    ids=["line-not-by-month", "pie-by-month", "pie-net", "unknown-category", "duplicate-category",
         "unknown-group", "no-raw-query", "limit"],
)
def test_reports_that_break_the_rules_are_refused(change, message):
    report = copy.deepcopy(PET_SPENDING)
    change(report)
    with pytest.raises(ValidationError, match=message):
        parse_manifest(_manifest(report))


def test_report_keys_are_unique():
    with pytest.raises(ValidationError, match="reports: keys must be unique"):
        parse_manifest(_manifest(PET_SPENDING, dict(BY_CATEGORY, key="pet-spending")))


def test_a_default_category_can_be_named():
    report = dict(PET_SPENDING, categories=["groceries"])
    assert parse_manifest(_manifest(report)).contributes.reports[0].categories == ["groceries"]


# --- Installing and reading it ----------------------------------------------------


async def _apply(client: AsyncClient, headers: dict, action: str = "install") -> None:
    plan = (await client.get(f"/api/plugins/{PLUGIN}/plan", params={"action": action}, headers=headers)).json()
    body = {"action": action, "commit": plan["commit"], "token": plan["token"]}
    resp = await client.post(f"/api/plugins/{PLUGIN}/apply", json=body, headers=headers)
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_installed_plugins_list_their_reports(client: AsyncClient, auth_headers: dict, catalog):  # noqa: F811
    catalog.manifests[V1] = _manifest()
    entries = (await client.get("/api/plugins/catalog", headers=auth_headers)).json()
    assert entries[0]["supported"] is True and entries[0]["contributes"]["reports"] == 2
    detail = (await client.get(f"/api/plugins/{PLUGIN}", headers=auth_headers)).json()
    assert [r["key"] for r in detail["contents"]["reports"]] == ["pet-spending", "by-category"]

    await _apply(client, auth_headers)
    catalog.down = True  # the library reads what was installed, never the catalog
    (installed,) = (await client.get("/api/plugins", headers=auth_headers)).json()
    assert installed["reports"][0] == {
        "key": "pet-spending",
        "name": {"en": "Pet spending", "pt-BR": "Gastos com bichos"},
        "description": {"en": "What the pets cost, month by month."},
        "code": False,
        "inputs": [],
        "measure": "expenses",
        "group_by": "month",
        "chart": "bar",
    }


# --- Running it -------------------------------------------------------------------


async def _ledger(session: AsyncSession, user, workspace) -> dict[str, Any]:
    """A workspace with the plugin's pets category and a few months of spending."""
    account = Account(
        id=uuid.uuid4(), user_id=user.id, workspace_id=workspace.id, name="Checking", type="checking",
        balance=Decimal("0"), currency="BRL",
    )
    other_account = Account(
        id=uuid.uuid4(), user_id=user.id, workspace_id=workspace.id, name="Card", type="credit_card",
        balance=Decimal("0"), currency="BRL",
    )
    session.add_all([account, other_account])
    await session.flush()
    pets = (await session.scalars(
        select(Category).where(Category.workspace_id == workspace.id, Category.source == PLUGIN,
                               Category.source_key == "pets")
    )).one()
    today = app_today()
    this_month = today.replace(day=1)
    last_month = (this_month - timedelta(days=1)).replace(day=1)
    others = [
        Category(id=uuid.uuid4(), user_id=user.id, workspace_id=workspace.id, name=name, icon="tag", color="#123456")
        for name in ("Rent", "Food", "Fun", "Books")
    ]
    session.add_all(others)
    await session.flush()

    def tx(amount: str, day: date, category: Category | None, *, kind: str = "debit", acct: Account = account,
           status: str = "posted", payee: str | None = None) -> Transaction:
        return Transaction(
            id=uuid.uuid4(), user_id=user.id, workspace_id=workspace.id, account_id=acct.id,
            category_id=category.id if category else None, description="x", amount=Decimal(amount), date=day,
            type=kind, source="manual", currency="BRL", status=status, payee=payee,
            created_at=datetime.now(timezone.utc),
        )

    session.add_all([
        tx("100.00", last_month, pets, payee="Pet Shop"),
        tx("40.00", this_month, pets, payee="Pet Shop"),
        tx("60.00", this_month, pets, acct=other_account, payee="Vet"),
        tx("999.00", this_month, pets, status="pending"),  # not settled: never counted
        tx("1500.00", this_month, others[0]),
        tx("300.00", this_month, others[1]),
        tx("50.00", this_month, others[2]),
        tx("20.00", this_month, others[3]),
        tx("5000.00", this_month, None, kind="credit"),  # income
        tx("25.00", today + timedelta(days=40), pets),  # the future isn't in a report yet
    ])
    await session.commit()
    return {"account": account, "card": other_account, "this_month": this_month, "last_month": last_month,
            "today": today}


async def _report(client, headers, key, start, end, **params):
    return await client.get(
        f"/api/reports/plugins/{PLUGIN}/{key}",
        params={"start_date": start.isoformat(), "end_date": end.isoformat(), **params},
        headers=headers,
    )


@pytest.fixture
async def installed(client: AsyncClient, auth_headers: dict, catalog, session, test_user, test_workspace):  # noqa: F811
    catalog.manifests[V1] = _manifest(
        PET_SPENDING,
        BY_CATEGORY,
        {"key": "net", "name": {"en": "Net by month"}, "measure": "net", "group_by": "month", "chart": "line"},
        {"key": "payees", "name": {"en": "Pets by payee"}, "group_by": "payee", "categories": ["pets"],
         "chart": "table"},
        {"key": "accounts", "name": {"en": "By account"}, "group_by": "account", "categories": ["pets"]},
    )
    await _apply(client, auth_headers)
    return await _ledger(session, test_user, test_workspace)


@pytest.mark.asyncio
async def test_a_report_by_month_adds_up_its_categories_with_every_month_listed(
    client: AsyncClient, auth_headers: dict, installed
):
    resp = await _report(client, auth_headers, "pet-spending", installed["last_month"], installed["today"])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["chart"], body["group_by"], body["measure"]) == ("bar", "month", "expenses")
    assert [(r["key"], r["value"]) for r in body["rows"]] == [
        (installed["last_month"].strftime("%Y-%m"), 100.0),
        (installed["this_month"].strftime("%Y-%m"), 100.0),
    ]
    assert body["total"] == 200.0


@pytest.mark.asyncio
async def test_a_report_by_category_keeps_the_largest_and_adds_up_the_rest(
    client: AsyncClient, auth_headers: dict, installed
):
    body = (await _report(client, auth_headers, "by-category", installed["this_month"], installed["today"])).json()
    assert [(r["label"], r["value"]) for r in body["rows"]] == [("Rent", 1500.0), ("Food", 300.0), (None, 170.0)]
    assert body["rows"][-1]["key"] == "other" and body["rows"][0]["color"] == "#123456"
    assert body["total"] == 1970.0


@pytest.mark.asyncio
async def test_net_reports_subtract_spending_from_income(client: AsyncClient, auth_headers: dict, installed):
    body = (await _report(client, auth_headers, "net", installed["this_month"], installed["today"])).json()
    assert body["rows"][-1]["value"] == 5000.0 - 1970.0


@pytest.mark.asyncio
async def test_reports_split_by_payee_and_by_account(client: AsyncClient, auth_headers: dict, installed):
    body = (await _report(client, auth_headers, "payees", installed["last_month"], installed["today"])).json()
    assert [(r["label"], r["value"]) for r in body["rows"]] == [("Pet Shop", 140.0), ("Vet", 60.0)]
    body = (await _report(client, auth_headers, "accounts", installed["last_month"], installed["today"])).json()
    assert [(r["label"], r["value"]) for r in body["rows"]] == [("Checking", 140.0), ("Card", 60.0)]


@pytest.mark.asyncio
async def test_a_report_reads_only_the_accounts_picked(client: AsyncClient, auth_headers: dict, installed):
    resp = await _report(client, auth_headers, "payees", installed["last_month"], installed["today"],
                         account_ids=[str(installed["card"].id)])
    assert [(r["label"], r["value"]) for r in resp.json()["rows"]] == [("Vet", 60.0)]


@pytest.mark.asyncio
async def test_a_viewer_can_read_a_report(client: AsyncClient, viewer_auth_headers: dict, installed):
    resp = await _report(client, viewer_auth_headers, "pet-spending", installed["this_month"], installed["today"])
    assert resp.status_code == 200 and resp.json()["total"] == 100.0


@pytest.mark.asyncio
async def test_a_report_the_workspace_doesnt_have_is_not_found(
    client: AsyncClient, auth_headers: dict, installed, monkeypatch
):
    today = installed["today"]
    assert (await _report(client, auth_headers, "nope", today, today)).status_code == 404
    resp = await client.get(f"/api/reports/plugins/other/plugin/pet-spending?start_date={today}&end_date={today}",
                            headers=auth_headers)
    assert resp.status_code == 404
    monkeypatch.setenv("PLUGINS_ENABLED", "false")
    assert (await _report(client, auth_headers, "pet-spending", today, today)).status_code == 404


@pytest.mark.asyncio
async def test_a_period_that_runs_backwards_or_too_long_is_refused(client: AsyncClient, auth_headers: dict, installed):
    today = installed["today"]
    assert (await _report(client, auth_headers, "pet-spending", today, today - timedelta(days=1))).status_code == 422
    assert (await _report(client, auth_headers, "pet-spending", today - timedelta(days=800), today)).status_code == 422


@pytest.mark.asyncio
async def test_a_two_year_window_at_the_end_of_a_31_day_month_is_read(
    client: AsyncClient, auth_headers: dict, installed
):
    # The plugin tabs trim their 2Y preset, which spans 760 days on this date,
    # to the 731 days the API reads (frontend/src/lib/report-period.ts); one
    # day more is still refused.
    end = date(2027, 3, 31)
    assert (await _report(client, auth_headers, "pet-spending", end - timedelta(days=731), end)).status_code == 200
    assert (await _report(client, auth_headers, "pet-spending", end - timedelta(days=732), end)).status_code == 422


@pytest.mark.asyncio
async def test_removing_the_plugin_takes_its_reports(client: AsyncClient, auth_headers: dict, installed):
    await _apply(client, auth_headers, "remove")
    today = installed["today"]
    assert (await _report(client, auth_headers, "pet-spending", today, today)).status_code == 404


# --- Code reports ----------------------------------------------------------------

from app.plugins.report_view import (  # noqa: E402
    MAX_BLOCKS,
    MAX_POINTS,
    MAX_SERIES,
    MAX_TOTAL_POINTS,
    ChartBlock,
    InvalidView,
    parse_view,
)
from tests import test_code_plugins as code_plugins  # noqa: E402

code_catalog = code_plugins.catalog
plugin_runner = code_plugins.plugin_runner
CODE_PLUGIN = code_plugins.PLUGIN

REPORT_SOURCE = '''
import json
from collections import defaultdict
from decimal import Decimal


class Plugin:
    def report(self, params, context):
        data = params["data"]
        if params["report"] == "broken":
            raise RuntimeError("the numbers don't add up")
        if params["report"] == "bad-view":
            return {"blocks": [{"type": "html", "html": "<script>x</script>"}]}
        if params["report"] == "huge-view":
            chart = {"type": "chart", "chart": "line", "x": list(range(1000)),
                     "series": [{"name": f"series {i}", "values": [1] * 1000} for i in range(12)]}
            return {"blocks": [chart for _ in range(22)]}
        names = {c["id"]: c["name"] for c in data.get("categories", [])}
        totals = defaultdict(Decimal)
        for tx in data["transactions"]["rows"]:
            if tx["type"] == "debit" and tx["status"] == "posted":
                totals[names.get(tx["category_id"], "None")] += Decimal(tx["amount"])
        top = sorted(totals.items(), key=lambda kv: -kv[1])
        return {
            "blocks": [
                {"type": "kpi", "label": "Spent", "value": float(sum(totals.values()))},
                {"type": "chart", "chart": "horizontal_bar", "x": [k for k, _ in top],
                 "series": [{"name": "Spent", "values": [float(v) for _, v in top]}]},
                {"type": "text", "text": json.dumps({
                    "accounts": len(data.get("accounts", [])), "period": [params["start_date"], params["end_date"]],
                    "currency": params["currency"], "workspace": context.securo["workspace_id"],
                })},
            ]
        }

plugin = Plugin()
'''


def _code_manifest(**report: Any) -> dict[str, Any]:
    manifest: dict[str, Any] = copy.deepcopy(code_plugins.MANIFEST)
    manifest["api_scopes"] = [
        {"scope": "transactions:read", "purpose": "Reads your spending for its reports"},
        {"scope": "accounts:read", "purpose": "Reads your accounts for its reports"},
        {"scope": "categories:read", "purpose": "Names your categories in its reports"},
    ]
    base = {"key": "insights", "name": {"en": "Insights"}, "code": True,
            "inputs": ["transactions", "accounts", "categories"]}
    reports = [dict(base, **report)] + [
        dict(base, key=k, inputs=[]) for k in ("broken", "bad-view", "huge-view")
    ]
    manifest["contributes"] = {"reports": reports}
    return manifest


def test_a_code_report_reads_only_what_its_scopes_grant():
    parse_manifest(_code_manifest())
    manifest = _code_manifest()
    manifest["api_scopes"] = [{"scope": "transactions:read", "purpose": "x"}]
    with pytest.raises(ValidationError, match="input accounts needs accounts:read"):
        parse_manifest(manifest)
    manifest["api_scopes"] = [
        {"scope": s, "purpose": "x"} for s in ("transactions:write", "accounts:write", "categories:write")
    ]
    parse_manifest(manifest)  # write includes read


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda m: m.pop("runtime"), "runtime"),
        (lambda m: m["contributes"]["reports"][0].update(group_by="month"), "computes its own view"),
        (lambda m: m["contributes"]["reports"][0].update(code=False), "inputs are for a code report"),
        (lambda m: m["contributes"]["reports"][0].update(inputs=["transactions", "transactions"]), "unique"),
        (lambda m: m["contributes"]["reports"][0].update(inputs=["passwords"]), "inputs"),
    ],
    ids=["no-runtime", "query-fields", "inputs-without-code", "duplicate-input", "unknown-input"],
)
def test_code_reports_that_break_the_rules_are_refused(change, message):
    manifest = _code_manifest()
    change(manifest)
    with pytest.raises(ValidationError, match=message):
        parse_manifest(manifest)


def _chart(points: int = MAX_POINTS, series: int = MAX_SERIES) -> dict[str, Any]:
    return {
        "type": "chart", "chart": "line", "x": list(range(points)),
        "series": [{"name": f"series {i}", "values": [1] * points} for i in range(series)],
    }


def test_a_view_draws_a_bounded_number_of_points_in_all():
    # One chart at the block limits is a report's own chart, and it is allowed.
    view = parse_view({"blocks": [_chart()]})
    block = view.blocks[0]
    assert isinstance(block, ChartBlock)
    assert len(block.series) == MAX_SERIES
    # The charts can't add up past one view's total, however many blocks fit:
    # at the block limits a second chart is already one too many.
    with pytest.raises(InvalidView, match="chart points"):
        parse_view({"blocks": [_chart(), _chart()]})
    # The view that took the browser down: 22 charts, each at the block limits.
    with pytest.raises(InvalidView, match="chart points"):
        parse_view({"blocks": [_chart() for _ in range(22)]})
    # Small charts still fit many to a view: 24 blocks of a hundred points each.
    parse_view({"blocks": [_chart(points=MAX_TOTAL_POINTS // MAX_BLOCKS, series=1)] * MAX_BLOCKS})


def test_a_view_is_data_only():
    view = parse_view({"blocks": [
        {"type": "kpi", "label": "Spent", "value": 10.5, "delta": -2, "format": "currency"},
        {"type": "chart", "chart": "stacked_bar", "x": ["2026-10", "2026-11"], "x_format": "month",
         "series": [{"name": "Food", "values": [1, 2], "color": "#112233"}, {"name": "Rent", "values": [3, None]}]},
        {"type": "table", "columns": [{"key": "name", "label": "Name"}, {"key": "v", "label": "V", "format": "number"}],
         "rows": [{"name": "a", "v": 1}]},
        {"type": "text", "text": "<b>shown as text</b>"},
    ]})
    assert [b.type for b in view.blocks] == ["kpi", "chart", "table", "text"]
    for bad in (
        {"blocks": []},
        {"blocks": [{"type": "html", "html": "<script>"}]},
        {"blocks": [{"type": "kpi", "label": "x", "value": 1, "onclick": "x"}]},
        {"blocks": [{"type": "chart", "chart": "bar", "x": [1, 2], "series": [{"name": "a", "values": [1]}]}]},
        {"blocks": [{"type": "chart", "chart": "pie", "x": [1], "series": [{"name": "a", "values": [1]},
                                                                           {"name": "b", "values": [1]}]}]},
        {"blocks": [{"type": "chart", "chart": "bar", "x": [1], "series": [{"name": "a", "values": [1],
                                                                            "color": "url(x)"}]}]},
        {"blocks": [{"type": "table", "columns": [{"key": "a", "label": "A"}], "rows": [{"b": 1}]}]},
        {"blocks": [{"type": "text", "text": "x"}] * 25},
    ):
        with pytest.raises(InvalidView):
            parse_view(bad)


async def _install_code_report(client, headers, fake) -> None:
    fake.manifests[code_plugins.V1] = _code_manifest()
    fake.archives[code_plugins.V1] = code_plugins._tarball(REPORT_SOURCE)
    plan = (await client.get(f"/api/plugins/{CODE_PLUGIN}/plan", params={"action": "install"}, headers=headers)).json()
    body = {"action": "install", "commit": plan["commit"], "token": plan["token"]}
    resp = await client.post(f"/api/plugins/{CODE_PLUGIN}/apply", json=body, headers=headers)
    assert resp.status_code == 200, resp.text


async def _code_report(client, headers, key, start, end, **params):
    return await client.get(
        f"/api/reports/plugins/{CODE_PLUGIN}/{key}",
        params={"start_date": start.isoformat(), "end_date": end.isoformat(), **params},
        headers=headers,
    )


@pytest.mark.asyncio
async def test_a_code_report_computes_its_view_from_the_inputs_it_asked_for(
    client: AsyncClient, auth_headers: dict, code_catalog, plugin_runner, session, test_user, test_workspace
):
    await _install_code_report(client, auth_headers, code_catalog)
    installed = (await client.get("/api/plugins", headers=auth_headers)).json()
    assert installed[0]["reports"][0]["code"] is True
    ledger = await _ledger_without_plugin(session, test_user, test_workspace)
    resp = await _code_report(client, auth_headers, "insights", ledger["last_month"], ledger["today"])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["code"] is True and body["rows"] == []
    kpi, chart, text = body["view"]["blocks"]
    assert (kpi["type"], kpi["label"], kpi["value"], kpi["format"]) == ("kpi", "Spent", 2070.0, "currency")
    assert chart["x"][:2] == ["Rent", "Food"] and chart["series"][0]["values"][:2] == [1500.0, 300.0]
    facts = json.loads(text["text"])
    assert facts["accounts"] == 2 and facts["workspace"] == str(test_workspace.id)
    assert facts["period"] == [ledger["last_month"].isoformat(), ledger["today"].isoformat()]
    assert body["view"]["currency"] == facts["currency"]


@pytest.mark.asyncio
async def test_a_code_report_reads_only_the_accounts_picked(
    client: AsyncClient, auth_headers: dict, code_catalog, plugin_runner, session, test_user, test_workspace
):
    await _install_code_report(client, auth_headers, code_catalog)
    ledger = await _ledger_without_plugin(session, test_user, test_workspace)
    resp = await _code_report(client, auth_headers, "insights", ledger["last_month"], ledger["today"],
                              account_ids=[str(ledger["card"].id)])
    kpi, _, text = resp.json()["view"]["blocks"]
    assert kpi["value"] == 60.0 and json.loads(text["text"])["accounts"] == 1


@pytest.mark.asyncio
async def test_a_failing_or_invalid_code_report_is_an_error_for_the_page(
    client: AsyncClient, auth_headers: dict, code_catalog, plugin_runner
):
    await _install_code_report(client, auth_headers, code_catalog)
    today = app_today()
    broken = await _code_report(client, auth_headers, "broken", today, today)
    assert broken.status_code == 502 and "the numbers don't add up" in broken.json()["detail"]
    bad = await _code_report(client, auth_headers, "bad-view", today, today)
    assert bad.status_code == 502 and "invalid view" in bad.json()["detail"]
    assert "<script>" not in bad.json()["detail"]
    huge = await _code_report(client, auth_headers, "huge-view", today, today)
    assert huge.status_code == 502 and "invalid view" in huge.json()["detail"]
    assert "chart points" in huge.json()["detail"]


@pytest.mark.asyncio
async def test_a_plugin_with_code_reports_needs_the_runner_to_install(
    client: AsyncClient, auth_headers: dict, code_catalog, monkeypatch
):
    from app.plugins import runner

    code_catalog.manifests[code_plugins.V1] = _code_manifest()
    monkeypatch.setattr(runner, "configured", lambda: False)
    (entry,) = (await client.get("/api/plugins/catalog", headers=auth_headers)).json()
    assert entry["supported"] is False


@pytest.mark.asyncio
async def test_a_code_report_without_the_runner_is_unavailable(
    client: AsyncClient, auth_headers: dict, code_catalog, plugin_runner, monkeypatch
):
    await _install_code_report(client, auth_headers, code_catalog)
    from app.plugins import runner

    monkeypatch.setattr(runner, "configured", lambda: False)
    today = app_today()
    assert (await _code_report(client, auth_headers, "insights", today, today)).status_code == 503


async def _ledger_without_plugin(session: AsyncSession, user, workspace) -> dict[str, Any]:
    """The same spending as `_ledger`, with a category of its own instead of the plugin's."""
    pets = Category(id=uuid.uuid4(), user_id=user.id, workspace_id=workspace.id, name="Pets", icon="dog",
                    color="#F97316", source=PLUGIN, source_key="pets")
    session.add(pets)
    await session.commit()
    return await _ledger(session, user, workspace)


def test_a_view_lays_out_and_mixes_every_kind_of_block():
    view = parse_view({"blocks": [
        {"type": "kpi", "label": "Spent", "value": 10, "delta": 2, "delta_good": "down", "width": "quarter"},
        {"type": "chart", "chart": "combo", "width": "two_thirds", "x": ["2026-10", "2026-11", "2026-12"],
         "x_format": "month", "y2_format": "percent",
         "series": [
             {"name": "Income", "values": [5, 6, 7], "kind": "bar"},
             {"name": "Net", "values": [-1, 2, 3], "kind": "bar", "negative_color": "#FF0000"},
             {"name": "Savings rate", "values": [10, 20, 30], "kind": "line", "axis": "right",
              "projected_from": "2026-11"},
         ],
         "references": [{"label": "Target", "y": 25, "axis": "right"}, {"label": "Today", "x": "2026-11"}]},
        {"type": "heatmap", "x": ["Mon", "Tue"], "y": ["Oct", "Nov"], "values": [[1, 2], [None, 4]]},
        {"type": "treemap", "items": [{"name": "Home", "children": [{"name": "Rent", "value": 10}]},
                                      {"name": "Fun", "value": 3}]},
        {"type": "sankey", "nodes": ["Salary", "Rent", "Saved"],
         "links": [{"source": 0, "target": 1, "value": 10}, {"source": 0, "target": 2, "value": 5}]},
        {"type": "waterfall", "steps": [{"label": "Start", "value": 100, "total": True}, {"label": "In", "value": 30},
                                        {"label": "Out", "value": -50}, {"label": "End", "value": 80, "total": True}]},
        {"type": "progress", "items": [{"label": "Food", "value": 120, "target": 100}], "over_is": "bad"},
        {"type": "table", "columns": [{"key": "a", "label": "A"}, {"key": "v", "label": "V", "format": "currency",
                                      "total": True, "bar": True, "signed": True}],
         "rows": [{"a": "x", "v": -1}], "sort_by": "v", "page_size": 10},
    ]})
    assert [b.type for b in view.blocks] == [
        "kpi", "chart", "heatmap", "treemap", "sankey", "waterfall", "progress", "table",
    ]


@pytest.mark.parametrize(
    "block",
    [
        {"type": "chart", "chart": "bar", "x": [1], "series": [{"name": "a", "values": [1], "projected_from": 9}]},
        {"type": "chart", "chart": "pie", "x": [1], "series": [{"name": "a", "values": [1], "axis": "right"}]},
        {"type": "chart", "chart": "line", "x": [1], "series": [{"name": "a", "values": [1]}],
         "references": [{"label": "x"}]},
        {"type": "heatmap", "x": ["a", "b"], "y": ["c"], "values": [[1]]},
        {"type": "heatmap", "x": list(range(60)), "y": list(range(60)), "values": [[1] * 60] * 60},
        {"type": "treemap", "items": [{"name": "empty"}]},
        {"type": "sankey", "nodes": ["a", "b"], "links": [{"source": 0, "target": 5, "value": 1}]},
        {"type": "sankey", "nodes": ["a", "b"], "links": [{"source": 0, "target": 0, "value": 1}]},
        {"type": "sankey", "nodes": ["a", "b", "c"], "links": [{"source": 0, "target": 1, "value": 1},
                                                              {"source": 1, "target": 2, "value": 1},
                                                              {"source": 2, "target": 0, "value": 1}]},
        {"type": "progress", "items": [{"label": "x", "value": 1, "target": 0}]},
        {"type": "table", "columns": [{"key": "a", "label": "A"}], "rows": [], "sort_by": "b"},
        {"type": "kpi", "label": "x", "value": 1, "width": "huge"},
    ],
    ids=["projected-from-unknown", "pie-second-axis", "reference-nowhere", "heatmap-shape", "heatmap-too-big",
         "treemap-empty-item", "sankey-unknown-node", "sankey-loop", "sankey-cycle", "progress-zero-target", "table-sort-unknown",
         "unknown-width"],
)
def test_blocks_that_cant_be_drawn_are_refused(block):
    with pytest.raises(InvalidView):
        parse_view({"blocks": [block]})
