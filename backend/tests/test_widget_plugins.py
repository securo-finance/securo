"""Dashboard widgets from plugins: a report sized for a card, over the month on screen (ADR 0034)."""
from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from httpx import AsyncClient
from pydantic import ValidationError

from app.core.app_clock import app_today
from app.plugins.manifest import parse_manifest
from tests import test_code_plugins as code_plugins
from tests.test_data_plugins import MANIFEST, PLUGIN, V1, catalog  # noqa: F401  (fixture)
from tests.test_report_plugins import BY_CATEGORY, PET_SPENDING, _apply, _ledger, _ledger_without_plugin

code_catalog = code_plugins.catalog
plugin_runner = code_plugins.plugin_runner
CODE_PLUGIN = code_plugins.PLUGIN

PETS_THIS_YEAR: dict[str, Any] = dict(PET_SPENDING, key="pets-card", width="third", period="last_12_months")


def _manifest(*widgets: dict[str, Any], reports: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    manifest: dict[str, Any] = copy.deepcopy(MANIFEST)
    contributes = {**manifest["contributes"], "widgets": [copy.deepcopy(w) for w in (widgets or (PETS_THIS_YEAR,))]}
    if reports is not None:
        contributes["reports"] = reports
    manifest["contributes"] = contributes
    return manifest


# --- The manifest ----------------------------------------------------------------


def test_a_plugin_can_declare_widgets_with_a_starting_width():
    (pets, by_category) = parse_manifest(_manifest(PETS_THIS_YEAR, BY_CATEGORY)).contributes.widgets
    assert (pets.key, pets.width, pets.period, pets.group_by) == ("pets-card", "third", "last_12_months", "month")
    assert (by_category.width, by_category.period) == ("half", "month")  # until the plugin says otherwise


def test_a_widget_only_plugin_is_valid():
    manifest = copy.deepcopy(MANIFEST)
    manifest["contributes"] = {"widgets": [dict(PETS_THIS_YEAR, categories=[])]}
    assert parse_manifest(manifest).contributes.widgets[0].key == "pets-card"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda w: w.update(width="quarter"), "width"),
        (lambda w: w.update(period="month"), "one month has nothing to split by month"),
        (lambda w: w.update(period="decade"), "period"),
        (lambda w: w.update(chart="pie", group_by="month"), "widget pets-card: a pie"),
        (lambda w: w.update(categories=["nope"]), "widget pets-card: unknown category"),
        (lambda w: w.update(script="alert(1)"), "Extra inputs"),
    ],
    ids=["unknown-width", "by-month-over-one-month", "unknown-period", "pie-by-month", "unknown-category", "no-script"],
)
def test_widgets_that_break_the_rules_are_refused(change, message):
    widget = copy.deepcopy(PETS_THIS_YEAR)
    change(widget)
    with pytest.raises(ValidationError, match=message):
        parse_manifest(_manifest(widget))


def test_widget_keys_are_unique_but_may_repeat_a_reports():
    with pytest.raises(ValidationError, match="widgets: keys must be unique"):
        parse_manifest(_manifest(PETS_THIS_YEAR, dict(BY_CATEGORY, key="pets-card")))
    # A report and a widget live on different surfaces: the same key names both.
    parse_manifest(_manifest(PETS_THIS_YEAR, reports=[dict(PET_SPENDING, key="pets-card")]))


# --- Installing and reading it ----------------------------------------------------


@pytest.mark.asyncio
async def test_installed_plugins_list_their_widgets(client: AsyncClient, auth_headers: dict, catalog):  # noqa: F811
    catalog.manifests[V1] = _manifest()
    (entry,) = (await client.get("/api/plugins/catalog", headers=auth_headers)).json()
    assert entry["supported"] is True and entry["contributes"]["widgets"] == 1
    detail = (await client.get(f"/api/plugins/{PLUGIN}", headers=auth_headers)).json()
    assert [(w["key"], w["width"]) for w in detail["contents"]["widgets"]] == [("pets-card", "third")]

    await _apply(client, auth_headers)
    catalog.down = True  # the library reads what was installed, never the catalog
    (installed,) = (await client.get("/api/plugins", headers=auth_headers)).json()
    assert installed["reports"] == []
    assert installed["widgets"] == [
        {
            "key": "pets-card",
            "name": {"en": "Pet spending", "pt-BR": "Gastos com bichos"},
            "description": {"en": "What the pets cost, month by month."},
            "code": False,
            "inputs": [],
            "measure": "expenses",
            "group_by": "month",
            "chart": "bar",
            "width": "third",
            "period": "last_12_months",
        }
    ]


async def _widget(client, headers, key, start, end, plugin=PLUGIN, **params):
    return await client.get(
        f"/api/dashboard/plugins/{plugin}/{key}",
        params={"start_date": start.isoformat(), "end_date": end.isoformat(), **params},
        headers=headers,
    )


@pytest.fixture
async def installed(client: AsyncClient, auth_headers: dict, catalog, session, test_user, test_workspace):  # noqa: F811
    catalog.manifests[V1] = _manifest(PETS_THIS_YEAR, BY_CATEGORY, reports=[PET_SPENDING])
    await _apply(client, auth_headers)
    return await _ledger(session, test_user, test_workspace)


@pytest.mark.asyncio
async def test_a_widget_adds_up_the_month_like_the_report_it_mirrors(
    client: AsyncClient, auth_headers: dict, installed
):
    start, end = installed["this_month"], installed["today"]
    widget = await _widget(client, auth_headers, "pets-card", start, end)
    assert widget.status_code == 200, widget.text
    report = await client.get(
        f"/api/reports/plugins/{PLUGIN}/pet-spending",
        params={"start_date": start.isoformat(), "end_date": end.isoformat()},
        headers=auth_headers,
    )
    assert {**widget.json(), "key": None} == {**report.json(), "key": None}
    assert widget.json()["total"] == 100.0

    by_category = (await _widget(client, auth_headers, "by-category", start, end)).json()
    assert [(r["label"], r["value"]) for r in by_category["rows"]] == [("Rent", 1500.0), ("Food", 300.0), (None, 170.0)]


@pytest.mark.asyncio
async def test_a_widget_reads_only_the_accounts_in_view(client: AsyncClient, auth_headers: dict, installed):
    body = (
        await _widget(client, auth_headers, "by-category", installed["this_month"], installed["today"],
                      account_ids=[str(installed["card"].id)])
    ).json()
    assert body["total"] == 60.0


@pytest.mark.asyncio
async def test_a_viewer_can_read_a_widget(client: AsyncClient, viewer_auth_headers: dict, installed):
    today = installed["today"]
    assert (await _widget(client, viewer_auth_headers, "pets-card", today, today)).status_code == 200


@pytest.mark.asyncio
async def test_widgets_and_reports_are_looked_up_on_their_own_surface(
    client: AsyncClient, auth_headers: dict, installed, monkeypatch
):
    today = installed["today"]
    # A report isn't a widget, nor the other way round.
    assert (await _widget(client, auth_headers, "pet-spending", today, today)).status_code == 404
    resp = await client.get(
        f"/api/reports/plugins/{PLUGIN}/pets-card",
        params={"start_date": today.isoformat(), "end_date": today.isoformat()},
        headers=auth_headers,
    )
    assert resp.status_code == 404
    assert (await _widget(client, auth_headers, "pets-card", today, today, plugin="other/plugin")).status_code == 404
    monkeypatch.setenv("PLUGINS_ENABLED", "false")
    assert (await _widget(client, auth_headers, "pets-card", today, today)).status_code == 404


@pytest.mark.asyncio
async def test_a_period_that_runs_backwards_is_refused(client: AsyncClient, auth_headers: dict, installed):
    resp = await _widget(client, auth_headers, "pets-card", installed["today"], installed["last_month"])
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_removing_the_plugin_takes_its_widgets(client: AsyncClient, auth_headers: dict, installed):
    await _apply(client, auth_headers, "remove")
    today = installed["today"]
    assert (await _widget(client, auth_headers, "pets-card", today, today)).status_code == 404


# --- Code widgets ------------------------------------------------------------------

WIDGET_SOURCE = '''
import json
from decimal import Decimal


class Plugin:
    def report(self, params, context):
        raise RuntimeError("a widget never runs the report method")

    def widget(self, params, context):
        if params["widget"] == "broken":
            raise RuntimeError("the widget fell over")
        spent = sum(
            Decimal(tx["amount"]) for tx in params["data"]["transactions"]["rows"]
            if tx["type"] == "debit" and tx["status"] == "posted"
        )
        return {
            "blocks": [
                {"type": "kpi", "label": "Spent", "value": float(spent)},
                {"type": "text", "text": json.dumps({
                    "widget": params["widget"], "report": params.get("report"),
                    "period": [params["start_date"], params["end_date"]],
                    "securo": context.securo.get("widget"),
                })},
            ]
        }

plugin = Plugin()
'''


def _code_manifest() -> dict[str, Any]:
    manifest: dict[str, Any] = copy.deepcopy(code_plugins.MANIFEST)
    manifest["api_scopes"] = [{"scope": "transactions:read", "purpose": "Adds up your spending for its widget"}]
    base = {"name": {"en": "Spent"}, "code": True, "inputs": ["transactions"], "width": "third"}
    manifest["contributes"] = {"widgets": [dict(base, key="spent"), dict(base, key="broken", inputs=[])]}
    return manifest


def test_a_code_widget_is_enough_for_a_runtime_and_needs_its_scopes():
    parse_manifest(_code_manifest())
    manifest = _code_manifest()
    manifest["api_scopes"] = []
    with pytest.raises(ValidationError, match="widget spent: input transactions needs transactions:read"):
        parse_manifest(manifest)
    manifest = _code_manifest()
    for key in ("runtime", "api_scopes", "network", "logos"):
        manifest.pop(key, None)
    with pytest.raises(ValidationError, match="need a runtime"):
        parse_manifest(manifest)


async def _install_code_widget(client, headers, fake) -> None:
    fake.manifests[code_plugins.V1] = _code_manifest()
    fake.archives[code_plugins.V1] = code_plugins._tarball(WIDGET_SOURCE)
    plan = (await client.get(f"/api/plugins/{CODE_PLUGIN}/plan", params={"action": "install"}, headers=headers)).json()
    body = {"action": "install", "commit": plan["commit"], "token": plan["token"]}
    resp = await client.post(f"/api/plugins/{CODE_PLUGIN}/apply", json=body, headers=headers)
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_a_code_widget_runs_the_plugins_widget_method(
    client: AsyncClient, auth_headers: dict, code_catalog, plugin_runner, session, test_user, test_workspace
):
    await _install_code_widget(client, auth_headers, code_catalog)
    ledger = await _ledger_without_plugin(session, test_user, test_workspace)
    resp = await _widget(client, auth_headers, "spent", ledger["this_month"], ledger["today"], plugin=CODE_PLUGIN)
    assert resp.status_code == 200, resp.text
    kpi, text = resp.json()["view"]["blocks"]
    assert kpi["label"] == "Spent" and kpi["value"] > 0
    facts = json.loads(text["text"])
    assert facts == {
        "widget": "spent",
        "report": None,
        "period": [ledger["this_month"].isoformat(), ledger["today"].isoformat()],
        "securo": "spent",
    }


@pytest.mark.asyncio
async def test_a_failing_code_widget_is_an_error_for_its_card(
    client: AsyncClient, auth_headers: dict, code_catalog, plugin_runner, monkeypatch
):
    await _install_code_widget(client, auth_headers, code_catalog)
    today = app_today()
    broken = await _widget(client, auth_headers, "broken", today, today, plugin=CODE_PLUGIN)
    assert broken.status_code == 502 and "the widget fell over" in broken.json()["detail"]

    from app.plugins import runner

    monkeypatch.setattr(runner, "configured", lambda: False)
    assert (await _widget(client, auth_headers, "spent", today, today, plugin=CODE_PLUGIN)).status_code == 503
