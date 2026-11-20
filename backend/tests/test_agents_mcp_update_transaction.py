"""MCP `propose_update_transaction`: preview, FX derivation, apply gate.

Exercises the handler directly with seeded data (same pattern as
test_mcp_notes_and_category_group_tools.py): the preview never writes, only
an external token with a writing role applies, ids from another workspace are
refused, `clear_fx` cannot be combined with explicit values, and a transfer
edit reaches the other leg.
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Importing this package registers all tools into mcp_server.registry.REGISTRY.
import mcp_server.tools  # noqa: F401
from app.models.account import Account
from app.models.category import Category
from app.models.fx_rate import FxRate
from app.models.transaction import Transaction
from app.models.workspace import Workspace, WorkspaceMember
from mcp_server.auth import CallContext
from mcp_server.registry import REGISTRY

TOOL = "propose_update_transaction"


async def _call(**kwargs):
    return await REGISTRY[TOOL].handler(**kwargs)


async def _fresh(session: AsyncSession, transaction_id) -> Transaction:
    return (
        await session.execute(
            select(Transaction)
            .where(Transaction.id == transaction_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


@pytest_asyncio.fixture
async def ctx(test_user) -> CallContext:
    """Internal runtime context (Securo's own agent): can never apply."""
    return CallContext(user_id=test_user.id, conversation_id=uuid.uuid4())


@pytest_asyncio.fixture
async def ext_ctx(test_user) -> CallContext:
    """External MCP token context (Claude Desktop, n8n): may apply."""
    return CallContext(user_id=test_user.id, conversation_id=uuid.uuid4(), external=True)


@pytest_asyncio.fixture
async def viewer_ctx(test_user) -> CallContext:
    """External token held by someone who may only read the workspace."""
    return CallContext(
        user_id=test_user.id, conversation_id=uuid.uuid4(), external=True, role="viewer"
    )


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


@pytest_asyncio.fixture
async def other_account(
    session: AsyncSession, test_user, other_workspace: Workspace
) -> Account:
    account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=other_workspace.id,
        name="Conta Outro Espaco",
        type="checking",
        balance=Decimal("500.00"),
        currency="BRL",
    )
    session.add(account)
    await session.commit()
    await session.refresh(account)
    return account


@pytest_asyncio.fixture
async def other_category(
    session: AsyncSession, test_user, other_workspace: Workspace
) -> Category:
    category = Category(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=other_workspace.id,
        name="Outro Espaco",
    )
    session.add(category)
    await session.commit()
    await session.refresh(category)
    return category


@pytest_asyncio.fixture
async def clp_transaction(
    session: AsyncSession, test_user, test_workspace, test_account: Account
) -> Transaction:
    """A CLP row imported without its primary amount (the issue's case)."""
    tx = Transaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=test_account.id,
        description="Transfer to Danny Tobar",
        amount=Decimal("671500"),
        currency="CLP",
        date=date(2023, 8, 12),
        effective_date=date(2023, 8, 12),
        type="debit",
        source="manual",
        status="posted",
        amount_primary=None,
        fx_rate_used=None,
    )
    session.add(tx)
    await session.commit()
    await session.refresh(tx)
    return tx


@pytest_asyncio.fixture
async def transfer_pair(
    session: AsyncSession, test_user, test_workspace, test_account: Account
) -> tuple[Transaction, Transaction]:
    """Two BRL legs sharing one transfer pair id."""
    pair_id = uuid.uuid4()
    debit = Transaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=test_account.id,
        description="To savings",
        amount=Decimal("100.00"),
        currency="BRL",
        date=date(2024, 3, 1),
        effective_date=date(2024, 3, 1),
        type="debit",
        source="manual",
        status="posted",
        transfer_pair_id=pair_id,
    )
    credit = Transaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=test_account.id,
        description="To savings",
        amount=Decimal("100.00"),
        currency="BRL",
        date=date(2024, 3, 1),
        effective_date=date(2024, 3, 1),
        type="credit",
        source="manual",
        status="posted",
        transfer_pair_id=pair_id,
    )
    session.add_all([debit, credit])
    await session.commit()
    await session.refresh(debit)
    await session.refresh(credit)
    return debit, credit


# --- Registry ---------------------------------------------------------------

def test_tool_is_registered_as_a_proposal():
    assert TOOL in REGISTRY
    assert REGISTRY[TOOL].is_proposal
    properties = set(REGISTRY[TOOL].parameters["properties"])
    assert {
        "transaction_id",
        "description",
        "amount",
        "date",
        "type",
        "currency",
        "account_id",
        "category_id",
        "payee_id",
        "notes",
        "amount_primary",
        "fx_rate_used",
        "is_ignored",
        "exclude_from_pnl",
        "status",
        "apply_to_transfer_pair",
        "clear_fx",
        "apply",
    } <= properties


# --- FX ---------------------------------------------------------------------

async def test_preview_derives_the_rate_and_does_not_write(
    session: AsyncSession, ext_ctx: CallContext, clp_transaction: Transaction
):
    result = await _call(
        session=session,
        ctx=ext_ctx,
        transaction_id=str(clp_transaction.id),
        amount_primary=3730.56,
    )

    assert result["kind"] == "update_transaction"
    assert result["target"]["currency"] == "CLP"
    assert result["target"]["amount"] == 671500.0
    assert result["target"]["amount_primary"] is None
    assert result["changes"]["amount_primary"] == 3730.56
    assert result["derived"]["fx_rate_used"] == pytest.approx(
        3730.56 / 671500, abs=1e-10
    )
    assert "applied" not in result

    fresh = await _fresh(session, clp_transaction.id)
    assert fresh.amount_primary is None
    assert fresh.fx_rate_used is None


async def test_apply_writes_the_primary_amount_and_the_derived_rate(
    session: AsyncSession, ext_ctx: CallContext, clp_transaction: Transaction
):
    result = await _call(
        session=session,
        ctx=ext_ctx,
        transaction_id=str(clp_transaction.id),
        amount_primary=3730.56,
        apply=True,
    )

    assert result["applied"] is True, result
    assert result["transaction"]["amount_primary"] == 3730.56

    fresh = await _fresh(session, clp_transaction.id)
    assert fresh.amount_primary == Decimal("3730.56")
    assert fresh.fx_rate_used is not None
    assert float(fresh.fx_rate_used) == pytest.approx(3730.56 / 671500, abs=1e-10)


async def test_rate_alone_derives_the_primary_amount_in_the_preview(
    session: AsyncSession, ext_ctx: CallContext, clp_transaction: Transaction
):
    result = await _call(
        session=session,
        ctx=ext_ctx,
        transaction_id=str(clp_transaction.id),
        fx_rate_used=0.0055555558,
    )

    assert result["derived"]["amount_primary"] == pytest.approx(3730.56, abs=0.01)
    fresh = await _fresh(session, clp_transaction.id)
    assert fresh.amount_primary is None


async def test_clear_fx_conflicts_with_explicit_values(
    session: AsyncSession, ext_ctx: CallContext, clp_transaction: Transaction
):
    result = await _call(
        session=session,
        ctx=ext_ctx,
        transaction_id=str(clp_transaction.id),
        amount_primary=3730.56,
        clear_fx=True,
    )

    assert "clear_fx" in result["error"]
    fresh = await _fresh(session, clp_transaction.id)
    assert fresh.amount_primary is None


async def test_clear_fx_previews_a_restamp(
    session: AsyncSession, ext_ctx: CallContext, clp_transaction: Transaction
):
    result = await _call(
        session=session,
        ctx=ext_ctx,
        transaction_id=str(clp_transaction.id),
        clear_fx=True,
    )

    assert result["derived"] == {"amount_primary": "auto", "fx_rate_used": "auto"}
    assert result["changes"]["amount_primary"] is None
    assert result["changes"]["fx_rate_used"] is None


async def test_clear_fx_drops_a_manual_override(
    session: AsyncSession, ext_ctx: CallContext, clp_transaction: Transaction
):
    clp_transaction.amount_primary = Decimal("3730.56")
    clp_transaction.fx_rate_used = Decimal("0.0055555558")
    await session.commit()

    result = await _call(
        session=session,
        ctx=ext_ctx,
        transaction_id=str(clp_transaction.id),
        clear_fx=True,
        apply=True,
    )

    assert result["applied"] is True, result
    fresh = await _fresh(session, clp_transaction.id)
    # Restamped by the save: the manual pair is gone.
    assert fresh.amount_primary != Decimal("3730.56")


# --- Restamp on an amount change (issue #271) --------------------------------

@pytest_asyncio.fixture
async def usd_row_with_manual_rate(
    session: AsyncSession, test_user, test_workspace, test_account: Account
) -> Transaction:
    """A USD row converted by hand at 5.5, next to a stored market rate of 5.0.

    Changing only its amount makes the save restamp the row from the market
    rate, so the typed rate is replaced (issue #271).
    """
    session.add(
        FxRate(
            base_currency="USD",
            quote_currency="BRL",
            date=date(2024, 5, 10),
            rate=Decimal("5.0"),
            source="test",
        )
    )
    tx = Transaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=test_account.id,
        description="Book from abroad",
        amount=Decimal("60.00"),
        currency="USD",
        date=date(2024, 5, 10),
        effective_date=date(2024, 5, 10),
        type="debit",
        source="manual",
        status="posted",
        amount_primary=Decimal("330.00"),
        fx_rate_used=Decimal("5.5"),
    )
    session.add(tx)
    await session.commit()
    await session.refresh(tx)
    return tx


async def test_a_new_amount_previews_the_restamped_conversion(
    session: AsyncSession, ext_ctx: CallContext, usd_row_with_manual_rate: Transaction
):
    tx = usd_row_with_manual_rate

    result = await _call(
        session=session, ctx=ext_ctx, transaction_id=str(tx.id), amount=70
    )

    assert result["derived"]["amount_primary"] == pytest.approx(350.0)
    assert result["derived"]["fx_rate_used"] == pytest.approx(5.0)
    assert "manual" in result["derived"]["note"]
    fresh = await _fresh(session, tx.id)
    assert fresh.amount == Decimal("60.00")
    assert fresh.amount_primary == Decimal("330.00")
    assert fresh.fx_rate_used == Decimal("5.5")


async def test_apply_writes_the_conversion_the_preview_showed(
    session: AsyncSession, ext_ctx: CallContext, usd_row_with_manual_rate: Transaction
):
    tx = usd_row_with_manual_rate

    preview = await _call(
        session=session, ctx=ext_ctx, transaction_id=str(tx.id), amount=70
    )
    applied = await _call(
        session=session, ctx=ext_ctx, transaction_id=str(tx.id), amount=70, apply=True
    )

    assert applied["applied"] is True, applied
    assert applied["transaction"]["amount_primary"] == preview["derived"]["amount_primary"]
    assert applied["transaction"]["fx_rate_used"] == pytest.approx(
        preview["derived"]["fx_rate_used"]
    )
    fresh = await _fresh(session, tx.id)
    assert fresh.amount == Decimal("70.00")
    assert fresh.amount_primary == Decimal("350.00")
    assert fresh.fx_rate_used == Decimal("5.0")


async def test_an_explicit_conversion_is_not_overridden_by_the_preview(
    session: AsyncSession, ext_ctx: CallContext, usd_row_with_manual_rate: Transaction
):
    """A row edited with `fx_rate_used` keeps that rate, and the preview says so."""
    tx = usd_row_with_manual_rate

    result = await _call(
        session=session,
        ctx=ext_ctx,
        transaction_id=str(tx.id),
        amount=70,
        fx_rate_used=5.5,
    )

    assert result["derived"]["amount_primary"] == pytest.approx(385.0)
    assert "note" not in result["derived"]


async def test_a_same_currency_amount_change_shows_no_conversion(
    session: AsyncSession, ext_ctx: CallContext, transfer_pair
):
    """A BRL row already in the primary currency has no conversion to show."""
    debit, _ = transfer_pair

    result = await _call(
        session=session, ctx=ext_ctx, transaction_id=str(debit.id), amount=150
    )

    assert "derived" not in result


# --- The apply gate ---------------------------------------------------------

async def test_internal_context_never_applies(
    session: AsyncSession, ctx: CallContext, clp_transaction: Transaction
):
    result = await _call(
        session=session,
        ctx=ctx,
        transaction_id=str(clp_transaction.id),
        description="Edited",
        apply=True,
    )

    assert "applied" not in result
    fresh = await _fresh(session, clp_transaction.id)
    assert fresh.description == "Transfer to Danny Tobar"


async def test_external_viewer_never_applies(
    session: AsyncSession, viewer_ctx: CallContext, clp_transaction: Transaction
):
    result = await _call(
        session=session,
        ctx=viewer_ctx,
        transaction_id=str(clp_transaction.id),
        description="Edited",
        apply=True,
    )

    assert "applied" not in result
    fresh = await _fresh(session, clp_transaction.id)
    assert fresh.description == "Transfer to Danny Tobar"


async def test_apply_saves_every_supported_field(
    session: AsyncSession,
    ext_ctx: CallContext,
    test_user,
    test_workspace,
    clp_transaction: Transaction,
    test_categories,
):
    category = test_categories[0]
    category_id = category.id
    category_name = category.name
    result = await _call(
        session=session,
        ctx=ext_ctx,
        transaction_id=str(clp_transaction.id),
        description="Corrected description",
        amount=500.0,
        date="2023-09-01",
        type="credit",
        currency="brl",
        category_id=str(category_id),
        notes="fixed by hand",
        is_ignored=True,
        exclude_from_pnl=True,
        status="pending",
        apply=True,
    )

    assert result["applied"] is True, result
    fresh = await _fresh(session, clp_transaction.id)
    assert fresh.description == "Corrected description"
    assert fresh.amount == Decimal("500.00")
    assert fresh.date == date(2023, 9, 1)
    assert fresh.type == "credit"
    assert fresh.currency == "BRL"
    assert fresh.category_id == category_id
    assert fresh.notes == "fixed by hand"
    assert fresh.is_ignored is True
    assert fresh.exclude_from_pnl is True
    assert fresh.status == "pending"
    assert result["transaction"]["category_name"] == category_name


# --- Scoping and validation -------------------------------------------------

async def test_transaction_in_another_workspace_is_not_found(
    session: AsyncSession,
    ext_ctx: CallContext,
    test_user,
    other_workspace: Workspace,
    other_account: Account,
):
    tx = Transaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=other_workspace.id,
        account_id=other_account.id,
        description="Elsewhere",
        amount=Decimal("10.00"),
        currency="BRL",
        date=date(2024, 1, 1),
        effective_date=date(2024, 1, 1),
        type="debit",
        source="manual",
        status="posted",
    )
    session.add(tx)
    await session.commit()

    result = await _call(
        session=session,
        ctx=ext_ctx,
        transaction_id=str(tx.id),
        description="Should not land",
        apply=True,
    )

    assert result["error"] == "transaction not found"
    fresh = await _fresh(session, tx.id)
    assert fresh.description == "Elsewhere"


async def test_account_from_another_workspace_is_refused(
    session: AsyncSession,
    ext_ctx: CallContext,
    clp_transaction: Transaction,
    other_account: Account,
):
    result = await _call(
        session=session,
        ctx=ext_ctx,
        transaction_id=str(clp_transaction.id),
        account_id=str(other_account.id),
    )

    assert result["error"] == "account not found"


async def test_category_from_another_workspace_is_refused(
    session: AsyncSession,
    ext_ctx: CallContext,
    clp_transaction: Transaction,
    other_category: Category,
):
    result = await _call(
        session=session,
        ctx=ext_ctx,
        transaction_id=str(clp_transaction.id),
        category_id=str(other_category.id),
    )

    assert result["error"] == "category not found"


async def test_update_without_changes_is_refused(
    session: AsyncSession, ext_ctx: CallContext, clp_transaction: Transaction
):
    result = await _call(
        session=session,
        ctx=ext_ctx,
        transaction_id=str(clp_transaction.id),
    )

    assert result["error"] == "no changes provided"


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        ({"description": "   "}, "description cannot be empty"),
        ({"amount": 0}, "amount must be greater than zero"),
        ({"amount": -5}, "amount must be greater than zero"),
        ({"date": "not-a-date"}, "invalid date"),
        ({"type": "transfer"}, "type must be 'debit' or 'credit'"),
        ({"currency": "AB"}, "currency must be a 3-letter code"),
        ({"amount_primary": 0}, "amount_primary must be greater than zero"),
        ({"fx_rate_used": -1}, "fx_rate_used must be greater than zero"),
        ({"status": "done"}, "status must be 'posted' or 'pending'"),
        ({"account_id": "not-a-uuid"}, "invalid account_id"),
        ({"notes": "x" * 1001}, "notes must be 1000 characters or fewer"),
    ],
)
async def test_invalid_values_are_refused_before_the_preview(
    session: AsyncSession,
    ext_ctx: CallContext,
    clp_transaction: Transaction,
    kwargs,
    expected,
):
    result = await _call(
        session=session,
        ctx=ext_ctx,
        transaction_id=str(clp_transaction.id),
        **kwargs,
    )

    assert result["error"] == expected


# --- Transfers --------------------------------------------------------------

async def test_preview_names_the_fields_that_follow_the_pair(
    session: AsyncSession,
    ext_ctx: CallContext,
    transfer_pair: tuple[Transaction, Transaction],
    test_categories,
):
    debit, _credit = transfer_pair
    result = await _call(
        session=session,
        ctx=ext_ctx,
        transaction_id=str(debit.id),
        amount=120.0,
        description="Moved to savings",
        category_id=str(test_categories[0].id),
        apply_to_transfer_pair=True,
    )

    assert result["transfer_pair"]["cascades"] == [
        "amount",
        "category_id",
        "description",
    ]


async def test_apply_cascades_amount_and_description_to_the_pair(
    session: AsyncSession,
    ext_ctx: CallContext,
    transfer_pair: tuple[Transaction, Transaction],
):
    debit, credit = transfer_pair
    result = await _call(
        session=session,
        ctx=ext_ctx,
        transaction_id=str(debit.id),
        amount=120.0,
        description="Moved to savings",
        apply=True,
    )

    assert result["applied"] is True, result
    fresh_pair = await _fresh(session, credit.id)
    assert fresh_pair.amount == Decimal("120.00")
    assert fresh_pair.description == "Moved to savings"


async def test_apply_sends_the_category_to_the_pair_only_when_asked(
    session: AsyncSession,
    ext_ctx: CallContext,
    transfer_pair: tuple[Transaction, Transaction],
    test_categories,
):
    debit, credit = transfer_pair
    category = test_categories[0]

    without = await _call(
        session=session,
        ctx=ext_ctx,
        transaction_id=str(debit.id),
        category_id=str(category.id),
        apply=True,
    )
    assert without["applied"] is True, without
    fresh_pair = await _fresh(session, credit.id)
    assert fresh_pair.category_id is None

    with_category = await _call(
        session=session,
        ctx=ext_ctx,
        transaction_id=str(debit.id),
        category_id=str(category.id),
        apply_to_transfer_pair=True,
        apply=True,
    )
    assert with_category["applied"] is True, with_category
    fresh_pair = await _fresh(session, credit.id)
    assert fresh_pair.category_id == category.id
