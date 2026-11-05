"""PR-2 §8 rule-workflow tests (`test_rules_review.py`).

Named cases: `test_rule_condition_review_status_validates`,
`test_rule_condition_review_status_matches`,
`test_rule_action_set_reviewed_transitions_and_stamps`,
`test_set_reviewed_skips_already_reviewed`,
`test_rule_preview_includes_review_status`,
`test_export_import_roundtrip_set_reviewed`.

The pair of stamping tests is also gate §15-A2's requested proof: the
driver keys the stamp off the PRE-action status, never a post-mutation
check alone, so a genuine transition gains both stamps and an
already-reviewed row keeps its old stamp.
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.transaction import Transaction
from app.schemas.rule import RuleAction, RuleCondition, RuleCreate
from app.services.rule_service import (
    apply_rules_to_transaction,
    create_rule,
    delete_rule,
    export_rules,
    get_rules,
    import_rules,
    preview_rule,
    preview_rules_for_transaction,
)


async def _make_tx(
    session: AsyncSession,
    test_user,
    test_account,
    test_workspace,
    *,
    description: str,
    review_status: str = "needs_review",
) -> Transaction:
    tx = Transaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=test_account.id,
        description=description,
        amount=Decimal("15.00"),
        date=date.today(),
        type="debit",
        source="import",
        review_status=review_status,
        created_at=datetime.now(timezone.utc),
    )
    session.add(tx)
    await session.commit()
    await session.refresh(tx)
    return tx


@pytest.mark.asyncio
async def test_rule_condition_review_status_validates(
    session, test_user, test_workspace
):
    """Closed set on the condition side (§6): equals/not_equals × the two
    state values pass; any other op or value is rejected at create."""
    for op, value in (("equals", "needs_review"), ("equals", "reviewed"),
                      ("not_equals", "needs_review"), ("not_equals", "reviewed")):
        rule = await create_rule(
            session, test_workspace.id, test_user.id,
            RuleCreate(
                name=f"valid {op} {value}",
                conditions=[RuleCondition(field="review_status", op=op, value=value)],
                actions=[RuleAction(op="set_reviewed", value=None)],
            ),
        )
        assert rule is not None

    with pytest.raises(ValueError, match="Invalid rule condition"):
        await create_rule(
            session, test_workspace.id, test_user.id,
            RuleCreate(
                name="bad op",
                conditions=[RuleCondition(field="review_status", op="contains", value="needs")],
                actions=[RuleAction(op="set_reviewed", value=None)],
            ),
        )

    with pytest.raises(ValueError, match="Invalid rule condition"):
        await create_rule(
            session, test_workspace.id, test_user.id,
            RuleCreate(
                name="bad value",
                conditions=[RuleCondition(field="review_status", op="equals", value="pending_review")],
                actions=[RuleAction(op="set_reviewed", value=None)],
            ),
        )


@pytest.mark.asyncio
async def test_rule_condition_review_status_matches(
    session, test_user, test_account, test_workspace, test_categories
):
    """A `review_status` condition actually selects rows by review state:
    the same rule fires on a needs-review row and skips a reviewed one."""
    cat = test_categories[0]
    await create_rule(
        session, test_workspace.id, test_user.id,
        RuleCreate(
            name="only needs review",
            conditions_op="and",
            conditions=[RuleCondition(field="review_status", op="equals", value="needs_review")],
            actions=[RuleAction(op="set_category", value=str(cat.id))],
        ),
    )

    needs = await _make_tx(session, test_user, test_account, test_workspace,
                           description="Needs category", review_status="needs_review")
    done = await _make_tx(session, test_user, test_account, test_workspace,
                          description="Already done", review_status="reviewed")

    await apply_rules_to_transaction(session, test_user.id, needs)
    await apply_rules_to_transaction(session, test_user.id, done)
    await session.commit()

    await session.refresh(needs)
    await session.refresh(done)
    assert needs.category_id == cat.id, "condition ignored review state"
    assert done.category_id is None, "condition matched a reviewed row it should skip"


@pytest.mark.asyncio
async def test_rule_action_set_reviewed_transitions_and_stamps(
    session, test_user, test_account, test_workspace
):
    """Gate §15-A2, transition direction: a needs-review row matching a
    `set_reviewed` rule gains a timestamp and executing-rule identity at the
    commit boundary, not a human reviewer (v1.2 supersedes assumption A4)."""
    rule = await create_rule(
        session, test_workspace.id, test_user.id,
        RuleCreate(
            name="stamp REVIEWME rows",
            conditions=[RuleCondition(field="description", op="contains", value="REVIEWME")],
            actions=[RuleAction(op="set_reviewed", value=None)],
        ),
    )
    assert rule.user_id == test_user.id

    tx = await _make_tx(session, test_user, test_account, test_workspace,
                        description="please REVIEWME now")

    await apply_rules_to_transaction(session, test_user.id, tx)
    # Commit boundary: the driver stamped in place; persistence is here.
    await session.commit()
    await session.refresh(tx)

    assert tx.review_status == "reviewed"
    assert tx.reviewed_at is not None, "A2: transition without reviewed_at"
    assert tx.reviewed_by_id is None
    assert tx.review_attribution_kind == "rule"
    assert tx.review_rule_id == rule.id

    # Re-running the rule is a stamp no-op (reviewed_at stays meaningful).
    first_stamp = tx.reviewed_at
    await apply_rules_to_transaction(session, test_user.id, tx)
    await session.commit()
    await session.refresh(tx)
    assert tx.reviewed_at == first_stamp, "re-run restamped reviewed_at"


@pytest.mark.asyncio
async def test_set_reviewed_skips_already_reviewed(
    session, test_user, test_account, test_workspace
):
    """Gate §15-A2, no-op direction: an already-reviewed row keeps its
    ORIGINAL stamp through a `set_reviewed` rule — the driver must not
    re-stamp what the engine did not transition."""
    # Naive on purpose: `reviewed_at` round-trips through the DB as a
    # timezone-less timestamp, so compare wall time like the DB stores it.
    original_at = datetime(2026, 3, 1, 12, 0, 0)
    tx = await _make_tx(session, test_user, test_account, test_workspace,
                        description="REVIEWME but already done",
                        review_status="reviewed")
    tx.reviewed_at = original_at
    tx.reviewed_by_id = test_user.id
    await session.commit()

    await create_rule(
        session, test_workspace.id, test_user.id,
        RuleCreate(
            name="stamp REVIEWME rows v2",
            conditions=[RuleCondition(field="description", op="contains", value="REVIEWME")],
            actions=[RuleAction(op="set_reviewed", value=None)],
        ),
    )

    await apply_rules_to_transaction(session, test_user.id, tx)
    await session.commit()
    await session.refresh(tx)

    assert tx.review_status == "reviewed"
    assert tx.reviewed_at == original_at, "stamp changed on a non-transition"
    assert tx.reviewed_by_id == test_user.id
    assert (tx.reviewed_at is None) == (tx.reviewed_by_id is None)


@pytest.mark.asyncio
async def test_rule_preview_includes_review_status(
    session, test_user, test_account, test_workspace, test_categories
):
    """The preview copy carries `review_status` (§6: `_PREVIEW_COLUMNS`
    + `_rule_preview`): conditions can match on it, the engine gate can
    see it on the draft, and `preview_rule` results reflect it."""
    cat = test_categories[0]
    cond_dicts = [{"field": "review_status", "op": "equals", "value": "needs_review"}]

    needs = await _make_tx(session, test_user, test_account, test_workspace,
                           description="preview needs", review_status="needs_review")
    done = await _make_tx(session, test_user, test_account, test_workspace,
                          description="preview done", review_status="reviewed")

    # (a) condition side: draft picks up the field, so the category rule
    # fires only for the needs-review row.
    await create_rule(
        session, test_workspace.id, test_user.id,
        RuleCreate(
            name="preview category by review state",
            conditions_op="and",
            conditions=[RuleCondition(field="review_status", op="equals", value="needs_review")],
            actions=[RuleAction(op="set_category", value=str(cat.id))],
        ),
    )
    preview_needs = await preview_rules_for_transaction(session, test_user.id, needs)
    preview_done = await preview_rules_for_transaction(session, test_user.id, done)
    assert preview_needs.category_id == cat.id, "preview draft lost review_status"
    assert preview_done.category_id is None
    # The preview is detached: the source rows are untouched.
    await session.refresh(needs)
    await session.refresh(done)
    assert needs.category_id is None and needs.review_status == "needs_review"
    assert done.review_status == "reviewed"

    # (b) engine side: a `set_reviewed` draft flips the status field the
    # engine gate depends on — without it the gate would read a copied
    # `None` and skip the transition.
    await create_rule(
        session, test_workspace.id, test_user.id,
        RuleCreate(
            name="preview mark reviewed",
            conditions=[RuleCondition(field="description", op="contains", value="preview needs")],
            actions=[RuleAction(op="set_reviewed", value=None)],
        ),
    )
    preview2 = await preview_rules_for_transaction(session, test_user.id, needs)
    assert preview2.review_status == "reviewed", "set_reviewed preview did not flip draft"
    await session.refresh(needs)
    assert needs.review_status == "needs_review", "preview leaked persistence"

    # (c) rule-editor results: `preview_rule` counts matches for a rule
    # conditioned on review status, and the sample includes our row.
    resp = await preview_rule(
        session, test_workspace.id, "and", cond_dicts,
        [{"op": "set_reviewed", "value": None}],
    )
    assert resp.matched >= 1, "preview_rule could not see review_status"
    sample_ids = {item.id for item in resp.sample}
    assert needs.id in sample_ids
    assert resp.will_apply is True


@pytest.mark.asyncio
async def test_export_import_roundtrip_set_reviewed(
    session, test_user, test_workspace
):
    """The value-less `set_reviewed` action survives export → import
    (validation runs only inside each direction; neither side drops it)."""
    created = await create_rule(
        session, test_workspace.id, test_user.id,
        RuleCreate(
            name="roundtrip set_reviewed",
            conditions=[RuleCondition(field="review_status", op="equals", value="needs_review")],
            actions=[RuleAction(op="set_reviewed", value=None)],
            priority=7,
        ),
    )

    payload = await export_rules(session, test_workspace.id)
    item = next(i for i in payload.rules if i.name == "roundtrip set_reviewed")

    def _op(a):
        return a.op if hasattr(a, "op") else a.get("op")

    assert any(_op(a) == "set_reviewed" for a in item.actions)

    await delete_rule(session, created.id, test_workspace.id)
    assert not [r for r in await get_rules(session, test_workspace.id)
                if r.name == "roundtrip set_reviewed"]

    result = await import_rules(session, test_workspace.id, test_user.id, payload)
    rules = [r for r in await get_rules(session, test_workspace.id)
             if r.name == "roundtrip set_reviewed"]
    assert len(rules) == 1, f"import dropped or skipped the rule: {result!r}"
    assert rules[0].priority == 7
    assert any(_op(a) == "set_reviewed" for a in rules[0].actions)
