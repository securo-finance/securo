"""MCP tools for a transaction's notes and tags, and for category groups.

Exercises the handlers directly with seeded data (same pattern as
test_mcp_transfer_tools.py): preview without a write, apply only on the
external transport, workspace scoping, and the notes tool leaving every
other field of the transaction alone.
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

# Importing this package registers all tools into mcp_server.registry.REGISTRY.
import mcp_server.tools  # noqa: F401
from app.models.account import Account
from app.models.category import Category
from app.models.category_group import CategoryGroup
from app.models.transaction import Transaction
from app.models.workspace import Workspace, WorkspaceMember
from app.services import transaction_service
from mcp_server.auth import CallContext
from mcp_server.registry import REGISTRY


@pytest_asyncio.fixture
async def ctx(test_user) -> CallContext:
    """Internal runtime context (Securo's own agent): can never apply."""
    return CallContext(user_id=test_user.id, conversation_id=uuid.uuid4())


@pytest_asyncio.fixture
async def ext_ctx(test_user) -> CallContext:
    """External MCP token context (Claude Desktop, n8n): may apply."""
    return CallContext(user_id=test_user.id, conversation_id=uuid.uuid4(), external=True)


@pytest_asyncio.fixture
async def other_workspace(session: AsyncSession, test_user) -> Workspace:
    """A second workspace for the same user (newer, so it is NOT the default)."""
    ws = Workspace(
        id=uuid.uuid4(),
        name="Outro Espaco",
        kind="personal",
        created_by_user_id=test_user.id,
        default_currency="BRL",
        locale="pt-BR",
    )
    session.add(ws)
    await session.flush()
    session.add(
        WorkspaceMember(
            id=uuid.uuid4(),
            workspace_id=ws.id,
            user_id=test_user.id,
            role="owner",
        )
    )
    await session.commit()
    await session.refresh(ws)
    return ws


async def _mk_tx(
    session: AsyncSession,
    *,
    user,
    account: Account,
    ws,
    description: str = "ALMOCO",
    notes: str | None = None,
    tx_type: str = "debit",
) -> Transaction:
    tx = Transaction(
        id=uuid.uuid4(),
        user_id=user.id,
        workspace_id=getattr(ws, "id", ws),
        account_id=account.id,
        description=description,
        amount=Decimal("42.50"),
        date=date(2026, 10, 1),
        type=tx_type,
        source="manual",
        currency="BRL",
        notes=notes,
        created_at=datetime.now(timezone.utc),
    )
    session.add(tx)
    await session.commit()
    await session.refresh(tx)
    return tx


async def _fetch(session: AsyncSession, tx_id: uuid.UUID) -> Transaction:
    session.expire_all()
    result = await session.execute(select(Transaction).where(Transaction.id == tx_id))
    return result.scalar_one()


def _call(name: str):
    return REGISTRY[name].handler


# --- Registry shape ---------------------------------------------------------


def test_registry_contains_notes_and_group_tools():
    for name in ("propose_update_transaction_notes", "propose_create_category_group"):
        assert name in REGISTRY, f"missing tool: {name}"
        assert REGISTRY[name].is_proposal is True
        assert "apply" in REGISTRY[name].parameters["properties"]


def test_notes_tool_schema_cannot_reach_other_fields():
    """The tool is scoped to notes: no amount, date, account or category."""
    props = set(REGISTRY["propose_update_transaction_notes"].parameters["properties"])
    assert props == {"transaction_id", "notes", "add_tags", "remove_tags", "apply"}


# --- propose_update_transaction_notes ---------------------------------------


async def test_notes_preview_adds_tags_without_writing(
    session: AsyncSession, ctx: CallContext, test_user, test_account
):
    tx = await _mk_tx(session, user=test_user, account=test_account,
                      ws=test_account.workspace_id, notes="com a Ana")

    result = await _call("propose_update_transaction_notes")(
        session=session, ctx=ctx, transaction_id=str(tx.id), add_tags=["trip", "#trip"]
    )

    assert result["kind"] == "update_transaction_notes"
    assert result["target"]["id"] == str(tx.id)
    assert result["target"]["notes"] == "com a Ana"
    # Normalised to #trip and not appended twice.
    assert result["changes"] == {"notes": "com a Ana #trip"}
    assert result["transfer_pair_id"] is None
    assert result["apply_endpoint"] == f"PATCH /api/transactions/{tx.id}"
    assert "applied" not in result
    assert (await _fetch(session, tx.id)).notes == "com a Ana"


async def test_notes_replace_then_remove_then_add(
    session: AsyncSession, ctx: CallContext, test_user, test_account
):
    tx = await _mk_tx(session, user=test_user, account=test_account,
                      ws=test_account.workspace_id, notes="old text #work")

    result = await _call("propose_update_transaction_notes")(
        session=session, ctx=ctx, transaction_id=str(tx.id),
        notes="dinner #work #family", remove_tags=["#work"], add_tags=["trip"],
    )

    assert result["changes"]["notes"] == "dinner #family #trip"


async def test_notes_empty_string_clears_the_notes(
    session: AsyncSession, ctx: CallContext, test_user, test_account
):
    tx = await _mk_tx(session, user=test_user, account=test_account,
                      ws=test_account.workspace_id, notes="#work")

    result = await _call("propose_update_transaction_notes")(
        session=session, ctx=ctx, transaction_id=str(tx.id), notes="   "
    )

    assert result["changes"] == {"notes": None}


async def test_notes_no_changes_requested(
    session: AsyncSession, ctx: CallContext, test_user, test_account
):
    tx = await _mk_tx(session, user=test_user, account=test_account, ws=test_account.workspace_id)

    result = await _call("propose_update_transaction_notes")(
        session=session, ctx=ctx, transaction_id=str(tx.id)
    )

    assert result == {"error": "no changes provided"}


async def test_notes_tag_already_present_is_nothing_to_change(
    session: AsyncSession, ext_ctx: CallContext, test_user, test_account
):
    tx = await _mk_tx(session, user=test_user, account=test_account,
                      ws=test_account.workspace_id, notes="lunch #trip")

    result = await _call("propose_update_transaction_notes")(
        session=session, ctx=ext_ctx, transaction_id=str(tx.id), add_tags=["trip"], apply=True
    )

    assert "applied" not in result
    assert "nothing to change" in result["error"]


async def test_notes_longer_than_the_column_are_refused_before_applying(
    session: AsyncSession, ext_ctx: CallContext, test_user, test_account
):
    tx = await _mk_tx(session, user=test_user, account=test_account,
                      ws=test_account.workspace_id, notes="a" * 995)

    result = await _call("propose_update_transaction_notes")(
        session=session, ctx=ext_ctx, transaction_id=str(tx.id), add_tags=["travel"], apply=True
    )

    assert result == {"error": "notes must be 1000 characters or fewer"}
    await session.refresh(tx)
    assert tx.notes == "a" * 995


async def test_notes_unknown_and_cross_workspace_transaction(
    session: AsyncSession, ext_ctx: CallContext, test_user, test_account, other_workspace
):
    foreign = await _mk_tx(session, user=test_user, account=test_account,
                           ws=other_workspace, notes="keep me")

    unknown = await _call("propose_update_transaction_notes")(
        session=session, ctx=ext_ctx, transaction_id=str(uuid.uuid4()), add_tags=["x"]
    )
    cross = await _call("propose_update_transaction_notes")(
        session=session, ctx=ext_ctx, transaction_id=str(foreign.id),
        notes="changed", apply=True,
    )

    assert unknown == {"error": "transaction not found"}
    assert cross == {"error": "transaction not found"}
    assert (await _fetch(session, foreign.id)).notes == "keep me"


async def test_notes_internal_apply_does_not_write(
    session: AsyncSession, ctx: CallContext, test_user, test_account
):
    tx = await _mk_tx(session, user=test_user, account=test_account,
                      ws=test_account.workspace_id, notes="before")

    result = await _call("propose_update_transaction_notes")(
        session=session, ctx=ctx, transaction_id=str(tx.id), notes="after", apply=True
    )

    assert "applied" not in result
    assert (await _fetch(session, tx.id)).notes == "before"


async def test_notes_external_apply_changes_only_the_notes(
    session: AsyncSession, ext_ctx: CallContext, test_user, test_account, test_categories
):
    tx = await _mk_tx(session, user=test_user, account=test_account,
                      ws=test_account.workspace_id, notes="Uber to the airport #work")
    account_id, category_id = test_account.id, test_categories[0].id
    tx.category_id = category_id
    await session.commit()

    result = await _call("propose_update_transaction_notes")(
        session=session, ctx=ext_ctx, transaction_id=str(tx.id),
        remove_tags=["work"], add_tags=["trip-lisbon"], apply=True,
    )

    assert result.get("applied") is True, result
    row = await _fetch(session, tx.id)
    assert row.notes == "Uber to the airport #trip-lisbon"
    assert row.amount == Decimal("42.50")
    assert row.date == date(2026, 10, 1)
    assert row.account_id == account_id
    assert row.category_id == category_id
    assert row.description == "ALMOCO"


async def test_notes_on_a_transfer_leg_follow_to_the_other_leg(
    session: AsyncSession, ext_ctx: CallContext, test_user, test_account, test_connection
):
    """Same behaviour as editing the notes in the UI: the pair shares them,
    so the preview names the pair before the user confirms."""
    savings = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        connection_id=test_connection.id,
        external_id="acc-ext-456",
        name="Conta Poupanca",
        type="savings",
        balance=Decimal("1000.00"),
        currency="BRL",
    )
    session.add(savings)
    await session.commit()
    ws = test_account.workspace_id
    debit = await _mk_tx(session, user=test_user, account=test_account, ws=ws)
    credit = await _mk_tx(session, user=test_user, account=savings, ws=ws, tx_type="credit")
    await transaction_service.link_existing_as_transfer(session, ws, [debit.id, credit.id])
    debit = await _fetch(session, debit.id)

    result = await _call("propose_update_transaction_notes")(
        session=session, ctx=ext_ctx, transaction_id=str(debit.id),
        add_tags=["savings"], apply=True,
    )

    assert result["transfer_pair_id"] == str(debit.transfer_pair_id)
    assert result.get("applied") is True, result
    assert (await _fetch(session, credit.id)).notes == "#savings"


# --- propose_create_category_group ------------------------------------------


async def _group_count(session: AsyncSession, ws_id) -> int:
    return (await session.execute(
        select(func.count()).select_from(CategoryGroup).where(CategoryGroup.workspace_id == ws_id)
    )).scalar_one()


async def test_group_preview_uses_defaults_and_does_not_write(
    session: AsyncSession, ctx: CallContext, test_workspace
):
    before = await _group_count(session, test_workspace.id)

    result = await _call("propose_create_category_group")(
        session=session, ctx=ctx, name="  Pets  "
    )

    assert result["kind"] == "create_category_group"
    assert result["proposed"] == {"name": "Pets", "icon": "folder", "color": "#6B7280"}
    assert result["name_collision"] is None
    assert result["apply_endpoint"] == "POST /api/category-groups"
    assert "applied" not in result
    assert await _group_count(session, test_workspace.id) == before


async def test_group_internal_apply_does_not_write(
    session: AsyncSession, ctx: CallContext, test_workspace
):
    before = await _group_count(session, test_workspace.id)

    result = await _call("propose_create_category_group")(
        session=session, ctx=ctx, name="Pets", apply=True
    )

    assert "applied" not in result
    assert await _group_count(session, test_workspace.id) == before


async def test_group_external_apply_creates_it_for_new_categories(
    session: AsyncSession, ext_ctx: CallContext, test_workspace
):
    result = await _call("propose_create_category_group")(
        session=session, ctx=ext_ctx, name="Pets", icon="paw-print", color="#10B981", apply=True
    )

    assert result.get("applied") is True, result
    group = (await session.execute(
        select(CategoryGroup).where(CategoryGroup.id == uuid.UUID(result["id"]))
    )).scalar_one()
    assert (group.name, group.icon, group.color) == ("Pets", "paw-print", "#10B981")
    assert group.workspace_id == test_workspace.id

    # The returned id is what propose_create_category takes as group_id.
    created = await _call("propose_create_category")(
        session=session, ctx=ext_ctx, name="Vet", group_id=result["id"], apply=True
    )
    category = (await session.execute(
        select(Category).where(Category.id == uuid.UUID(created["id"]))
    )).scalar_one()
    assert category.group_id == group.id


async def test_group_name_collision_blocks_apply(
    session: AsyncSession, ext_ctx: CallContext, test_user, test_workspace
):
    session.add(CategoryGroup(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id, name="Pets",
    ))
    await session.commit()
    before = await _group_count(session, test_workspace.id)

    result = await _call("propose_create_category_group")(
        session=session, ctx=ext_ctx, name="pets", apply=True
    )

    assert result["name_collision"]["name"] == "Pets"
    assert "applied" not in result
    assert "already exists" in result["error"]
    assert await _group_count(session, test_workspace.id) == before


async def test_group_name_with_like_wildcards_matches_only_itself(
    session: AsyncSession, ctx: CallContext, test_user, test_workspace
):
    session.add(CategoryGroup(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id, name="alphaXbeta",
    ))
    await session.commit()
    propose = _call("propose_create_category_group")

    for name in ("alpha_beta", "alpha%", "%"):
        result = await propose(session=session, ctx=ctx, name=name)
        assert result["name_collision"] is None, name
    result = await propose(session=session, ctx=ctx, name="ALPHAxBETA")
    assert result["name_collision"]["name"] == "alphaXbeta"


async def test_group_with_same_name_in_another_workspace_is_no_collision(
    session: AsyncSession, ctx: CallContext, test_user, other_workspace
):
    session.add(CategoryGroup(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=other_workspace.id, name="Pets",
    ))
    await session.commit()

    result = await _call("propose_create_category_group")(
        session=session, ctx=ctx, name="Pets"
    )

    assert result["name_collision"] is None
