"""Rule packs as data files, the origin they leave on rules, and removing them."""
import json
import uuid
from datetime import date
from decimal import Decimal

import pytest
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.category import Category
from app.models.rule import Rule
from app.models.transaction import Transaction
from app.models.workspace import Workspace, WorkspaceMember
from app.services.category_service import create_default_categories
from app.services.rule_packs import (
    PACK_FILES,
    PACKS_DIR,
    built_in_packs,
    find_pack_rule,
    load_pack,
    rule_content_hash,
)
from app.services.rule_service import (
    _validate_rule_definition,
    install_rule_pack,
    list_rule_packs,
    plan_rule_pack_removal,
    remove_rule_pack,
)


@pytest.fixture
def ids(test_user, test_workspace) -> tuple[uuid.UUID, uuid.UUID]:
    """(workspace id, user id), read before any commit expires the fixtures."""
    return test_workspace.id, test_user.id


async def _rules(session: AsyncSession, workspace_id: uuid.UUID) -> list[Rule]:
    query = select(Rule).where(Rule.workspace_id == workspace_id).execution_options(populate_existing=True)
    return list((await session.execute(query)).scalars().all())


async def _status(session: AsyncSession, workspace_id: uuid.UUID, code: str):
    return next(s for s in await list_rule_packs(session, workspace_id) if s.pack.code == code)


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


def test_flags():
    assert built_in_packs()["BR"].flag == "\U0001F1E7\U0001F1F7"
    assert built_in_packs()["EU"].flag == "\U0001F1EA\U0001F1FA"


@pytest.mark.parametrize(
    "change",
    [
        lambda m: m["privacy"].update(data_leaves_instance=True),
        lambda m: m["contributes"]["rules"][0].update(key="Not A Key"),
        lambda m: m.update(default_locale="fr"),
        lambda m: m.update(manifest_version=2),
        lambda m: m["contributes"]["rules"].append(m["contributes"]["rules"][0]),
        lambda m: m["contributes"].update(categories=[]),
    ],
    ids=["leaks data", "bad key", "no default-locale text", "unknown version", "duplicate key", "not just rules"],
)
def test_a_pack_that_breaks_the_contract_is_rejected(tmp_path, change):
    manifest = json.loads((PACKS_DIR / "br.json").read_text(encoding="utf-8"))
    change(manifest)
    path = tmp_path / "br.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_pack(path)


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["BR", "US", "EU", "GB"])
async def test_every_pack_installs_whole_and_passes_the_rule_validator(session: AsyncSession, ids, code):
    workspace_id, user_id = ids
    result = await install_rule_pack(session, workspace_id, user_id, code, "en", create_missing_categories=True)
    assert result.unresolved == 0
    assert len(result.rules) == len(built_in_packs()[code].rules)
    for rule in result.rules:
        await _validate_rule_definition(session, workspace_id, rule.conditions, rule.actions)


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

    removal = await remove_rule_pack(session, workspace_id, "BR")
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

    await remove_rule_pack(session, workspace_id, "BR")
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

    await remove_rule_pack(session, workspace_id, "BR")
    assert await _rules(session, workspace_id) == []
    assert len(await _rules(session, other_id)) == len(built_in_packs()["BR"].rules)


@pytest.mark.asyncio
async def test_unknown_pack(session: AsyncSession, ids):
    workspace_id, _ = ids
    assert await plan_rule_pack_removal(session, workspace_id, "ZZ") is None
    assert await remove_rule_pack(session, workspace_id, "ZZ") is None


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


def test_two_packs_sharing_a_rule_name_are_told_apart_by_content():
    """US and GB both have "Rent / Mortgage", with different conditions."""
    us = next(r for r in built_in_packs()["US"].rules if r.name == "Rent / Mortgage")
    found = find_pack_rule(
        name=us.name, conditions_op=us.conditions_op, conditions=us.conditions,
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

    resp = await client.delete("/api/rules/packs/BR", headers=auth_headers)
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
    assert (await client.delete("/api/rules/packs/BR", headers=viewer_auth_headers)).status_code == 403


@pytest.mark.asyncio
async def test_api_unknown_pack_is_404(client: AsyncClient, auth_headers: dict):
    assert (await client.get("/api/rules/packs/ZZ/removal", headers=auth_headers)).status_code == 404
    assert (await client.delete("/api/rules/packs/ZZ", headers=auth_headers)).status_code == 404
