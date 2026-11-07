"""Data plugins from the catalog: manifests, install, update, remove, and the API."""
import copy
import uuid
from datetime import date
from decimal import Decimal

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

PLUGIN = "maria/casa"
REPO = "https://github.com/maria/securo-casa"
V1, V2 = "a" * 40, "b" * 40
INDEX_URL = "https://raw.githubusercontent.com/securo-finance/plugins/main/index.json"

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
    """A fake catalog: the index and manifests by commit, served over httpx."""

    def __init__(self) -> None:
        self.entries: dict[str, dict] = {PLUGIN: {"id": PLUGIN, "repository": REPO, "commit": V1}}
        self.manifests: dict[str, dict] = {V1: copy.deepcopy(MANIFEST)}
        self.down = False

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("down")
        url = str(request.url)
        if url == INDEX_URL:
            return httpx.Response(200, json={"plugins": list(self.entries.values())})
        for commit, manifest in self.manifests.items():
            if url == f"https://raw.githubusercontent.com/maria/securo-casa/{commit}/securo-plugin.json":
                return httpx.Response(200, json=manifest)
        return httpx.Response(404)

    def publish(self, commit: str, change) -> None:
        manifest = copy.deepcopy(self.manifests[V1])
        change(manifest)
        self.manifests[commit] = manifest
        self.entries[PLUGIN]["commit"] = commit


@pytest.fixture
def catalog(monkeypatch) -> Catalog:
    fake = Catalog()
    monkeypatch.setattr(
        index, "http_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(fake.handle))
    )
    index._manifests.clear()
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


@pytest.mark.parametrize(
    "change",
    [
        lambda m: m.update(runtime={"entrypoint": "x:y", "lockfile": "uv.lock"}),
        lambda m: m.update(network=[{"host": "api.example.com", "purpose": "x"}]),
        lambda m: m["contributes"].update(widgets=[]),
        lambda m: m["contributes"]["rules"][0]["actions"].__setitem__(0, {"op": "set_category", "value": "nope"}),
        lambda m: m["contributes"]["rules"][0]["actions"].__setitem__(0, {"op": "set_payee", "value": "x"}),
        lambda m: m["contributes"]["rules"][0]["conditions"][0].update(field="account_id"),
        lambda m: m["contributes"]["rules"][0]["conditions"][0].update(op="regex", value="("),
        lambda m: m["contributes"]["categories"][0].update(group="nope"),
        lambda m: m.update(name={"pt-BR": "Casa"}),
        lambda m: m.update(contributes={}),
    ],
    ids=[
        "code", "network", "unsupported contribution", "unknown category", "set_payee needs an id",
        "account id", "bad regex", "unknown group", "translation without en", "contributes nothing",
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
    catalog.entries[PLUGIN] |= {"revoked": True, "revoked_reason": "malicious rules"}
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
    info = (await client.get("/api/info")).json()
    assert info["features"]["plugins"] is False


@pytest.mark.asyncio
async def test_api_catalog_plan_and_apply(client: AsyncClient, auth_headers: dict, catalog):
    entries = (await client.get("/api/plugins/catalog", headers=auth_headers)).json()
    assert entries[0]["id"] == PLUGIN
    assert entries[0]["contributes"] == {"category_groups": 1, "categories": 2, "rules": 2}
    assert entries[0]["compatible"] is True and entries[0]["installed_version"] is None

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

