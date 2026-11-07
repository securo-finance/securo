"""Rule packs as data files, the origin they leave on rules, and removing them."""
import importlib.util
import json
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.category import Category
from app.models.rule import Rule
from app.models.transaction import Transaction
from app.models.workspace import Workspace, WorkspaceMember
from app.services.category_service import create_default_categories
from app.plugins.provenance import rule_content_hash
from app.plugins.sync import SyncPlanChanged
from app.services.rule_packs import (
    PACK_FILES,
    PACKS_DIR,
    built_in_packs,
    find_pack_rule,
    install_rule_pack,
    list_rule_packs,
    load_pack,
    plan_rule_pack_removal,
    remove_rule_pack,
)
from app.services.rule_service import validate_rule_definition


@pytest.fixture
def ids(test_user, test_workspace) -> tuple[uuid.UUID, uuid.UUID]:
    """(workspace id, user id), read before any commit expires the fixtures."""
    return test_workspace.id, test_user.id


async def _rules(session: AsyncSession, workspace_id: uuid.UUID) -> list[Rule]:
    query = select(Rule).where(Rule.workspace_id == workspace_id).execution_options(populate_existing=True)
    return list((await session.execute(query)).scalars().all())


async def _status(session: AsyncSession, workspace_id: uuid.UUID, code: str):
    return next(s for s in await list_rule_packs(session, workspace_id) if s.pack.code == code)


async def _remove(session: AsyncSession, workspace_id: uuid.UUID, code: str):
    plan = await plan_rule_pack_removal(session, workspace_id, code)
    assert plan is not None
    return await remove_rule_pack(session, workspace_id, uuid.uuid4(), code, plan.token)


def _content(rule: Rule) -> dict:
    return {
        "name": rule.name,
        "conditions_op": rule.conditions_op,
        "conditions": rule.conditions,
        "actions": rule.actions,
        "priority": rule.priority,
    }


# --- The data files ---------------------------------------------------------------


def test_every_pack_file_is_listed_and_named_after_its_id():
    assert sorted(p.stem for p in PACKS_DIR.glob("*.json")) == sorted(PACK_FILES)
    packs = built_in_packs()
    assert list(packs) == ["BR", "US", "EU", "GB"]
    for name in PACK_FILES:
        assert packs[name.upper()].source == f"securo/rules-{name}"


def test_plain_text_is_english():
    manifest = json.loads((PACKS_DIR / "br.json").read_text(encoding="utf-8"))
    assert isinstance(manifest["contributes"]["rules"][0]["name"], str)
    pack = built_in_packs()["BR"]
    assert pack.names == {"en": "Brazil", "pt-BR": "Brasil"}
    assert pack.name == "Brazil"


def test_flags():
    assert built_in_packs()["BR"].flag == "\U0001F1E7\U0001F1F7"
    assert built_in_packs()["EU"].flag == "\U0001F1EA\U0001F1FA"


@pytest.mark.parametrize(
    "change",
    [
        lambda m: m.update(network=[{"host": "example.com", "purpose": "x"}]),
        lambda m: m.update(runtime={"entrypoint": "x:y"}),
        lambda m: m["contributes"]["rules"][0].update(key="Not A Key"),
        lambda m: m.update(name={"pt-BR": "Brasil"}),
        lambda m: m.update(min_securo_version=">=0.19"),
        lambda m: m.pop("min_securo_version"),
        lambda m: m["contributes"]["rules"].append(m["contributes"]["rules"][0]),
        lambda m: m["contributes"].update(categories=[{"key": "pets", "name": "Pets"}]),
    ],
    ids=[
        "data pack reaches the network", "data pack runs code", "bad key", "translation without en",
        "a range instead of a minimum", "no minimum version", "duplicate key", "not just rules",
    ],
)
def test_a_pack_that_breaks_the_contract_is_rejected(tmp_path, change):
    manifest = json.loads((PACKS_DIR / "br.json").read_text(encoding="utf-8"))
    change(manifest)
    path = tmp_path / "br.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError):  # a pydantic ValidationError is one too
        load_pack(path)


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["BR", "US", "EU", "GB"])
async def test_every_pack_installs_whole_and_passes_the_rule_validator(session: AsyncSession, ids, code):
    workspace_id, user_id = ids
    result = await install_rule_pack(session, workspace_id, user_id, code, "en", create_missing_categories=True)
    assert result.unresolved == 0
    assert len(result.rules) == len(built_in_packs()[code].rules)
    for rule in result.rules:
        await validate_rule_definition(session, workspace_id, rule.conditions, rule.actions)


# --- Installing records the origin --------------------------------------------------


@pytest.mark.asyncio
async def test_installed_rules_record_their_pack(session: AsyncSession, ids):
    workspace_id, user_id = ids
    await create_default_categories(session, user_id, lang="pt-BR")
    rules = (await install_rule_pack(session, workspace_id, user_id, "BR")).rules

    pack = built_in_packs()["BR"]
    assert {r.source for r in rules} == {pack.source}
    assert len(rules) == len(pack.rules)
    assert {r.source_key for r in rules} == {r.key for r in pack.rules}
    assert all(r.source_hash == rule_content_hash(**_content(r)) for r in rules)


@pytest.mark.asyncio
async def test_a_renamed_pack_rule_is_not_installed_again(session: AsyncSession, ids):
    workspace_id, user_id = ids
    await create_default_categories(session, user_id, lang="pt-BR")
    first = await install_rule_pack(session, workspace_id, user_id, "BR")
    first.rules[0].name = "Meu nome"
    await session.commit()

    second = await install_rule_pack(session, workspace_id, user_id, "BR")
    assert second.rules == []
    assert (await _status(session, workspace_id, "BR")).installed


# --- Removing a pack ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_removing_a_pack_keeps_what_the_user_edited(session: AsyncSession, ids):
    workspace_id, user_id = ids
    await create_default_categories(session, user_id, lang="pt-BR")
    installed = (await install_rule_pack(session, workspace_id, user_id, "BR")).rules
    edited, switched_off = installed[0], installed[1]
    edited.priority += 7
    switched_off.is_active = False
    mine = Rule(
        user_id=user_id, workspace_id=workspace_id, name="Mine", conditions_op="and",
        conditions=[{"field": "description", "op": "contains", "value": "X"}],
        actions=[{"op": "append_notes", "value": "x"}], priority=1,
    )
    session.add(mine)
    await session.flush()
    edited_id, switched_off_id, mine_id = edited.id, switched_off.id, mine.id
    await session.commit()

    plan = await plan_rule_pack_removal(session, workspace_id, "BR")
    assert plan is not None
    assert [r.id for r in plan.keep] == [edited_id]
    assert len(plan.remove) == len(installed) - 1
    assert switched_off_id in {r.id for r in plan.remove}, "switching a rule off doesn't make it the user's"

    removal = await remove_rule_pack(session, workspace_id, user_id, "BR", plan.token)
    assert removal == plan
    left = {r.id: r for r in await _rules(session, workspace_id)}
    assert set(left) == {edited_id, mine_id}
    assert (left[edited_id].source, left[edited_id].source_key, left[edited_id].source_hash) == (None, None, None)
    status = await _status(session, workspace_id, "BR")
    assert (status.installed, status.rules_from_pack) == (False, 0)


@pytest.mark.asyncio
async def test_removing_a_pack_leaves_categorized_transactions_alone(session: AsyncSession, ids):
    workspace_id, user_id = ids
    await create_default_categories(session, user_id, lang="pt-BR")
    rules = (await install_rule_pack(session, workspace_id, user_id, "BR")).rules
    category_id = uuid.UUID(next(a["value"] for r in rules for a in r.actions if a["op"] == "set_category"))
    account = Account(
        user_id=user_id, workspace_id=workspace_id, name="Conta", type="checking",
        balance=Decimal("0"), currency="BRL",
    )
    session.add(account)
    await session.flush()
    txn = Transaction(
        user_id=user_id, workspace_id=workspace_id, account_id=account.id, description="IFOOD",
        amount=Decimal("30"), date=date(2026, 11, 1), type="debit", currency="BRL",
        category_id=category_id, source="manual",
    )
    session.add(txn)
    await session.flush()
    txn_id = txn.id
    await session.commit()

    await _remove(session, workspace_id, "BR")
    reloaded = await session.get(Transaction, txn_id, populate_existing=True)
    assert reloaded is not None and reloaded.category_id == category_id


@pytest.mark.asyncio
async def test_removal_only_touches_its_own_workspace(session: AsyncSession, ids):
    workspace_id, user_id = ids
    other_id = uuid.uuid4()
    session.add(Workspace(id=other_id, name="Other", kind="personal", default_currency="BRL", locale="pt-BR"))
    await session.flush()
    session.add(WorkspaceMember(id=uuid.uuid4(), workspace_id=other_id, user_id=user_id, role="owner"))
    await session.commit()
    await install_rule_pack(session, workspace_id, user_id, "BR", create_missing_categories=True)
    await install_rule_pack(session, other_id, user_id, "BR", create_missing_categories=True)

    await _remove(session, workspace_id, "BR")
    assert await _rules(session, workspace_id) == []
    assert len(await _rules(session, other_id)) == len(built_in_packs()["BR"].rules)


@pytest.mark.asyncio
async def test_removal_refuses_a_plan_that_changed(session: AsyncSession, ids):
    """An edit saved after the preview changes the plan: nothing is removed."""
    workspace_id, user_id = ids
    await create_default_categories(session, user_id, lang="pt-BR")
    rules = (await install_rule_pack(session, workspace_id, user_id, "BR")).rules
    plan = await plan_rule_pack_removal(session, workspace_id, "BR")
    assert plan is not None
    rules[0].priority += 1
    await session.commit()

    with pytest.raises(SyncPlanChanged):
        await remove_rule_pack(session, workspace_id, user_id, "BR", plan.token)
    assert len(await _rules(session, workspace_id)) == len(built_in_packs()["BR"].rules)


@pytest.mark.asyncio
async def test_removal_rereads_rules_this_session_already_holds(session: AsyncSession, ids):
    """An edit saved elsewhere after the preview is seen, even though this
    session still holds the rules as they were."""
    workspace_id, user_id = ids
    await create_default_categories(session, user_id, lang="pt-BR")
    rules = (await install_rule_pack(session, workspace_id, user_id, "BR")).rules
    plan = await plan_rule_pack_removal(session, workspace_id, "BR")
    assert plan is not None
    rule_id = rules[0].id
    # Another session's edit: straight to the database, this session's copy untouched.
    await session.execute(update(Rule).where(Rule.id == rule_id).values(priority=99).execution_options(
        synchronize_session=False
    ))
    await session.commit()

    with pytest.raises(SyncPlanChanged):
        await remove_rule_pack(session, workspace_id, user_id, "BR", plan.token)


@pytest.mark.asyncio
async def test_a_pack_whose_remaining_rules_are_all_edited_can_still_be_removed(session: AsyncSession, ids):
    """Nothing to delete, but the edited rules are handed over to the user."""
    workspace_id, user_id = ids
    await create_default_categories(session, user_id, lang="pt-BR")
    for rule in (await install_rule_pack(session, workspace_id, user_id, "BR")).rules:
        rule.priority += 1
    await session.commit()

    plan = await plan_rule_pack_removal(session, workspace_id, "BR")
    assert plan is not None
    assert (len(plan.remove), len(plan.keep)) == (0, len(built_in_packs()["BR"].rules))
    await remove_rule_pack(session, workspace_id, user_id, "BR", plan.token)
    assert (await _status(session, workspace_id, "BR")).rules_from_pack == 0


@pytest.mark.asyncio
async def test_unknown_pack(session: AsyncSession, ids):
    workspace_id, _ = ids
    assert await plan_rule_pack_removal(session, workspace_id, "ZZ") is None
    assert await remove_rule_pack(session, workspace_id, uuid.uuid4(), "ZZ", "any") is None


# --- Rules installed before packs recorded their origin ------------------------------------


@pytest.mark.asyncio
async def test_untouched_old_rules_are_recognized_and_edited_ones_are_not(session: AsyncSession, ids):
    """What migration 107 relies on."""
    workspace_id, user_id = ids
    await create_default_categories(session, user_id, lang="en")
    rules = (await install_rule_pack(session, workspace_id, user_id, "BR")).rules
    for rule in rules:  # as if installed before origins were recorded
        rule.source = rule.source_key = rule.source_hash = None
    await session.commit()
    category_names = {
        str(c.id): c.name
        for c in (await session.execute(select(Category).where(Category.workspace_id == workspace_id))).scalars()
    }

    def match(rule: Rule, **override):
        found = find_pack_rule(**(_content(rule) | override), category_names=category_names)
        return found and (found[0].source, found[1].key)

    pack = built_in_packs()["BR"]
    key_by_name = {r.name: r.key for r in pack.rules}
    assert all(match(rule) == (pack.source, key_by_name[rule.name]) for rule in rules)

    sample = rules[0]
    assert not match(sample, priority=sample.priority + 1)
    assert not match(sample, name=sample.name + " (meu)")
    assert not match(sample, conditions=[{"field": "description", "op": "contains", "value": "OUTRA"}])
    other_category = next(cid for cid in category_names if cid not in {a["value"] for a in sample.actions})
    assert not match(sample, actions=[{"op": "set_category", "value": other_category}])

    # Without a recorded origin the pack still reads as installed, by name.
    assert (await _status(session, workspace_id, "BR")).installed


def _migration_107():
    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "107_rule_pack_provenance.py"
    spec = importlib.util.spec_from_file_location("migration_107", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_migration_marks_untouched_old_rules_and_changes_no_content(session: AsyncSession, ids):
    workspace_id, user_id = ids
    await create_default_categories(session, user_id, lang="en")
    rules = (await install_rule_pack(session, workspace_id, user_id, "BR")).rules
    for rule in rules:  # as installed before origins were recorded
        rule.source = rule.source_key = rule.source_hash = None
    rules[0].priority += 1  # edited by the user
    mine = Rule(
        user_id=user_id, workspace_id=workspace_id, name="Mine", conditions_op="and",
        conditions=[{"field": "description", "op": "contains", "value": "X"}],
        actions=[{"op": "append_notes", "value": "x"}], priority=1,
    )
    session.add(mine)
    await session.flush()
    edited_id, mine_id = rules[0].id, mine.id
    await session.commit()
    before = {r.id: _content(r) for r in await _rules(session, workspace_id)}

    mark = _migration_107().mark_rules_from_packs
    marked = await session.run_sync(lambda sync: mark(sync.connection()))
    await session.commit()

    after = {r.id: r for r in await _rules(session, workspace_id)}
    assert marked == len(rules) - 1
    assert {i: _content(r) for i, r in after.items()} == before, "no rule's content changes"
    assert after[edited_id].source is None
    assert after[mine_id].source is None
    pack = built_in_packs()["BR"]
    for rule_id, rule in after.items():
        if rule_id in (edited_id, mine_id):
            continue
        assert (rule.source, rule.source_hash) == (pack.source, rule_content_hash(**_content(rule)))

    # Running it again marks nothing new.
    assert await session.run_sync(lambda sync: mark(sync.connection())) == 0


def test_two_packs_sharing_a_rule_name_are_told_apart_by_content():
    """US and GB both have "Rent / Mortgage", with different conditions."""
    us = next(r for r in built_in_packs()["US"].rules if r.name == "Rent / Mortgage")
    found = find_pack_rule(
        name=us.name, conditions_op=us.conditions_op, conditions=us.template()["conditions"],
        actions=[{"op": "set_category", "value": "cat-1"}], priority=us.priority,
        category_names={"cat-1": "Housing"},
    )
    assert found is not None
    assert (found[0].source, found[1].key) == ("securo/rules-us", us.key)


# --- API ---------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_lists_previews_and_removes_a_pack(client: AsyncClient, auth_headers: dict):
    resp = await client.post(
        "/api/rules/packs/BR/install", headers=auth_headers, params={"create_missing_categories": True}
    )
    assert resp.status_code == 200, resp.text
    total = len(built_in_packs()["BR"].rules)

    packs = {p["code"]: p for p in (await client.get("/api/rules/packs", headers=auth_headers)).json()}
    assert (packs["BR"]["installed"], packs["BR"]["rules_from_pack"]) == (True, total)
    assert packs["BR"]["names"]["pt-BR"] == "Brasil"
    assert packs["US"]["rules_from_pack"] == 0

    rules = (await client.get("/api/rules", headers=auth_headers)).json()
    edited = next(r for r in rules if r["name"] == "iFood / Rappi")
    resp = await client.patch(f"/api/rules/{edited['id']}", headers=auth_headers, json={"priority": 99})
    assert resp.status_code == 200, resp.text

    preview = (await client.get("/api/rules/packs/BR/removal", headers=auth_headers)).json()
    assert preview["keep"] == [{"id": edited["id"], "name": "iFood / Rappi"}]
    assert len(preview["remove"]) == total - 1

    resp = await client.delete("/api/rules/packs/BR", headers=auth_headers, params={"token": preview["token"]})
    assert resp.status_code == 200
    assert resp.json() == preview
    names = [r["name"] for r in (await client.get("/api/rules", headers=auth_headers)).json()]
    assert "iFood / Rappi" in names
    packs = {p["code"]: p for p in (await client.get("/api/rules/packs", headers=auth_headers)).json()}
    assert (packs["BR"]["installed"], packs["BR"]["rules_from_pack"]) == (False, 0)


@pytest.mark.asyncio
async def test_api_viewer_can_preview_but_not_remove(
    client: AsyncClient, auth_headers: dict, viewer_auth_headers: dict
):
    await client.post("/api/rules/packs/BR/install", headers=auth_headers, params={"create_missing_categories": True})
    assert (await client.get("/api/rules/packs/BR/removal", headers=viewer_auth_headers)).status_code == 200
    token = (await client.get("/api/rules/packs/BR/removal", headers=viewer_auth_headers)).json()["token"]
    resp = await client.delete("/api/rules/packs/BR", headers=viewer_auth_headers, params={"token": token})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_api_unknown_pack_is_404(client: AsyncClient, auth_headers: dict):
    assert (await client.get("/api/rules/packs/ZZ/removal", headers=auth_headers)).status_code == 404
    resp = await client.delete("/api/rules/packs/ZZ", headers=auth_headers, params={"token": "x"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_api_refuses_a_stale_or_foreign_preview(
    client: AsyncClient, auth_headers: dict, session: AsyncSession, test_user
):
    """A preview from before an edit, or from another workspace, removes nothing."""
    install = {"create_missing_categories": True}
    await client.post("/api/rules/packs/BR/install", headers=auth_headers, params=install)
    stale = (await client.get("/api/rules/packs/BR/removal", headers=auth_headers)).json()["token"]
    rule = next(r for r in (await client.get("/api/rules", headers=auth_headers)).json() if r["name"] == "Aluguel")
    await client.patch(f"/api/rules/{rule['id']}", headers=auth_headers, json={"priority": 77})

    resp = await client.delete("/api/rules/packs/BR", headers=auth_headers, params={"token": stale})
    assert resp.status_code == 409

    other_id = uuid.uuid4()
    session.add(Workspace(id=other_id, name="Other", kind="personal", default_currency="BRL", locale="pt-BR"))
    await session.flush()
    session.add(WorkspaceMember(id=uuid.uuid4(), workspace_id=other_id, user_id=test_user.id, role="owner"))
    await session.commit()
    other = {**auth_headers, "X-Workspace-Id": str(other_id)}
    await client.post("/api/rules/packs/BR/install", headers=other, params=install)
    fresh_here = (await client.get("/api/rules/packs/BR/removal", headers=auth_headers)).json()["token"]

    resp = await client.delete("/api/rules/packs/BR", headers=other, params={"token": fresh_here})
    assert resp.status_code == 409
    total = len(built_in_packs()["BR"].rules)
    for headers in (auth_headers, other):
        packs = {p["code"]: p for p in (await client.get("/api/rules/packs", headers=headers)).json()}
        assert packs["BR"]["rules_from_pack"] == total
