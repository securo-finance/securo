"""Report plugins: a query and a chart in the manifest, added up and drawn by Securo (ADR 0028)."""
from __future__ import annotations

import copy
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
async def test_removing_the_plugin_takes_its_reports(client: AsyncClient, auth_headers: dict, installed):
    await _apply(client, auth_headers, "remove")
    today = installed["today"]
    assert (await _report(client, auth_headers, "pet-spending", today, today)).status_code == 404
