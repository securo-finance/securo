import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.category import Category
from app.models.payee import Payee
from app.models.transaction import Transaction
from app.schemas.rule import (
    RuleAction,
    RuleCondition,
    RuleConditionGroup,
    RuleCreate,
    RuleExportItem,
    RuleExportPayload,
    RuleUpdate,
)
from app.schemas.transaction import TransactionUpdate
from app.services.rule_service import (
    DuplicateRuleError,
    RULE_PACKS,
    apply_all_rules,
    apply_rules_to_transaction,
    apply_single_rule,
    create_default_rules,
    create_rule,
    delete_rule,
    export_rules,
    get_installed_packs,
    get_rule,
    get_rules,
    import_rules,
    install_rule_pack,
    update_rule,
)
from app.services.transaction_service import update_transaction
from app.services.category_service import create_default_categories


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_rule(session: AsyncSession, test_user, test_workspace, test_categories):
    data = RuleCreate(
        name="My Rule",
        conditions_op="or",
        conditions=[RuleCondition(field="description", op="contains", value="UBER")],
        actions=[RuleAction(op="set_category", value=str(test_categories[1].id))],
        priority=10,
    )
    rule = await create_rule(session, test_workspace.id, test_user.id, data)

    assert rule.id is not None
    assert rule.name == "My Rule"
    assert rule.conditions_op == "or"
    assert len(rule.conditions) == 1
    assert len(rule.actions) == 1
    assert rule.is_active is True


@pytest.mark.asyncio
async def test_get_rules(session: AsyncSession, test_user, test_workspace, test_categories):
    for name in ["Rule A", "Rule B"]:
        await create_rule(
            session,
            test_workspace.id, test_user.id,
            RuleCreate(
                name=name,
                conditions=[RuleCondition(field="description", op="contains", value="X")],
                actions=[RuleAction(op="set_category", value=str(test_categories[0].id))],
            ),
        )

    rules = await get_rules(session, test_workspace.id)
    assert len(rules) >= 2
    names = {r.name for r in rules}
    assert "Rule A" in names
    assert "Rule B" in names


@pytest.mark.asyncio
async def test_get_rule_by_id(session: AsyncSession, test_user, test_workspace, test_categories):
    created = await create_rule(
        session,
        test_workspace.id, test_user.id,
        RuleCreate(
            name="Lookup Rule",
            conditions=[RuleCondition(field="description", op="contains", value="X")],
            actions=[RuleAction(op="set_category", value=str(test_categories[0].id))],
        ),
    )
    fetched = await get_rule(session, created.id, test_workspace.id)
    assert fetched is not None
    assert fetched.id == created.id


@pytest.mark.asyncio
async def test_get_rule_not_found(session: AsyncSession, test_user, test_workspace):
    result = await get_rule(session, uuid.uuid4(), test_workspace.id)
    assert result is None


@pytest.mark.asyncio
async def test_update_rule(session: AsyncSession, test_user, test_workspace, test_categories):
    rule = await create_rule(
        session,
        test_workspace.id, test_user.id,
        RuleCreate(
            name="Original",
            conditions=[RuleCondition(field="description", op="contains", value="OLD")],
            actions=[RuleAction(op="set_category", value=str(test_categories[0].id))],
            priority=5,
        ),
    )
    updated = await update_rule(
        session,
        rule.id,
        test_workspace.id,
        RuleUpdate(name="Updated", priority=20),
    )
    assert updated is not None
    assert updated.name == "Updated"
    assert updated.priority == 20


@pytest.mark.asyncio
async def test_update_rule_not_found(session: AsyncSession, test_user, test_workspace):
    result = await update_rule(
        session,
        uuid.uuid4(),
        test_workspace.id,
        RuleUpdate(name="Nope"),
    )
    assert result is None


@pytest.mark.asyncio
async def test_delete_rule(session: AsyncSession, test_user, test_workspace, test_categories):
    rule = await create_rule(
        session,
        test_workspace.id, test_user.id,
        RuleCreate(
            name="ToDelete",
            conditions=[RuleCondition(field="description", op="contains", value="X")],
            actions=[RuleAction(op="set_category", value=str(test_categories[0].id))],
        ),
    )
    assert await delete_rule(session, rule.id, test_workspace.id) is True
    assert await get_rule(session, rule.id, test_workspace.id) is None


@pytest.mark.asyncio
async def test_delete_rule_not_found(session: AsyncSession, test_user, test_workspace):
    assert await delete_rule(session, uuid.uuid4(), test_workspace.id) is False


# ---------------------------------------------------------------------------
# DuplicateRuleError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_duplicate_rule_raises(session: AsyncSession, test_user, test_workspace, test_categories):
    data = RuleCreate(
        name="Unique Name",
        conditions=[RuleCondition(field="description", op="contains", value="X")],
        actions=[RuleAction(op="set_category", value=str(test_categories[0].id))],
    )
    await create_rule(session, test_workspace.id, test_user.id, data)

    with pytest.raises(DuplicateRuleError):
        await create_rule(session, test_workspace.id, test_user.id, data)


@pytest.mark.asyncio
async def test_update_rule_duplicate_name_raises(session: AsyncSession, test_user, test_workspace, test_categories):
    rule_a = await create_rule(
        session,
        test_workspace.id, test_user.id,
        RuleCreate(
            name="Name A",
            conditions=[RuleCondition(field="description", op="contains", value="X")],
            actions=[RuleAction(op="set_category", value=str(test_categories[0].id))],
        ),
    )
    await create_rule(
        session,
        test_workspace.id, test_user.id,
        RuleCreate(
            name="Name B",
            conditions=[RuleCondition(field="description", op="contains", value="Y")],
            actions=[RuleAction(op="set_category", value=str(test_categories[0].id))],
        ),
    )

    with pytest.raises(DuplicateRuleError):
        await update_rule(
            session,
            rule_a.id,
            test_workspace.id,
            RuleUpdate(name="Name B"),
        )


@pytest.mark.asyncio
async def test_create_rule_rejects_unknown_action(session: AsyncSession, test_user, test_workspace):
    with pytest.raises(ValueError, match="Invalid rule action"):
        await create_rule(
            session,
            test_workspace.id,
            test_user.id,
            RuleCreate(
                name="Bad Action",
                conditions=[RuleCondition(field="description", op="contains", value="X")],
                actions=[RuleAction(op="explode", value="nope")],
            ),
        )


@pytest.mark.asyncio
async def test_create_rule_rejects_category_outside_workspace(
    session: AsyncSession, test_user, test_workspace
):
    with pytest.raises(ValueError, match="Category not found"):
        await create_rule(
            session,
            test_workspace.id,
            test_user.id,
            RuleCreate(
                name="Wrong Category",
                conditions=[RuleCondition(field="description", op="contains", value="X")],
                actions=[RuleAction(op="set_category", value=str(uuid.uuid4()))],
            ),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("pattern", ["foo|", "a*", "^$"])
async def test_create_rule_rejects_empty_matching_regex(
    session: AsyncSession, test_user, test_workspace, pattern
):
    name = f"Unsafe regex {pattern}"
    error = None
    try:
        await create_rule(
            session,
            test_workspace.id,
            test_user.id,
            RuleCreate(
                name=name,
                conditions=[
                    RuleCondition(field="description", op="regex", value=pattern)
                ],
                actions=[RuleAction(op="append_notes", value="#unsafe")],
            ),
        )
    except ValueError as exc:
        error = str(exc)

    persisted = {rule.name for rule in await get_rules(session, test_workspace.id)}
    assert (error, name in persisted) == (
        "Regular expression must not match an empty string",
        False,
    )


@pytest.mark.asyncio
async def test_create_rule_rejects_malformed_regex(
    session: AsyncSession, test_user, test_workspace
):
    name = "Malformed regex"
    error = None
    try:
        await create_rule(
            session,
            test_workspace.id,
            test_user.id,
            RuleCreate(
                name=name,
                conditions=[RuleCondition(field="description", op="regex", value="[")],
                actions=[RuleAction(op="append_notes", value="#malformed")],
            ),
        )
    except ValueError as exc:
        error = str(exc)

    persisted = {rule.name for rule in await get_rules(session, test_workspace.id)}
    assert (error, name in persisted) == ("Invalid regular expression", False)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "pattern",
    ["foo|bar", "^NETFLIX", "PIX.*RECEBIDO", r"\bFOO\b", "foo.*", ".+", "^.+$"],
)
async def test_create_rule_accepts_safe_regex(
    session: AsyncSession, test_user, test_workspace, pattern
):
    await create_rule(
        session,
        test_workspace.id,
        test_user.id,
        RuleCreate(
            name=f"Safe regex {pattern}",
            conditions=[RuleCondition(field="description", op="regex", value=pattern)],
            actions=[RuleAction(op="append_notes", value="#safe")],
        ),
    )



@pytest.mark.asyncio
async def test_create_rule_rejects_empty_matching_regex_in_condition_group(
    session: AsyncSession, test_user, test_workspace
):
    name = "Unsafe grouped regex"
    error = None
    try:
        await create_rule(
            session,
            test_workspace.id,
            test_user.id,
            RuleCreate(
                name=name,
                conditions_op="or",
                conditions=[
                    RuleCondition(
                        field="description", op="contains", value="SAFE"
                    ),
                    RuleConditionGroup(
                        op="or",
                        conditions=[
                            RuleCondition(
                                field="description", op="regex", value="foo|"
                            ),
                            RuleCondition(
                                field="description", op="contains", value="OTHER"
                            ),
                        ],
                    ),
                ],
                actions=[RuleAction(op="append_notes", value="#grouped")],
            ),
        )
    except ValueError as exc:
        error = str(exc)

    persisted = {rule.name for rule in await get_rules(session, test_workspace.id)}
    assert (error, name in persisted) == (
        "Regular expression must not match an empty string",
        False,
    )


@pytest.mark.asyncio
async def test_update_rule_rejects_payee_outside_workspace(
    session: AsyncSession, test_user, test_workspace
):
    payee = Payee(user_id=test_user.id, workspace_id=uuid.uuid4(), name="Foreign")
    session.add(payee)
    await session.commit()

    rule = await create_rule(
        session,
        test_workspace.id,
        test_user.id,
        RuleCreate(
            name="Safe",
            conditions=[RuleCondition(field="description", op="contains", value="X")],
            actions=[RuleAction(op="append_notes", value="#safe")],
        ),
    )

    with pytest.raises(ValueError, match="Payee not found"):
        await update_rule(
            session,
            rule.id,
            test_workspace.id,
            RuleUpdate(actions=[RuleAction(op="set_payee", value=str(payee.id))]),
        )


@pytest.mark.asyncio
async def test_import_rules_skips_invalid_rules(
    session: AsyncSession, test_user, test_workspace
):
    category = Category(
        user_id=test_user.id,
        workspace_id=uuid.uuid4(),
        name="Foreign",
        icon="x",
        color="#000000",
    )
    session.add(category)
    await session.commit()

    payload = RuleExportPayload(
        rules=[
            RuleExportItem(
                name="Foreign category",
                conditions=[RuleCondition(field="description", op="contains", value="X")],
                actions=[RuleAction(op="set_category", value=str(category.id))],
            ),
            RuleExportItem(
                name="Invalid condition",
                conditions=[RuleCondition(field="description", op="invalid", value="X")],
                actions=[RuleAction(op="append_notes", value="#bad")],
            ),
            RuleExportItem(
                name="Valid",
                conditions=[RuleCondition(field="description", op="contains", value="X")],
                actions=[RuleAction(op="append_notes", value="#valid")],
            ),
        ]
    )

    result = await import_rules(
        session, test_workspace.id, test_user.id, payload, overwrite=True
    )

    assert result.imported == 1
    assert result.skipped == 2



@pytest.mark.asyncio
async def test_import_rules_skips_unsafe_and_malformed_regexes(
    session: AsyncSession, test_user, test_workspace
):
    payload = RuleExportPayload(
        rules=[
            RuleExportItem(
                name="Unsafe regex import",
                conditions=[
                    RuleCondition(field="description", op="regex", value="foo|")
                ],
                actions=[RuleAction(op="append_notes", value="#unsafe")],
            ),
            RuleExportItem(
                name="Malformed regex import",
                conditions=[RuleCondition(field="description", op="regex", value="[")],
                actions=[RuleAction(op="append_notes", value="#malformed")],
            ),
            RuleExportItem(
                name="Safe regex import",
                conditions=[
                    RuleCondition(field="description", op="regex", value="foo|bar")
                ],
                actions=[RuleAction(op="append_notes", value="#safe")],
            ),
        ]
    )

    result = await import_rules(
        session, test_workspace.id, test_user.id, payload, overwrite=True
    )
    persisted = {
        rule.name: rule.conditions[0]["value"]
        for rule in await get_rules(session, test_workspace.id)
    }

    assert (
        result.imported,
        result.skipped,
        persisted,
    ) == (
        1,
        2,
        {"Safe regex import": "foo|bar"},
    )

@pytest.mark.asyncio
async def test_set_description_validation_and_export_compatibility(
    session: AsyncSession, test_user, test_workspace
):
    condition = RuleCondition(field="payee", op="contains", value="IFOOD.COM")

    with pytest.raises(ValueError, match="blank"):
        await create_rule(
            session,
            test_workspace.id,
            test_user.id,
            RuleCreate(
                name="Blank description",
                conditions=[condition],
                actions=[RuleAction(op="set_description", value="   ")],
            ),
        )

    with pytest.raises(ValueError, match="500"):
        await create_rule(
            session,
            test_workspace.id,
            test_user.id,
            RuleCreate(
                name="Long description",
                conditions=[condition],
                actions=[RuleAction(op="set_description", value="x" * 501)],
            ),
        )

    rule = await create_rule(
        session,
        test_workspace.id,
        test_user.id,
        RuleCreate(
            name="Normalize iFood",
            conditions=[condition],
            actions=[RuleAction(op="set_description", value="iFood")],
        ),
    )
    payload = await export_rules(session, test_workspace.id)
    assert payload.version == 1
    exported = next(item for item in payload.rules if item.name == rule.name)
    # The list can hold groups now, so narrow before reading a leaf's field.
    exported_condition = exported.conditions[0]
    assert isinstance(exported_condition, RuleCondition)
    assert exported_condition.field == "payee"
    assert exported.actions[0].op == "set_description"
# ---------------------------------------------------------------------------
# apply_rules_to_transaction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_rules_to_transaction(session: AsyncSession, test_user, test_workspace, test_categories):
    # Create a rule matching UBER
    await create_rule(
        session,
        test_workspace.id, test_user.id,
        RuleCreate(
            name="UBER Rule",
            conditions_op="or",
            conditions=[RuleCondition(field="description", op="starts_with", value="UBER")],
            actions=[RuleAction(op="set_category", value=str(test_categories[1].id))],
            priority=10,
        ),
    )

    account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        name="RuleAcc",
        type="checking",
        balance=Decimal("1000"),
        currency="BRL",
    )
    session.add(account)
    await session.commit()

    txn = Transaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        account_id=account.id,
        description="UBER TRIP",
        amount=Decimal("25.50"),
        date=date(2025, 3, 1),
        type="debit",
        source="manual",
        created_at=datetime.now(timezone.utc),
    )
    session.add(txn)
    await session.commit()

    await apply_rules_to_transaction(session, test_user.id, txn)

    assert txn.category_id == test_categories[1].id


@pytest.mark.asyncio
async def test_apply_rules_no_match(session: AsyncSession, test_user, test_workspace, test_categories):
    await create_rule(
        session,
        test_workspace.id, test_user.id,
        RuleCreate(
            name="IFOOD Only",
            conditions_op="or",
            conditions=[RuleCondition(field="description", op="starts_with", value="IFOOD")],
            actions=[RuleAction(op="set_category", value=str(test_categories[0].id))],
        ),
    )

    account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        name="NoMatch",
        type="checking",
        balance=Decimal("1000"),
        currency="BRL",
    )
    session.add(account)
    await session.commit()

    txn = Transaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        account_id=account.id,
        description="RANDOM MERCHANT",
        amount=Decimal("10"),
        date=date(2025, 3, 1),
        type="debit",
        source="manual",
        created_at=datetime.now(timezone.utc),
    )
    session.add(txn)
    await session.commit()

    await apply_rules_to_transaction(session, test_user.id, txn)
    assert txn.category_id is None


# ---------------------------------------------------------------------------
# apply_all_rules
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_all_rules(session: AsyncSession, test_user, test_workspace, test_categories):
    account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        name="AllRules",
        type="checking",
        balance=Decimal("5000"),
        currency="BRL",
    )
    session.add(account)
    await session.commit()

    # Create transactions
    txn1 = Transaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        account_id=account.id,
        description="UBER RIDE",
        amount=Decimal("30"),
        date=date(2025, 3, 5),
        type="debit",
        source="manual",
        created_at=datetime.now(timezone.utc),
    )
    txn2 = Transaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        account_id=account.id,
        description="IFOOD RESTAURANTE",
        amount=Decimal("45"),
        date=date(2025, 3, 6),
        type="debit",
        source="manual",
        created_at=datetime.now(timezone.utc),
    )
    session.add_all([txn1, txn2])
    await session.commit()

    # Create rules
    await create_rule(
        session,
        test_workspace.id, test_user.id,
        RuleCreate(
            name="UBER apply-all",
            conditions_op="or",
            conditions=[RuleCondition(field="description", op="starts_with", value="UBER")],
            actions=[RuleAction(op="set_category", value=str(test_categories[1].id))],
            priority=10,
        ),
    )
    await create_rule(
        session,
        test_workspace.id, test_user.id,
        RuleCreate(
            name="IFOOD apply-all",
            conditions_op="or",
            conditions=[RuleCondition(field="description", op="starts_with", value="IFOOD")],
            actions=[RuleAction(op="set_category", value=str(test_categories[0].id))],
            priority=10,
        ),
    )

    count = await apply_all_rules(session, test_workspace.id)
    assert count >= 2

    await session.refresh(txn1)
    await session.refresh(txn2)
    assert txn1.category_id == test_categories[1].id  # transport
    assert txn2.category_id == test_categories[0].id  # food


@pytest.mark.asyncio
async def test_apply_all_rules_replaces_edited_normalization_idempotently(
    session: AsyncSession, test_user, test_workspace
):
    account = Account(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Normalization",
        type="checking",
        balance=Decimal("1000"),
        currency="BRL",
    )
    session.add(account)
    await session.commit()
    transaction = Transaction(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=account.id,
        description="D01-123 AMZNPrime DE changing-token",
        amount=Decimal("19.90"),
        date=date(2026, 1, 10),
        type="debit",
        source="sync",
    )
    session.add(transaction)
    await session.commit()
    rule = await create_rule(
        session,
        test_workspace.id,
        test_user.id,
        RuleCreate(
            name="Editable normalization",
            conditions=[
                RuleCondition(
                    field="description", op="contains", value="AMZNPrime DE"
                )
            ],
            actions=[
                RuleAction(op="set_description", value="Amazon Prime"),
                RuleAction(op="append_notes", value="#subscription"),
            ],
            apply_to_existing=False,
        ),
    )

    assert await apply_all_rules(session, test_workspace.id) == 1
    await session.refresh(transaction)
    assert transaction.description == "Amazon Prime"
    assert transaction.original_description == "D01-123 AMZNPrime DE changing-token"
    assert transaction.description_is_rule_managed is True
    assert transaction.notes == "#subscription"

    await update_rule(
        session,
        rule.id,
        test_workspace.id,
        RuleUpdate(
            actions=[
                RuleAction(op="set_description", value="Prime"),
                RuleAction(op="append_notes", value="#subscription"),
            ]
        ),
    )
    assert await apply_all_rules(session, test_workspace.id) == 1
    await session.refresh(transaction)
    assert transaction.description == "Prime"
    assert transaction.original_description == "D01-123 AMZNPrime DE changing-token"
    assert transaction.description_is_rule_managed is True
    assert transaction.notes == "#subscription"
    await apply_all_rules(session, test_workspace.id)
    await session.refresh(transaction)
    assert transaction.description == "Prime"
    assert transaction.original_description == "D01-123 AMZNPrime DE changing-token"
    assert transaction.description_is_rule_managed is True
    assert transaction.notes == "#subscription"

    assert await delete_rule(
        session, rule.id, test_workspace.id
    ) is True
    assert await apply_all_rules(session, test_workspace.id) == 1
    await session.refresh(transaction)
    assert transaction.description == "D01-123 AMZNPrime DE changing-token"
    assert transaction.original_description == "D01-123 AMZNPrime DE changing-token"
    assert transaction.description_is_rule_managed is False


@pytest.mark.asyncio
async def test_apply_all_rules_preserves_manually_edited_import_description(
    session: AsyncSession, test_user, test_workspace
):
    account = Account(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Imported",
        type="checking",
        balance=Decimal("1000"),
        currency="BRL",
    )
    session.add(account)
    await session.flush()
    transaction = Transaction(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=account.id,
        description="BANK RAW DESCRIPTION",
        original_description="BANK RAW DESCRIPTION",
        amount=Decimal("12.34"),
        date=date(2026, 1, 11),
        type="debit",
        source="csv",
        payee="IFOOD.COM RESTAURANTES",
    )
    session.add(transaction)
    await session.commit()

    updated = await update_transaction(
        session,
        transaction.id,
        test_workspace.id,
        test_user.id,
        TransactionUpdate(description="Manually edited merchant"),
    )
    assert updated is not None
    assert updated.description_is_rule_managed is False

    await create_rule(
        session,
        test_workspace.id,
        test_user.id,
        RuleCreate(
            name="Stable payee normalization",
            conditions=[
                RuleCondition(field="payee", op="contains", value="IFOOD.COM")
            ],
            actions=[
                RuleAction(op="set_description", value="iFood"),
                RuleAction(op="append_notes", value="#delivery"),
            ],
            apply_to_existing=False,
        ),
    )

    assert await apply_all_rules(session, test_workspace.id) == 1
    await session.refresh(transaction)
    assert transaction.description == "Manually edited merchant"
    assert transaction.original_description == "BANK RAW DESCRIPTION"
    assert transaction.description_is_rule_managed is False
    assert transaction.notes == "#delivery"


@pytest.mark.asyncio
async def test_apply_single_rule_preserves_manually_edited_import_description(
    session: AsyncSession, test_user, test_workspace
):
    """Re-saving a normalization rule must not undo the user's own wording.

    `apply_all_rules` already protects a hand-edited description; the same must
    hold for the far more common path of editing one rule and re-applying it.
    """
    account = Account(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Imported",
        type="checking",
        balance=Decimal("1000"),
        currency="BRL",
    )
    session.add(account)
    await session.flush()
    transaction = Transaction(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=account.id,
        description="D01-123 AMZNPrime DE changing-token",
        original_description="D01-123 AMZNPrime DE changing-token",
        amount=Decimal("19.90"),
        date=date(2026, 1, 10),
        type="debit",
        source="csv",
    )
    session.add(transaction)
    await session.commit()

    rule = await create_rule(
        session,
        test_workspace.id,
        test_user.id,
        RuleCreate(
            name="Amazon Prime normalization",
            conditions=[
                RuleCondition(field="description", op="contains", value="AMZNPrime")
            ],
            actions=[
                RuleAction(op="set_description", value="Amazon Prime"),
                RuleAction(op="append_notes", value="#subscription"),
            ],
            apply_to_existing=False,
        ),
    )

    assert await apply_single_rule(session, test_workspace.id, rule) == 1
    await session.refresh(transaction)
    assert transaction.description == "Amazon Prime"
    assert transaction.description_is_rule_managed is True

    updated = await update_transaction(
        session,
        transaction.id,
        test_workspace.id,
        test_user.id,
        TransactionUpdate(description="Amazon Prime (family)"),
    )
    assert updated is not None
    assert updated.description_is_rule_managed is False

    # Clear the notes so the re-application has something left to do: the rule
    # must still match and still run its other actions, and only the typed
    # description is off limits.
    transaction.notes = None
    await session.commit()

    assert await apply_single_rule(session, test_workspace.id, rule) == 1
    await session.refresh(transaction)
    assert transaction.description == "Amazon Prime (family)"
    assert transaction.original_description == "D01-123 AMZNPrime DE changing-token"
    assert transaction.description_is_rule_managed is False
    assert transaction.notes == "#subscription"


@pytest.mark.asyncio
async def test_apply_single_rule_prefers_current_description_then_falls_back_to_original(
    session: AsyncSession, test_user, test_workspace, test_categories
):
    account = Account(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Rule chain",
        type="checking",
        balance=Decimal("1000"),
        currency="BRL",
    )
    session.add(account)
    await session.flush()
    transaction = Transaction(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=account.id,
        description="D01-123 AMZNPrime DE changing-token",
        amount=Decimal("19.90"),
        date=date(2026, 1, 10),
        type="debit",
        source="sync",
    )
    session.add(transaction)
    await session.commit()

    normalizer = await create_rule(
        session,
        test_workspace.id,
        test_user.id,
        RuleCreate(
            name="Normalize Amazon",
            conditions=[
                RuleCondition(
                    field="description", op="contains", value="AMZNPrime DE"
                )
            ],
            actions=[
                RuleAction(op="set_description", value="Amazon Prime")
            ],
            apply_to_existing=False,
            priority=10,
        ),
    )
    assert await apply_single_rule(
        session, test_workspace.id, normalizer
    ) == 1
    assert transaction.description == "Amazon Prime"
    assert (
        transaction.original_description
        == "D01-123 AMZNPrime DE changing-token"
    )

    dependent = await create_rule(
        session,
        test_workspace.id,
        test_user.id,
        RuleCreate(
            name="Categorize normalized Amazon",
            conditions=[
                RuleCondition(
                    field="description", op="equals", value="Amazon Prime"
                )
            ],
            actions=[
                RuleAction(
                    op="set_category", value=str(test_categories[0].id)
                )
            ],
            apply_to_existing=False,
            priority=20,
        ),
    )
    assert await apply_single_rule(
        session, test_workspace.id, dependent
    ) == 1
    assert transaction.category_id == test_categories[0].id
    assert await apply_single_rule(
        session, test_workspace.id, dependent
    ) == 0
    negative = await create_rule(
        session,
        test_workspace.id,
        test_user.id,
        RuleCreate(
            name="Current-state negative match",
            conditions=[
                RuleCondition(
                    field="description",
                    op="not_contains",
                    value="AMZNPrime DE",
                )
            ],
            actions=[RuleAction(op="append_notes", value="#normalized")],
            apply_to_existing=False,
            priority=30,
        ),
    )
    assert await apply_single_rule(
        session, test_workspace.id, negative
    ) == 1
    assert transaction.notes == "#normalized"
    assert await apply_single_rule(
        session, test_workspace.id, negative
    ) == 0

    normalizer = await update_rule(
        session,
        normalizer.id,
        test_workspace.id,
        RuleUpdate(
            actions=[
                RuleAction(op="set_description", value="Prime"),
                RuleAction(
                    op="set_category", value=str(test_categories[1].id)
                ),
            ]
        ),
    )
    assert normalizer is not None
    assert await apply_single_rule(
        session, test_workspace.id, normalizer
    ) == 1
    assert transaction.description == "Prime"
    assert (
        transaction.original_description
        == "D01-123 AMZNPrime DE changing-token"
    )
    assert transaction.category_id == test_categories[0].id
    assert await apply_single_rule(
        session, test_workspace.id, normalizer
    ) == 0
    assert transaction.category_id == test_categories[0].id


@pytest.mark.asyncio
async def test_apply_all_rules_preserves_manual_categories(
    session: AsyncSession, test_user, test_workspace, test_categories
):
    """Manually categorized transactions that don't match any rule must keep their category."""
    account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        name="ManualCat",
        type="checking",
        balance=Decimal("1000"),
        currency="BRL",
    )
    session.add(account)
    await session.commit()

    manual_cat = test_categories[0]
    rule_cat = test_categories[1]

    # txn_manual: manually categorized, does NOT match any rule
    txn_manual = Transaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        account_id=account.id,
        description="PADARIA DO JOAO",
        amount=Decimal("15"),
        date=date(2025, 4, 1),
        type="debit",
        source="manual",
        category_id=manual_cat.id,
        notes="my manual note",
        created_at=datetime.now(timezone.utc),
    )
    # txn_rule: uncategorized, matches a rule
    txn_rule = Transaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        account_id=account.id,
        description="UBER TRIP",
        amount=Decimal("25"),
        date=date(2025, 4, 2),
        type="debit",
        source="manual",
        created_at=datetime.now(timezone.utc),
    )
    # txn_uncategorized: no category, no rule match — should stay uncategorized
    txn_uncategorized = Transaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        account_id=account.id,
        description="RANDOM STORE",
        amount=Decimal("50"),
        date=date(2025, 4, 3),
        type="debit",
        source="manual",
        created_at=datetime.now(timezone.utc),
    )
    session.add_all([txn_manual, txn_rule, txn_uncategorized])
    await session.commit()

    # Only one rule: matches UBER
    await create_rule(
        session,
        test_workspace.id, test_user.id,
        RuleCreate(
            name="UBER preserve-test",
            conditions_op="or",
            conditions=[RuleCondition(field="description", op="starts_with", value="UBER")],
            actions=[RuleAction(op="set_category", value=str(rule_cat.id))],
            priority=10,
        ),
    )

    count = await apply_all_rules(session, test_workspace.id)

    await session.refresh(txn_manual)
    await session.refresh(txn_rule)
    await session.refresh(txn_uncategorized)

    # Manual category and notes must be preserved
    assert txn_manual.category_id == manual_cat.id
    assert txn_manual.notes == "my manual note"

    # Rule-matched transaction gets categorized
    assert txn_rule.category_id == rule_cat.id

    # Unmatched, uncategorized transaction stays uncategorized
    assert txn_uncategorized.category_id is None

    # Only 1 transaction was affected by rules
    assert count == 1


# ---------------------------------------------------------------------------
# create_default_rules / install_rule_pack / get_installed_packs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_default_rules(session: AsyncSession, test_user, test_workspace):
    # Need default categories first so rule templates can resolve
    await create_default_categories(session, test_user.id, lang="pt-BR")

    rules = await create_default_rules(session, test_user.id, lang="pt-BR")
    assert len(rules) >= 3  # at least Streaming, Uber, Amazon, etc.

    names = {r.name for r in rules}
    assert "Uber" in names
    assert "Amazon" in names


@pytest.mark.asyncio
async def test_install_rule_pack_br(session: AsyncSession, test_user, test_workspace):
    await create_default_categories(session, test_user.id, lang="pt-BR")

    result = await install_rule_pack(session, test_workspace.id, test_user.id, "BR", lang="pt-BR")
    assert len(result.rules) > 0
    assert result.unresolved == 0

    names = {r.name for r in result.rules}
    assert "iFood / Rappi" in names


@pytest.mark.asyncio
async def test_install_rule_pack_skips_duplicates(session: AsyncSession, test_user, test_workspace):
    await create_default_categories(session, test_user.id, lang="pt-BR")

    first = await install_rule_pack(session, test_workspace.id, test_user.id, "BR", lang="pt-BR")
    second = await install_rule_pack(session, test_workspace.id, test_user.id, "BR", lang="pt-BR")

    assert len(first.rules) > 0
    # All rules already installed — distinct from "couldn't install" because
    # nothing was unresolvable.
    assert len(second.rules) == 0
    assert second.unresolved == 0


@pytest.mark.asyncio
async def test_install_rule_pack_works_across_languages(session: AsyncSession, test_user, test_workspace):
    # Regression for #154: user registers in English, switches UI to pt-BR,
    # then installs the BR pack. Categories ("Transport") and template
    # language ("pt-BR" → "Transporte") would mismatch — install should
    # still resolve the category by internal key.
    await create_default_categories(session, test_user.id, lang="en")

    result = await install_rule_pack(session, test_workspace.id, test_user.id, "BR", lang="pt-BR")

    # Every BR rule's set_category action should have resolved to a real
    # English category UUID, so the pack installs in full.
    assert len(result.rules) == len(RULE_PACKS["BR"]["rules"])
    assert result.unresolved == 0


@pytest.mark.asyncio
async def test_install_rule_pack_reports_unresolved_when_categories_missing(
    session: AsyncSession, test_user, test_workspace
):
    # User in a degenerate state with no default categories — every pack
    # rule's set_category target is missing, so the install can't actually
    # write any rules. The result must surface this so the frontend can
    # tell the user "missing categories" instead of the misleading
    # "pack already installed" toast.
    result = await install_rule_pack(session, test_workspace.id, test_user.id, "BR", lang="pt-BR")

    assert len(result.rules) == 0
    assert result.unresolved == len(RULE_PACKS["BR"]["rules"])


@pytest.mark.asyncio
async def test_install_rule_pack_creates_missing_categories_when_opted_in(
    session: AsyncSession, test_user, test_workspace
):
    # Same degenerate user, but they tick the "create missing categories"
    # checkbox in the modal. Pack must seed the categories it needs and
    # then install the full rule set.
    result = await install_rule_pack(
        session,
        test_workspace.id,
        test_user.id,
        "BR",
        lang="pt-BR",
        create_missing_categories=True,
    )

    assert len(result.rules) == len(RULE_PACKS["BR"]["rules"])
    assert result.unresolved == 0
    assert result.categories_created > 0


@pytest.mark.asyncio
async def test_install_rule_pack_unknown_returns_empty(session: AsyncSession, test_user, test_workspace):
    result = await install_rule_pack(session, test_workspace.id, test_user.id, "ZZ")
    assert result.rules == []
    assert result.unresolved == 0


@pytest.mark.asyncio
async def test_get_installed_packs(session: AsyncSession, test_user, test_workspace):
    await create_default_categories(session, test_user.id, lang="pt-BR")

    packs_before = await get_installed_packs(session, test_user.id)
    assert packs_before["BR"] is False

    await install_rule_pack(session, test_workspace.id, test_user.id, "BR", lang="pt-BR")

    packs_after = await get_installed_packs(session, test_user.id)
    assert packs_after["BR"] is True
