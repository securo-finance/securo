"""Rule packs as data files, the origin they leave on rules, and removing them."""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.category import Category
from app.models.rule import Rule
from app.models.transaction import Transaction
from app.services.category_service import create_default_categories
from app.services.rule_packs import (
    PACKS_DIR,
    built_in_packs,
    load_pack,
    match_installed_rule,
    rule_content_hash,
)
from app.services.rule_service import (
    _validate_rule_definition,
    get_installed_packs,
    install_rule_pack,
    plan_rule_pack_removal,
    remove_rule_pack,
)


async def _rules(session: AsyncSession, workspace_id: uuid.UUID) -> list[Rule]:
    query = select(Rule).where(Rule.workspace_id == workspace_id).execution_options(populate_existing=True)
    return list((await session.execute(query)).scalars().all())


async def _categories(session: AsyncSession, workspace_id: uuid.UUID) -> dict[str, str]:
    rows = (await session.execute(select(Category).where(Category.workspace_id == workspace_id))).scalars().all()
    return {str(c.id): c.name for c in rows}


# --- The data files -------------------------------------------------------------


def test_every_built_in_pack_loads():
    packs = built_in_packs()
    assert list(packs) == ["BR", "US", "EU", "GB"]
    assert {p.source for p in packs.values()} == {
        "securo/rules-br", "securo/rules-us", "securo/rules-eu", "securo/rules-gb",
    }
    assert packs["BR"].flag == "\U0001F1E7\U0001F1F7"
    assert packs["EU"].flag == "\U0001F1EA\U0001F1FA"
    assert len(list(PACKS_DIR.glob("*.json"))) == 4


def test_a_pack_that_breaks_the_contract_is_rejected(tmp_path):
    good = (PACKS_DIR / "br.json").read_text(encoding="utf-8")
    for broken in (
        good.replace('"data_leaves_instance": false', '"data_leaves_instance": true'),
        good.replace('"key": "99-ride-hailing"', '"key": "Not A Key"'),
        good.replace('"default_locale": "pt-BR"', '"default_locale": "fr"'),
        good.replace('"manifest_version": 1', '"manifest_version": 2'),
    ):
        assert broken != good
        path = tmp_path / "pack.json"
        path.write_text(broken, encoding="utf-8")
        with pytest.raises(ValueError):
            load_pack(path)


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["BR", "US", "EU", "GB"])
async def test_every_pack_installs_whole_and_passes_the_rule_validator(
    session: AsyncSession, test_user, test_workspace, code
):
    result = await install_rule_pack(
        session, test_workspace.id, test_user.id, code, lang="en", create_missing_categories=True
    )
    assert result.unresolved == 0
    assert len(result.rules) == len(built_in_packs()[code].rules)
    for rule in result.rules:
        await _validate_rule_definition(session, test_workspace.id, rule.conditions, rule.actions)


# --- Installing records the origin ------------------------------------------------


@pytest.mark.asyncio
async def test_installed_rules_remember_their_pack(session: AsyncSession, test_user, test_workspace):
    await create_default_categories(session, test_user.id, lang="pt-BR")
    result = await install_rule_pack(session, test_workspace.id, test_user.id, "BR")

    pack = built_in_packs()["BR"]
    assert {r.source for r in result.rules} == {pack.source}
    assert {r.source_key for r in result.rules} == {r["key"] for r in pack.rules}
    for rule in result.rules:
        assert rule.source_hash == rule_content_hash(
            rule.name, rule.conditions_op, rule.conditions, rule.actions, rule.priority
        )


@pytest.mark.asyncio
async def test_a_renamed_pack_rule_is_not_installed_again(session: AsyncSession, test_user, test_workspace):
    await create_default_categories(session, test_user.id, lang="pt-BR")
    first = await install_rule_pack(session, test_workspace.id, test_user.id, "BR")
    first.rules[0].name = "Meu nome"
    await session.commit()

    second = await install_rule_pack(session, test_workspace.id, test_user.id, "BR")
    assert second.rules == []
    assert (await get_installed_packs(session, test_workspace.id, test_user.id))["BR"] is True


# --- Removing a pack ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_removing_a_pack_keeps_what_the_user_edited(session: AsyncSession, test_user, test_workspace):
    ws_id, user_id = test_workspace.id, test_user.id
    await create_default_categories(session, test_user.id, lang="pt-BR")
    installed = (await install_rule_pack(session, test_workspace.id, test_user.id, "BR")).rules
    edited = installed[0]
    edited.priority = edited.priority + 7
    disabled = installed[1]
    disabled.is_active = False
    mine = Rule(user_id=test_user.id, workspace_id=test_workspace.id, name="Mine", conditions_op="and",
                conditions=[{"field": "description", "op": "contains", "value": "X"}],
                actions=[{"op": "append_notes", "value": "x"}], priority=1)
    session.add(mine)
    await session.flush()
    edited_id, disabled_id, mine_id, total = edited.id, disabled.id, mine.id, len(installed)
    await session.commit()

    plan = await plan_rule_pack_removal(session, test_workspace.id, "BR")
    assert plan is not None
    assert [r.id for r in plan.kept] == [edited_id]
    assert len(plan.removed) == total - 1
    assert disabled_id in {r.id for r in plan.removed}, "switching a rule off doesn't make it the user's"

    await remove_rule_pack(session, ws_id, "BR")
    left = await _rules(session, ws_id)
    assert {r.id for r in left} == {edited_id, mine_id}
    kept = next(r for r in left if r.id == edited_id)
    assert (kept.source, kept.source_key, kept.source_hash) == (None, None, None)
    assert (await get_installed_packs(session, ws_id, user_id))["BR"] is False


@pytest.mark.asyncio
async def test_removing_a_pack_leaves_categorized_transactions_alone(
    session: AsyncSession, test_user, test_workspace
):
    await create_default_categories(session, test_user.id, lang="pt-BR")
    rules = (await install_rule_pack(session, test_workspace.id, test_user.id, "BR")).rules
    category_id = uuid.UUID(next(a["value"] for r in rules for a in r.actions if a["op"] == "set_category"))
    account = Account(user_id=test_user.id, workspace_id=test_workspace.id, name="Conta", type="checking",
                      balance=Decimal("0"), currency="BRL")
    session.add(account)
    await session.flush()
    txn = Transaction(user_id=test_user.id, workspace_id=test_workspace.id, account_id=account.id,
                      description="IFOOD", amount=Decimal("30"), date=date(2026, 11, 1), type="debit",
                      currency="BRL", category_id=category_id, source="manual")
    session.add(txn)
    await session.flush()
    ws_id, txn_id = test_workspace.id, txn.id
    await session.commit()

    await remove_rule_pack(session, ws_id, "BR")
    reloaded = await session.get(Transaction, txn_id, populate_existing=True)
    assert reloaded is not None and reloaded.category_id == category_id


@pytest.mark.asyncio
async def test_removal_only_touches_its_own_workspace(session: AsyncSession, test_user, test_workspace):
    from app.models.workspace import Workspace, WorkspaceMember

    ws_id, user_id, other_id = test_workspace.id, test_user.id, uuid.uuid4()
    session.add(Workspace(id=other_id, name="Other", kind="personal", default_currency="BRL", locale="pt-BR"))
    await session.flush()
    session.add(WorkspaceMember(id=uuid.uuid4(), workspace_id=other_id, user_id=user_id, role="owner"))
    await session.commit()
    await install_rule_pack(session, ws_id, user_id, "BR", create_missing_categories=True)
    await install_rule_pack(session, other_id, user_id, "BR", create_missing_categories=True)

    await remove_rule_pack(session, ws_id, "BR")
    assert await _rules(session, ws_id) == []
    assert len(await _rules(session, other_id)) == len(built_in_packs()["BR"].rules)


@pytest.mark.asyncio
async def test_unknown_pack(session: AsyncSession, test_workspace):
    assert await plan_rule_pack_removal(session, test_workspace.id, "ZZ") is None
    assert await remove_rule_pack(session, test_workspace.id, "ZZ") is None


# --- Rules installed before packs recorded their origin ------------------------------------


@pytest.mark.asyncio
async def test_untouched_legacy_rules_are_recognized_and_edited_ones_are_not(
    session: AsyncSession, test_user, test_workspace
):
    """What migration 107 relies on: an exact copy of a pack rule maps back to
    it, anything edited does not."""
    await create_default_categories(session, test_user.id, lang="en")
    rules = (await install_rule_pack(session, test_workspace.id, test_user.id, "BR")).rules
    for rule in rules:  # as if installed before this change
        rule.source = rule.source_key = rule.source_hash = None
    await session.commit()
    names = await _categories(session, test_workspace.id)

    def match(rule: Rule, **override):
        fields = {"name": rule.name, "conditions_op": rule.conditions_op, "conditions": rule.conditions,
                  "actions": rule.actions, "priority": rule.priority, **override}
        return match_installed_rule(category_name=names, **fields)

    pack = built_in_packs()["BR"]
    for rule in rules:
        assert match(rule) == (pack.source, next(r["key"] for r in pack.rules if r["name"] == rule.name))

    sample = rules[0]
    assert match(sample, priority=sample.priority + 1) is None
    assert match(sample, name=sample.name + " (meu)") is None
    assert match(sample, conditions=[{"field": "description", "op": "contains", "value": "OUTRA"}]) is None
    other_category = next(cid for cid in names if cid not in {a["value"] for a in sample.actions})
    assert match(sample, actions=[{"op": "set_category", "value": other_category}]) is None

    # And the status still says installed, by name, as it always did.
    assert (await get_installed_packs(session, test_workspace.id, test_user.id))["BR"] is True


def test_a_rule_two_packs_could_have_produced_is_not_guessed():
    """US and GB share the name "Rent / Mortgage" but not the conditions, so
    the conditions decide; the matcher never picks one at random."""
    us = next(r for r in built_in_packs()["US"].rules if r["name"] == "Rent / Mortgage")
    housing = {"cat-1": "Housing"}
    found = match_installed_rule(
        us["name"], us["conditions_op"], us["conditions"],
        [{"op": "set_category", "value": "cat-1"}], us["priority"], housing,
    )
    assert found == ("securo/rules-us", us["key"])


# --- API ---------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_previews_and_removes_a_pack(client: AsyncClient, auth_headers: dict):
    resp = await client.post("/api/rules/packs/BR/install", headers=auth_headers,
                             params={"create_missing_categories": True})
    assert resp.status_code == 200, resp.text
    total = len(built_in_packs()["BR"].rules)

    packs = {p["code"]: p for p in (await client.get("/api/rules/packs", headers=auth_headers)).json()}
    assert packs["BR"]["installed"] is True
    assert packs["BR"]["rules_from_pack"] == total
    assert packs["US"]["rules_from_pack"] == 0
    assert packs["BR"]["names"]["pt-BR"] == "Brasil"

    rules = (await client.get("/api/rules", headers=auth_headers)).json()
    edited = next(r for r in rules if r["name"] == "iFood / Rappi")
    resp = await client.patch(f"/api/rules/{edited['id']}", headers=auth_headers, json={"priority": 99})
    assert resp.status_code == 200, resp.text

    preview = (await client.get("/api/rules/packs/BR/removal", headers=auth_headers)).json()
    assert [r["name"] for r in preview["keep"]] == ["iFood / Rappi"]
    assert len(preview["remove"]) == total - 1

    resp = await client.delete("/api/rules/packs/BR", headers=auth_headers)
    assert resp.status_code == 200
    assert len(resp.json()["remove"]) == total - 1
    names = [r["name"] for r in (await client.get("/api/rules", headers=auth_headers)).json()]
    assert "iFood / Rappi" in names
    packs = {p["code"]: p for p in (await client.get("/api/rules/packs", headers=auth_headers)).json()}
    assert packs["BR"]["installed"] is False
    assert packs["BR"]["rules_from_pack"] == 0


@pytest.mark.asyncio
async def test_api_viewer_cannot_remove_a_pack(client: AsyncClient, auth_headers: dict, viewer_auth_headers: dict):
    await client.post("/api/rules/packs/BR/install", headers=auth_headers, params={"create_missing_categories": True})
    assert (await client.get("/api/rules/packs/BR/removal", headers=viewer_auth_headers)).status_code == 200
    assert (await client.delete("/api/rules/packs/BR", headers=viewer_auth_headers)).status_code == 403


@pytest.mark.asyncio
async def test_api_unknown_pack_is_404(client: AsyncClient, auth_headers: dict):
    assert (await client.get("/api/rules/packs/ZZ/removal", headers=auth_headers)).status_code == 404
    assert (await client.delete("/api/rules/packs/ZZ", headers=auth_headers)).status_code == 404
