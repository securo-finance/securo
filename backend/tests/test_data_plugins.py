"""Data plugins from the catalog: manifests, install, update, remove, and the API."""
import copy
import hashlib
import json
import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import httpx
import pytest
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access_token_scopes import required_scope
from app.models.account import Account
from app.models.category import Category
from app.models.category_group import CategoryGroup
from app.models.rule import Rule
from app.models.transaction import Transaction
from app.models.workspace_plugin import WorkspacePlugin
from app.plugins import index, service
from app.plugins.manifest import parse_manifest
from app.plugins.provenance import is_untouched
from app.plugins.sync import SyncPlanChanged
from app.services.category_service import create_default_categories
from app.services.rule_engine import evaluate_conditions

PLUGIN = "maria/casa"
REPO = "https://github.com/maria/securo-casa"
V1, V2 = "a" * 40, "b" * 40
INDEX_URL = "https://plugins.usesecuro.com/v1/index.json"

MANIFEST = {
    "id": PLUGIN,
    "name": {"en": "Home", "pt-BR": "Casa"},
    "description": "Categories and rules for a home with pets.",
    "version": "1.0.0",
    "repository": REPO,
    "license": "MIT",
    "min_securo_version": "0.1.0",
    "contributes": {
        "category_groups": [{"key": "home", "name": {"en": "Home", "pt-BR": "Casa"}, "icon": "house"}],
        "categories": [
            {"key": "pets", "name": {"en": "Pets", "pt-BR": "Bichos"}, "group": "home", "icon": "dog"},
            {"key": "garden", "name": {"en": "Garden", "pt-BR": "Jardim"}, "group": "housing"},
        ],
        "rules": [
            {
                "key": "petshop", "name": "Pet shop", "conditions_op": "or",
                "conditions": [{"field": "description", "op": "contains", "value": "PETZ"}],
                "actions": [{"op": "set_category", "value": "pets"}], "priority": 10,
            },
            {
                "key": "garden-center", "name": "Garden center", "conditions_op": "and",
                "conditions": [{"field": "description", "op": "contains", "value": "GARDEN"}],
                "actions": [{"op": "set_category", "value": "groceries"}],
            },
        ],
    },
}


class Catalog:
    """A fake static catalog: the index, and manifest copies addressed by commit."""

    def __init__(self) -> None:
        self.manifests: dict[str, dict] = {V1: copy.deepcopy(MANIFEST)}
        self.commit = V1
        self.extra: dict = {}
        """Fields to override on the index entry (revoked, manifest path...)."""
        self.tampered = False
        self.down = False
        self.readme = b"# Home\n\nCategories for a home with pets."

    def _body(self, commit: str) -> bytes:
        return json.dumps(self.manifests[commit]).encode()

    def index(self) -> dict:
        m = self.manifests[self.commit]
        entry = {
            "id": PLUGIN, "repository": REPO, "release": f"v{m['version']}", "commit": self.commit,
            "manifest": f"manifests/{self.commit}.json",
            "manifest_sha256": hashlib.sha256(self._body(self.commit)).hexdigest(),
            "name": m["name"], "description": m["description"], "version": m["version"], "license": m["license"],
            "min_securo_version": m["min_securo_version"],
            "contributes": {kind: len(items) for kind, items in m["contributes"].items()},
            "stars": 12, "stars_7d": 3,
            "readme": f"readmes/{self.commit}.md", "readme_sha256": hashlib.sha256(self.readme).hexdigest(),
        }
        return {"plugins": [entry | self.extra]}

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("down")
        url = str(request.url)
        if url == INDEX_URL:
            return httpx.Response(200, json=self.index())
        for commit in self.manifests:
            if url == f"https://plugins.usesecuro.com/v1/manifests/{commit}.json":
                return httpx.Response(200, content=self._body(commit) + (b" " if self.tampered else b""))
        if url == f"https://plugins.usesecuro.com/v1/readmes/{self.commit}.md":
            return httpx.Response(200, content=self.readme + (b" " if self.tampered else b""))
        if url.startswith("https://evil.example.com/"):  # answers, so only the host check can refuse it
            return httpx.Response(200, content=self.readme)
        return httpx.Response(404)

    def publish(self, commit: str, change) -> None:
        manifest = copy.deepcopy(self.manifests[V1])
        change(manifest)
        self.manifests[commit] = manifest
        self.commit = commit


@pytest.fixture
def catalog(monkeypatch) -> Catalog:
    fake = Catalog()
    monkeypatch.setattr(
        index, "http_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(fake.handle))
    )
    index._manifests.clear()
    index._readmes.clear()
    monkeypatch.setenv("PLUGINS_ENABLED", "true")
    return fake


@pytest.fixture
def ids(test_user, test_workspace) -> tuple[uuid.UUID, uuid.UUID]:
    return test_workspace.id, test_user.id


async def _install(session, workspace_id, user_id, action="install", lang="en"):
    plan = await service.plan(session, workspace_id, PLUGIN, action, lang=lang)
    return await service.apply(
        session, workspace_id, user_id, PLUGIN, action, commit=plan.commit, token=plan.sync.token, lang=lang
    )


async def _of(session, model, workspace_id, **where):
    query = select(model).where(model.workspace_id == workspace_id).execution_options(populate_existing=True)
    for column, value in where.items():
        query = query.where(getattr(model, column) == value)
    return list((await session.execute(query)).scalars().all())


# --- The manifest -------------------------------------------------------------------


def test_the_example_manifest_is_valid():
    manifest = parse_manifest(MANIFEST)
    assert manifest.description == {"en": "Categories and rules for a home with pets."}


def test_an_empty_list_of_an_unknown_kind_contributes_nothing():
    manifest: dict[str, Any] = copy.deepcopy(MANIFEST)
    manifest["contributes"]["csv_presets"] = []
    assert parse_manifest(manifest).contributes.rules  # accepted, as the index counts it: zero


def test_a_condition_group_is_stored_the_way_the_rule_engine_reads_it():
    manifest: dict[str, Any] = copy.deepcopy(MANIFEST)
    manifest["contributes"]["rules"][0]["conditions"] = [
        {"op": "or", "conditions": [
            {"field": "description", "op": "contains", "value": "PETZ"},
            {"field": "description", "op": "contains", "value": "COBASI"},
        ]},
    ]
    rule = parse_manifest(manifest).contributes.rules[0].template()
    assert rule["conditions"][0]["op"] == "or"
    assert evaluate_conditions(rule["conditions_op"], rule["conditions"], Transaction(description="COBASI 12"))


@pytest.mark.parametrize(
    "change",
    [
        lambda m: m.update(runtime={"entrypoint": "x:y", "lockfile": "uv.lock"}),
        lambda m: m.update(network=[{"host": "api.example.com", "purpose": "x"}]),
        lambda m: m["contributes"].update(widgets=[{"key": "chart"}]),
        lambda m: m["contributes"]["rules"][0]["actions"].__setitem__(0, {"op": "set_category", "value": "nope"}),
        lambda m: m["contributes"]["rules"][0]["actions"].__setitem__(0, {"op": "set_payee", "value": "x"}),
        lambda m: m["contributes"]["rules"][0]["conditions"][0].update(field="account_id"),
        lambda m: m["contributes"]["rules"][0]["conditions"][0].update(op="regex", value="("),
        lambda m: m["contributes"]["categories"][0].update(group="nope"),
        lambda m: m.update(name={"pt-BR": "Casa"}),
        lambda m: m.update(contributes={}),
        lambda m: m["contributes"]["rules"][0]["conditions"][0].update(op="regex", value=".*"),
        lambda m: m["contributes"]["categories"][0].update(name={"en": "x" * 101}),
        lambda m: m["contributes"]["category_groups"][0].update(name={"en": "Home", "pt-BR": "x" * 101}),
        lambda m: m["contributes"]["rules"][1].update(name="pet SHOP"),
        lambda m: m.update(version="1.0." + "1" * 30),
    ],
    ids=[
        "code", "network", "unsupported contribution", "unknown category", "set_payee needs an id",
        "account id", "bad regex", "unknown group", "translation without en", "contributes nothing",
        "regex matching everything", "category name too long", "group translation too long",
        "duplicate rule names", "version too long",
    ],
)
def test_manifests_that_break_the_contract_are_rejected(change):
    manifest = copy.deepcopy(MANIFEST)
    change(manifest)
    with pytest.raises(ValidationError):
        parse_manifest(manifest)


# --- Installing ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_install_creates_everything_with_its_origin(session: AsyncSession, ids, catalog):
    workspace_id, user_id = ids
    await create_default_categories(session, user_id, lang="pt-BR")

    plan = await service.plan(session, workspace_id, PLUGIN, "install", lang="pt-BR")
    assert sorted((i.kind, i.key) for i in plan.sync.create) == [
        ("categories", "garden"), ("categories", "pets"), ("category_groups", "home"),
        ("rules", "garden-center"), ("rules", "petshop"),
    ]
    await service.apply(
        session, workspace_id, user_id, PLUGIN, "install", commit=V1, token=plan.sync.token, lang="pt-BR"
    )

    group = (await _of(session, CategoryGroup, workspace_id, source=PLUGIN))[0]
    categories = {c.source_key: c for c in await _of(session, Category, workspace_id, source=PLUGIN)}
    rules = {r.source_key: r for r in await _of(session, Rule, workspace_id, source=PLUGIN)}
    assert group.name == "Casa"
    assert categories["pets"].name == "Bichos" and categories["pets"].group_id == group.id
    moradia = (await _of(session, CategoryGroup, workspace_id, name="Moradia"))[0]
    assert categories["garden"].group_id == moradia.id
    assert rules["petshop"].actions == [{"op": "set_category", "value": str(categories["pets"].id)}]
    mercado = (await _of(session, Category, workspace_id, name="Mercado"))[0]
    assert rules["garden-center"].actions[0]["value"] == str(mercado.id)
    assert all(is_untouched(row) for row in [group, *categories.values(), *rules.values()])
    record = (await _of(session, WorkspacePlugin, workspace_id))[0]
    assert (record.plugin_id, record.version, record.commit) == (PLUGIN, "1.0.0", V1)


@pytest.mark.asyncio
async def test_what_the_workspace_already_has_is_left_alone(session: AsyncSession, ids, catalog):
    workspace_id, user_id = ids
    await create_default_categories(session, user_id, lang="en")
    session.add(Category(user_id=user_id, workspace_id=workspace_id, name="Pets"))
    session.add(Rule(user_id=user_id, workspace_id=workspace_id, name="Pet shop", conditions_op="and",
                     conditions=[{"field": "description", "op": "contains", "value": "X"}],
                     actions=[{"op": "append_notes", "value": "x"}]))
    await session.commit()

    plan = await service.plan(session, workspace_id, PLUGIN, "install", lang="en")
    assert sorted((i.kind, i.key) for i in plan.sync.existing) == [("categories", "pets"), ("rules", "petshop")]
    await service.apply(session, workspace_id, user_id, PLUGIN, "install", commit=V1, token=plan.sync.token, lang="en")
    assert len(await _of(session, Category, workspace_id, name="Pets")) == 1
    assert (await _of(session, Category, workspace_id, name="Pets"))[0].source is None


@pytest.mark.asyncio
async def test_missing_default_categories_are_created_first(session: AsyncSession, ids, catalog):
    workspace_id, user_id = ids  # no default categories at all
    plan = await service.plan(session, workspace_id, PLUGIN, "install", lang="en")
    assert plan.sync.default_categories == ("groceries",)
    await service.apply(session, workspace_id, user_id, PLUGIN, "install", commit=V1, token=plan.sync.token, lang="en")
    rule = (await _of(session, Rule, workspace_id, source_key="garden-center"))[0]
    groceries = (await _of(session, Category, workspace_id, name="Groceries"))[0]
    assert rule.actions[0]["value"] == str(groceries.id)


# --- Confirming exactly what was shown ----------------------------------------------------


@pytest.mark.asyncio
async def test_a_plan_is_refused_once_the_workspace_changed(session: AsyncSession, ids, catalog):
    workspace_id, user_id = ids
    plan = await service.plan(session, workspace_id, PLUGIN, "install", lang="en")
    session.add(Category(user_id=user_id, workspace_id=workspace_id, name="Pets"))
    await session.commit()
    with pytest.raises(SyncPlanChanged):
        await service.apply(session, workspace_id, user_id, PLUGIN, "install", commit=V1, token=plan.sync.token, lang="en")
    assert await _of(session, Category, workspace_id, source=PLUGIN) == []


@pytest.mark.asyncio
async def test_a_plan_is_refused_once_the_catalog_moved(session: AsyncSession, ids, catalog):
    workspace_id, user_id = ids
    plan = await service.plan(session, workspace_id, PLUGIN, "install", lang="en")
    catalog.publish(V2, lambda m: m.update(version="1.1.0"))
    with pytest.raises(service.PluginChanged):
        await service.apply(session, workspace_id, user_id, PLUGIN, "install", commit=V1, token=plan.sync.token, lang="en")


# --- Updating ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_follows_the_plugin_but_never_overrides_the_user(session: AsyncSession, ids, catalog):
    workspace_id, user_id = ids
    await create_default_categories(session, user_id, lang="en")
    await _install(session, workspace_id, user_id)
    edited = (await _of(session, Category, workspace_id, source_key="garden"))[0]
    edited.color = "#000000"
    await session.commit()

    def v2(m):
        m["version"] = "2.0.0"
        m["contributes"]["categories"][0]["icon"] = "cat"  # pets: changed upstream
        m["contributes"]["categories"][1]["icon"] = "flower"  # garden: changed, but the user edited it
        m["contributes"]["rules"].pop()  # garden-center: dropped
    catalog.publish(V2, v2)

    plan = await _install(session, workspace_id, user_id, action="update")
    assert [(i.kind, i.key) for i in plan.sync.update] == [("categories", "pets")]
    assert [(i.kind, i.key) for i in plan.sync.keep] == [("categories", "garden")]
    assert [(i.kind, i.key) for i in plan.sync.delete] == [("rules", "garden-center")]
    pets = (await _of(session, Category, workspace_id, source_key="pets"))[0]
    garden = (await _of(session, Category, workspace_id, source_key="garden"))[0]
    assert pets.icon == "cat" and is_untouched(pets)
    assert garden.icon != "flower" and garden.color == "#000000"
    assert await _of(session, Rule, workspace_id, source_key="garden-center") == []
    record = (await _of(session, WorkspacePlugin, workspace_id))[0]
    assert (record.version, record.commit) == ("2.0.0", V2)


@pytest.mark.asyncio
async def test_update_never_brings_back_a_default_category_the_user_deleted(session: AsyncSession, ids, catalog):
    workspace_id, user_id = ids
    await create_default_categories(session, user_id, lang="en")
    await _install(session, workspace_id, user_id)
    rule = (await _of(session, Rule, workspace_id, source_key="garden-center"))[0]
    rule.priority = 5  # edited, so the update leaves it alone
    groceries = (await _of(session, Category, workspace_id, name="Groceries"))[0]
    await session.delete(groceries)
    await session.commit()
    catalog.publish(V2, lambda m: m.update(version="1.1.0"))

    plan = await service.plan(session, workspace_id, PLUGIN, "update", lang="en")
    assert plan.sync.default_categories == ()
    await service.apply(session, workspace_id, user_id, PLUGIN, "update", commit=V2, token=plan.sync.token, lang="en")
    assert await _of(session, Category, workspace_id, name="Groceries") == []


@pytest.mark.asyncio
async def test_update_is_refused_when_already_up_to_date(session: AsyncSession, ids, catalog):
    workspace_id, user_id = ids
    await _install(session, workspace_id, user_id)
    with pytest.raises(service.PluginUnavailable):
        await service.plan(session, workspace_id, PLUGIN, "update", lang="en")


# --- Removing ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remove_keeps_history_and_what_the_user_made_theirs(session: AsyncSession, ids, catalog):
    workspace_id, user_id = ids
    await create_default_categories(session, user_id, lang="en")
    await _install(session, workspace_id, user_id)
    pets = (await _of(session, Category, workspace_id, source_key="pets"))[0]
    account = Account(user_id=user_id, workspace_id=workspace_id, name="Checking", type="checking",
                      balance=Decimal("0"), currency="USD")
    session.add(account)
    await session.flush()
    session.add(Transaction(user_id=user_id, workspace_id=workspace_id, account_id=account.id,
                            description="PETZ", amount=Decimal("40"), date=date(2026, 11, 1), type="debit",
                            currency="USD", category_id=pets.id, source="manual"))
    rule = (await _of(session, Rule, workspace_id, source_key="garden-center"))[0]
    rule.priority = 77  # edited
    pets_id = pets.id
    await session.commit()

    plan = await _install(session, workspace_id, user_id, action="remove")
    assert sorted((i.kind, i.key) for i in plan.sync.delete) == [("categories", "garden"), ("rules", "petshop")]
    assert sorted((i.kind, i.key) for i in plan.sync.release) == [
        ("categories", "pets"), ("category_groups", "home"), ("rules", "garden-center"),
    ]
    assert await _of(session, WorkspacePlugin, workspace_id) == []
    for model in (Rule, Category, CategoryGroup):
        assert await _of(session, model, workspace_id, source=PLUGIN) == []
    kept = (await _of(session, Category, workspace_id, name="Pets"))[0]
    assert kept.id == pets_id
    txn = (await _of(session, Transaction, workspace_id))[0]
    assert txn.category_id == pets_id


@pytest.mark.asyncio
async def test_a_revoked_plugin_cannot_be_installed_but_can_be_removed(session: AsyncSession, ids, catalog):
    workspace_id, user_id = ids
    await _install(session, workspace_id, user_id)
    catalog.extra |= {"revoked": True, "revoked_reason": "malicious rules"}
    with pytest.raises(service.PluginUnavailable, match="malicious rules"):
        await service.plan(session, workspace_id, PLUGIN, "update", lang="en")
    catalog.down = True  # removing doesn't need the catalog at all
    await _install(session, workspace_id, user_id, action="remove")
    assert await _of(session, Rule, workspace_id, source=PLUGIN) == []


@pytest.mark.asyncio
async def test_a_plugin_for_a_newer_securo_is_refused(session: AsyncSession, ids, catalog):
    workspace_id, _ = ids
    catalog.manifests[V1]["min_securo_version"] = "99.0.0"
    with pytest.raises(service.PluginUnavailable, match="99.0.0"):
        await service.plan(session, workspace_id, PLUGIN, "install", lang="en")


@pytest.mark.asyncio
async def test_a_manifest_naming_another_plugin_is_refused(session: AsyncSession, ids, catalog):
    workspace_id, _ = ids
    catalog.manifests[V1]["id"] = "evil/impostor"
    with pytest.raises(index.PluginIndexError):
        await service.plan(session, workspace_id, PLUGIN, "install", lang="en")


@pytest.mark.asyncio
async def test_a_manifest_that_does_not_match_its_checksum_is_refused(session: AsyncSession, ids, catalog):
    workspace_id, _ = ids
    catalog.tampered = True
    with pytest.raises(index.PluginIndexError, match="checksum"):
        await service.plan(session, workspace_id, PLUGIN, "install", lang="en")


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example.com/securo-plugin.json",
        "http://plugins.usesecuro.com/v1/securo-plugin.json",
        "https://user@plugins.usesecuro.com/v1/securo-plugin.json",
    ],
    ids=["other host", "plain http", "credentials"],
)
@pytest.mark.asyncio
async def test_manifests_are_read_only_from_the_catalog_host(session: AsyncSession, ids, catalog, url):
    workspace_id, _ = ids
    catalog.extra["manifest"] = url
    with pytest.raises(index.PluginIndexError, match="catalog's host"):
        await service.plan(session, workspace_id, PLUGIN, "install", lang="en")


@pytest.mark.asyncio
async def test_a_plugin_with_contributions_this_version_cannot_apply_is_listed_not_installable(
    session: AsyncSession, ids, catalog
):
    workspace_id, _ = ids
    catalog.extra["contributes"] = {"rules": 2, "csv_presets": 1}
    items = await service.catalog(session, workspace_id)
    assert items[0].entry.supported is False
    with pytest.raises(service.PluginUnavailable, match="can't apply"):
        await service.plan(session, workspace_id, PLUGIN, "install", lang="en")


@pytest.mark.asyncio
async def test_an_invalid_index_entry_is_skipped(session: AsyncSession, ids, catalog):
    workspace_id, _ = ids
    catalog.extra["commit"] = "not-a-commit"
    assert await service.catalog(session, workspace_id) == []


@pytest.mark.asyncio
async def test_built_in_pack_ids_are_not_catalog_plugins(session: AsyncSession, ids, catalog):
    workspace_id, _ = ids
    with pytest.raises(service.PluginNotFound):
        await service.plan(session, workspace_id, "securo/rules-br", "remove", lang="en")


@pytest.mark.asyncio
async def test_removal_only_touches_its_own_workspace(session: AsyncSession, ids, catalog):
    from app.models.workspace import Workspace, WorkspaceMember

    workspace_id, user_id = ids
    other_id = uuid.uuid4()
    session.add(Workspace(id=other_id, name="Other", kind="personal", default_currency="USD", locale="en"))
    await session.flush()
    session.add(WorkspaceMember(id=uuid.uuid4(), workspace_id=other_id, user_id=user_id, role="owner"))
    await session.commit()
    await _install(session, workspace_id, user_id)
    await _install(session, other_id, user_id)

    await _install(session, workspace_id, user_id, action="remove")
    assert await _of(session, Rule, workspace_id, source=PLUGIN) == []
    assert len(await _of(session, Rule, other_id, source=PLUGIN)) == 2


# --- API ------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_everything_is_hidden_while_plugins_are_off(client: AsyncClient, auth_headers: dict, monkeypatch):
    monkeypatch.delenv("PLUGINS_ENABLED", raising=False)
    assert (await client.get("/api/plugins/catalog", headers=auth_headers)).status_code == 404
    assert (await client.get(f"/api/plugins/{PLUGIN}", headers=auth_headers)).status_code == 404
    info = (await client.get("/api/info")).json()
    assert info["features"]["plugins"] is False


@pytest.mark.asyncio
async def test_api_catalog_plan_and_apply(client: AsyncClient, auth_headers: dict, catalog):
    entries = (await client.get("/api/plugins/catalog", headers=auth_headers)).json()
    assert entries[0]["id"] == PLUGIN
    assert entries[0]["contributes"] == {"category_groups": 1, "categories": 2, "rules": 2}
    assert (entries[0]["owner"], entries[0]["stars"], entries[0]["stars_7d"]) == ("maria", 12, 3)
    assert entries[0]["compatible"] is True and entries[0]["supported"] is True
    assert entries[0]["installed_version"] is None
    assert "manifest_sha256" not in entries[0]

    plan = (await client.get(f"/api/plugins/{PLUGIN}/plan", params={"action": "install"}, headers=auth_headers)).json()
    body = {"action": "install", "commit": plan["commit"], "token": plan["token"]}
    resp = await client.post(f"/api/plugins/{PLUGIN}/apply", json=body, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert (await client.post(f"/api/plugins/{PLUGIN}/apply", json=body, headers=auth_headers)).status_code == 409

    entries = (await client.get("/api/plugins/catalog", headers=auth_headers)).json()
    assert entries[0]["installed_version"] == "1.0.0" and entries[0]["update_available"] is False

    catalog.down = True  # the installed list reads only the database
    installed = (await client.get("/api/plugins", headers=auth_headers)).json()
    assert [(p["plugin_id"], p["version"], p["name"]["pt-BR"]) for p in installed] == [(PLUGIN, "1.0.0", "Casa")]


@pytest.mark.asyncio
async def test_api_viewer_can_look_but_not_apply(
    client: AsyncClient, auth_headers: dict, viewer_auth_headers: dict, catalog
):
    assert (await client.get("/api/plugins/catalog", headers=viewer_auth_headers)).status_code == 200
    plan = (await client.get(f"/api/plugins/{PLUGIN}/plan", params={"action": "install"}, headers=viewer_auth_headers)).json()
    body = {"action": "install", "commit": plan["commit"], "token": plan["token"]}
    resp = await client.post(f"/api/plugins/{PLUGIN}/apply", json=body, headers=viewer_auth_headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_api_unreachable_catalog_is_502(client: AsyncClient, auth_headers: dict, catalog):
    catalog.down = True
    assert (await client.get("/api/plugins/catalog", headers=auth_headers)).status_code == 502


def test_access_tokens_cannot_reach_plugins():
    assert required_scope("GET", "/api/plugins/catalog") is None
    assert required_scope("POST", f"/api/plugins/{PLUGIN}/apply") is None


# --- Opening a plugin ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_details_show_the_readme_and_what_the_plugin_contains(client: AsyncClient, auth_headers, catalog):
    response = await client.get(f"/api/plugins/{PLUGIN}", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["plugin"]["id"] == PLUGIN and "readme" not in body["plugin"]
    assert body["readme"].startswith("# Home")
    contents = body["contents"]
    assert [g["key"] for g in contents["category_groups"]] == ["home"]
    pets, garden = contents["categories"]
    assert pets["group"] == {"key": "home", "name": {"en": "Home", "pt-BR": "Casa"}, "default": False}
    assert garden["group"]["default"] is True and garden["group"]["name"]["en"]
    petshop, garden_center = contents["rules"]
    assert petshop["actions"][0]["category"]["name"]["pt-BR"] == "Bichos"
    assert garden_center["actions"][0]["category"]["default"] is True


@pytest.mark.asyncio
async def test_details_leave_out_what_does_not_match_its_checksum(client: AsyncClient, auth_headers, catalog):
    catalog.tampered = True
    body = (await client.get(f"/api/plugins/{PLUGIN}", headers=auth_headers)).json()
    assert body["readme"] is None and body["contents"] is None
    assert body["plugin"]["id"] == PLUGIN


@pytest.mark.asyncio
async def test_details_never_read_a_readme_off_the_catalog_host(client: AsyncClient, auth_headers, catalog):
    catalog.extra = {"readme": "https://evil.example.com/readme.md"}
    body = (await client.get(f"/api/plugins/{PLUGIN}", headers=auth_headers)).json()
    assert body["readme"] is None and body["contents"] is not None


@pytest.mark.asyncio
async def test_details_of_a_plugin_this_securo_cannot_apply_have_no_contents(
    client: AsyncClient, auth_headers, catalog
):
    catalog.extra = {"contributes": {"widgets": 2}}
    body = (await client.get(f"/api/plugins/{PLUGIN}", headers=auth_headers)).json()
    assert body["contents"] is None and body["readme"].startswith("# Home")
    assert (await client.get("/api/plugins/nobody/nothing", headers=auth_headers)).status_code == 404


# --- What the catalog can't slip past a preview ------------------------------------------


@pytest.mark.asyncio
async def test_a_cached_manifest_still_has_to_be_the_plugin_the_entry_names(session: AsyncSession, ids, catalog):
    async with index.http_client() as client:
        entry = (await index.fetch_index(client))[0]
        await index.fetch_manifest(client, entry)  # cached by its checksum
        other = entry.model_copy(update={"id": "joao/other", "repository": "https://github.com/joao/other"})
        with pytest.raises(index.PluginIndexError, match="isn't the plugin"):
            await index.fetch_manifest(client, other)


@pytest.mark.asyncio
async def test_the_manifest_decides_the_minimum_version(session: AsyncSession, ids, catalog):
    workspace_id, _ = ids
    catalog.publish(V2, lambda m: m.update(min_securo_version="99.0.0"))
    catalog.extra = {"min_securo_version": "0.1.0"}  # the index summary says otherwise
    with pytest.raises(service.PluginUnavailable, match="99.0.0"):
        await service.plan(session, workspace_id, PLUGIN, "install", lang="en")


@pytest.mark.asyncio
async def test_a_preview_is_refused_once_the_manifest_copy_changed(session: AsyncSession, ids, catalog):
    workspace_id, user_id = ids
    plan = await service.plan(session, workspace_id, PLUGIN, "install", lang="en")
    # Same commit and the same items, but a rule that now does something else.
    catalog.manifests[V1]["contributes"]["rules"][0]["actions"] = [{"op": "set_category", "value": "garden"}]
    index._manifests.clear()
    with pytest.raises(SyncPlanChanged):
        await service.apply(session, workspace_id, user_id, PLUGIN, "install", commit=V1, token=plan.sync.token, lang="en")


@pytest.mark.asyncio
async def test_an_update_never_renames_a_rule_onto_another(session: AsyncSession, ids, catalog):
    workspace_id, user_id = ids
    await _install(session, workspace_id, user_id)
    session.add(Rule(user_id=user_id, workspace_id=workspace_id, name="Vet", conditions_op="and",
                     conditions=[{"field": "description", "op": "contains", "value": "VET"}], actions=[]))
    await session.commit()
    catalog.publish(V2, lambda m: m["contributes"]["rules"][0].update(name="Vet"))
    result = await _install(session, workspace_id, user_id, action="update")
    assert ("rules", "petshop") in {(i.kind, i.key) for i in result.sync.existing}
    assert len(await _of(session, Rule, workspace_id, name="Vet")) == 1
    assert len(await _of(session, Rule, workspace_id, name="Pet shop")) == 1


@pytest.mark.asyncio
async def test_a_default_group_created_for_a_rule_also_holds_the_plugins_category(
    session: AsyncSession, ids, catalog
):
    workspace_id, user_id = ids  # no default categories, so no Housing group either
    # The garden-center rule now needs the default `housing` category, which brings its group.
    catalog.manifests[V1]["contributes"]["rules"][1]["actions"] = [{"op": "set_category", "value": "housing"}]
    await _install(session, workspace_id, user_id)
    garden = (await _of(session, Category, workspace_id, source_key="garden"))[0]
    housing = (await _of(session, Category, workspace_id, name="Housing"))[0]
    assert garden.group_id is not None and garden.group_id == housing.group_id
