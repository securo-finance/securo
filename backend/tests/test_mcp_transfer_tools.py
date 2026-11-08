"""MCP transfer-pair tools: list candidates, propose link, propose unlink.

Exercises the handlers directly with seeded data (same pattern as
test_agents_mcp_tools.py). Covers the acceptance criteria from the
issue: cross-workspace ids, missing ids, already-applied/idempotent
states, and conflicting transfer state.
"""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Importing this package registers all tools into mcp_server.registry.REGISTRY.
import mcp_server.tools  # noqa: F401
from app.models.account import Account
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
async def second_account(session: AsyncSession, test_user, test_connection) -> Account:
    """A second account in the default workspace, for the other leg."""
    account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        connection_id=test_connection.id,
        external_id="acc-ext-456",
        name="Conta Poupanca",
        type="savings",
        balance=Decimal("1000.00"),
        currency="BRL",
    )
    session.add(account)
    await session.commit()
    await session.refresh(account)
    return account


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
    session: AsyncSession, test_user, test_connection, other_workspace
) -> Account:
    """An account that lives in the second workspace."""
    account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        connection_id=test_connection.id,
        external_id="acc-ext-789",
        name="Conta Outro Espaco",
        type="checking",
        balance=Decimal("500.00"),
        currency="BRL",
        workspace_id=other_workspace.id,
    )
    session.add(account)
    await session.commit()
    await session.refresh(account)
    return account


async def _mk_tx(
    session: AsyncSession,
    *,
    user,
    account: Account,
    ws: Workspace,
    description: str,
    amount,
    tx_type: str = "credit",
    day_offset: int = 0,
    source: str = "manual",
) -> Transaction:
    tx = Transaction(
        id=uuid.uuid4(),
        user_id=user.id,
        workspace_id=getattr(ws, "id", ws),
        account_id=account.id,
        description=description,
        amount=Decimal(str(amount)),
        date=date.today() + timedelta(days=day_offset),
        type=tx_type,
        source=source,
        currency="BRL",
        created_at=datetime.now(timezone.utc),
    )
    session.add(tx)
    await session.commit()
    await session.refresh(tx)
    return tx


async def _fetch(session: AsyncSession, tx_id: uuid.UUID) -> Transaction:
    result = await session.execute(select(Transaction).where(Transaction.id == tx_id))
    return result.scalar_one()


def _call(name: str):
    return REGISTRY[name].handler


# --- Registry shape ---------------------------------------------------------


def test_registry_contains_transfer_tools():
    for name in ("list_transfer_candidates", "propose_link_transfer", "propose_unlink_transfer"):
        assert name in REGISTRY, f"missing tool: {name}"
    assert REGISTRY["propose_link_transfer"].is_proposal is True
    assert REGISTRY["propose_unlink_transfer"].is_proposal is True
    assert REGISTRY["list_transfer_candidates"].is_proposal is False
    # The propose tools carry the apply flag, the read tool does not.
    assert "apply" in REGISTRY["propose_link_transfer"].parameters["properties"]
    assert "apply" in REGISTRY["propose_unlink_transfer"].parameters["properties"]
    assert "apply" not in REGISTRY["list_transfer_candidates"].parameters["properties"]


# --- list_transfer_candidates -----------------------------------------------


async def test_list_candidates_ranked(
    session: AsyncSession, ctx: CallContext, test_user, test_account, second_account, other_workspace, other_account
):
    ws = test_account.workspace_id
    anchor = await _mk_tx(session, user=test_user, account=test_account, ws=ws,
                          description="STORE A", amount="100.00", tx_type="debit")
    exact = await _mk_tx(session, user=test_user, account=second_account, ws=ws,
                         description="SALARY 100", amount="100.00")
    amount_off = await _mk_tx(session, user=test_user, account=second_account, ws=ws,
                              description="SALARY 90", amount="90.00")
    date_off = await _mk_tx(session, user=test_user, account=second_account, ws=ws,
                            description="SALARY LATER", amount="100.00", day_offset=2)
    # Decoys: same type, opening balance, already linked, outside window.
    await _mk_tx(session, user=test_user, account=second_account, ws=ws,
                 description="STORE B", amount="100.00", tx_type="debit")
    await _mk_tx(session, user=test_user, account=second_account, ws=ws,
                 description="OPENING CREDIT", amount="100.00", source="opening_balance")
    d1 = await _mk_tx(session, user=test_user, account=test_account, ws=ws,
                      description="STORE C", amount="10.00", tx_type="debit", day_offset=1)
    d2 = await _mk_tx(session, user=test_user, account=second_account, ws=ws,
                      description="SALARY 10", amount="10.00", day_offset=1)
    await transaction_service.link_existing_as_transfer(session, ws, [d1.id, d2.id])
    await _mk_tx(session, user=test_user, account=second_account, ws=ws,
                 description="SALARY FAR", amount="100.00", day_offset=40)
    # Cross-workspace decoy: same shape, different workspace.
    await _mk_tx(session, user=test_user, account=other_account, ws=other_workspace,
                 description="OTHER WORKSPACE CREDIT", amount="100.00")

    result = await _call("list_transfer_candidates")(
        session=session, ctx=ctx, transaction_id=str(anchor.id)
    )
    assert "error" not in result, result
    assert result["linked"] is False
    assert result["anchor"]["id"] == str(anchor.id)
    ids = [c["id"] for c in result["candidates"]]
    # Ranked by date proximity first, then primary-currency amount closeness.
    assert ids == [str(exact.id), str(amount_off.id), str(date_off.id)], ids
    # Each candidate carries the fields needed to compare both legs safely.
    for cand in result["candidates"]:
        for field in ("id", "description", "date", "amount", "currency",
                      "amount_primary", "type", "account_id", "account_name",
                      "category_id", "category_name", "status", "is_ignored"):
            assert field in cand, f"missing field {field}"
    assert all(c["type"] == "credit" for c in result["candidates"])
    assert all(c["account_id"] == str(second_account.id) for c in result["candidates"])


async def test_list_candidates_window_and_limit(
    session: AsyncSession, ctx: CallContext, test_user, test_account, second_account
):
    ws = test_account.workspace_id
    anchor = await _mk_tx(session, user=test_user, account=test_account, ws=ws,
                          description="STORE A", amount="100.00", tx_type="debit")
    await _mk_tx(session, user=test_user, account=second_account, ws=ws,
                 description="SALARY 100", amount="100.00")
    await _mk_tx(session, user=test_user, account=second_account, ws=ws,
                 description="SALARY 95", amount="95.00")
    await _mk_tx(session, user=test_user, account=second_account, ws=ws,
                 description="SALARY WEEK", amount="100.00", day_offset=14)

    narrow = await _call("list_transfer_candidates")(
        session=session, ctx=ctx, transaction_id=str(anchor.id), window_days=1
    )
    # The 14-day-away candidate falls outside a 1-day window.
    assert [c["description"] for c in narrow["candidates"]] == ["SALARY 100", "SALARY 95"]

    limited = await _call("list_transfer_candidates")(
        session=session, ctx=ctx, transaction_id=str(anchor.id), limit=1
    )
    assert len(limited["candidates"]) == 1


async def test_list_candidates_anchor_cross_workspace(
    session: AsyncSession, ctx: CallContext, test_user, other_workspace, other_account
):
    anchor = await _mk_tx(session, user=test_user, account=other_account, ws=other_workspace,
                          description="STORE A", amount="100.00", tx_type="debit")
    result = await _call("list_transfer_candidates")(
        session=session, ctx=ctx, transaction_id=str(anchor.id)
    )
    assert result == {"error": "transaction not found in this workspace"}


async def test_list_candidates_unknown_anchor(session: AsyncSession, ctx: CallContext):
    result = await _call("list_transfer_candidates")(
        session=session, ctx=ctx, transaction_id=str(uuid.uuid4())
    )
    assert result == {"error": "transaction not found in this workspace"}


async def test_list_candidates_already_linked(
    session: AsyncSession, ctx: CallContext, test_user, test_account, second_account
):
    ws = test_account.workspace_id
    d1 = await _mk_tx(session, user=test_user, account=test_account, ws=ws,
                      description="STORE A", amount="100.00", tx_type="debit")
    d2 = await _mk_tx(session, user=test_user, account=second_account, ws=ws,
                      description="SALARY 100", amount="100.00")
    await transaction_service.link_existing_as_transfer(session, ws, [d1.id, d2.id])

    result = await _call("list_transfer_candidates")(
        session=session, ctx=ctx, transaction_id=str(d1.id)
    )
    assert result["linked"] is True
    assert result["candidates"] == []
    assert result["transfer_pair_id"] == str(d1.transfer_pair_id)
    assert result["pair"]["id"] == str(d2.id)
    assert result["pair"]["account_name"] == second_account.name


# --- propose_link_transfer ---------------------------------------------------


async def _seed_linkable_pair(session, ctx_args) -> tuple[Transaction, Transaction]:
    test_user, test_account, second_account = ctx_args
    ws = test_account.workspace_id
    debit = await _mk_tx(session, user=test_user, account=test_account, ws=ws,
                         description="STORE A", amount="100.00", tx_type="debit")
    credit = await _mk_tx(session, user=test_user, account=second_account, ws=ws,
                          description="SALARY 100", amount="100.00")
    return debit, credit


async def test_propose_link_preview_does_not_write(
    session: AsyncSession, ctx: CallContext, test_user, test_account, second_account
):
    debit, credit = await _seed_linkable_pair(
        session, (test_user, test_account, second_account)
    )
    result = await _call("propose_link_transfer")(
        session=session, ctx=ctx,
        transaction_ids=[str(debit.id), str(credit.id)],
    )
    assert "error" not in result, result
    assert result["kind"] == "link_transfer"
    assert "applied" not in result
    assert result["apply_endpoint"] == "POST /api/transactions/link-transfer"
    legs = {leg["id"]: leg for leg in result["legs"]}
    assert legs[str(debit.id)]["type"] == "debit"
    assert legs[str(credit.id)]["type"] == "credit"
    assert legs[str(debit.id)]["account_name"] == test_account.name
    assert legs[str(credit.id)]["account_name"] == second_account.name
    # Nothing was written.
    for tx in (debit, credit):
        fresh = await _fetch(session, tx.id)
        assert fresh.transfer_pair_id is None


async def test_propose_link_apply_external_writes(
    session: AsyncSession, ext_ctx: CallContext, test_user, test_account, second_account
):
    debit, credit = await _seed_linkable_pair(
        session, (test_user, test_account, second_account)
    )
    result = await _call("propose_link_transfer")(
        session=session, ctx=ext_ctx,
        transaction_ids=[str(debit.id), str(credit.id)], apply=True,
    )
    assert "error" not in result, result
    assert result["applied"] is True
    pair_id = result["transfer_pair_id"]
    fresh_debit = await _fetch(session, debit.id)
    fresh_credit = await _fetch(session, credit.id)
    assert str(fresh_debit.transfer_pair_id) == pair_id
    assert str(fresh_credit.transfer_pair_id) == pair_id


async def test_propose_link_internal_cannot_apply(
    session: AsyncSession, ctx: CallContext, test_user, test_account, second_account
):
    debit, credit = await _seed_linkable_pair(
        session, (test_user, test_account, second_account)
    )
    result = await _call("propose_link_transfer")(
        session=session, ctx=ctx,
        transaction_ids=[str(debit.id), str(credit.id)], apply=True,
    )
    # Internal runtime: apply is silently ignored, preview only, no write.
    assert "applied" not in result
    assert result["kind"] == "link_transfer"
    for tx in (debit, credit):
        fresh = await _fetch(session, tx.id)
        assert fresh.transfer_pair_id is None


async def test_propose_link_validation_errors(
    session: AsyncSession, ctx: CallContext, test_user, test_account, second_account,
    other_workspace, other_account,
):
    ws = test_account.workspace_id
    same_acc_1 = await _mk_tx(session, user=test_user, account=test_account, ws=ws,
                              description="STORE A", amount="100.00", tx_type="debit")
    same_acc_2 = await _mk_tx(session, user=test_user, account=test_account, ws=ws,
                              description="STORE B", amount="100.00", tx_type="credit")
    same_type_1 = await _mk_tx(session, user=test_user, account=test_account, ws=ws,
                               description="STORE C", amount="50.00", tx_type="debit")
    same_type_2 = await _mk_tx(session, user=test_user, account=second_account, ws=ws,
                               description="STORE D", amount="50.00", tx_type="debit")
    other_ws_tx = await _mk_tx(session, user=test_user, account=other_account,
                               ws=other_workspace, description="STORE E", amount="100.00",
                               tx_type="credit")

    # Same account.
    r = await _call("propose_link_transfer")(
        session=session, ctx=ctx,
        transaction_ids=[str(same_acc_1.id), str(same_acc_2.id)])
    assert r["error"] == "transactions must be in different accounts"
    # Same type.
    r = await _call("propose_link_transfer")(
        session=session, ctx=ctx,
        transaction_ids=[str(same_type_1.id), str(same_type_2.id)])
    assert r["error"] == "transactions must be one debit and one credit"
    # Self link.
    r = await _call("propose_link_transfer")(
        session=session, ctx=ctx,
        transaction_ids=[str(same_acc_1.id), str(same_acc_1.id)])
    assert r["error"] == "cannot link a transaction to itself"
    # Wrong number of ids.
    r = await _call("propose_link_transfer")(
        session=session, ctx=ctx, transaction_ids=[str(same_acc_1.id)])
    assert r["error"] == "pass exactly two transaction ids"
    r = await _call("propose_link_transfer")(
        session=session, ctx=ctx,
        transaction_ids=[str(same_acc_1.id), str(same_acc_2.id), str(same_type_1.id)])
    assert r["error"] == "pass exactly two transaction ids"
    # One leg in another workspace.
    r = await _call("propose_link_transfer")(
        session=session, ctx=ctx,
        transaction_ids=[str(same_acc_1.id), str(other_ws_tx.id)])
    assert r["error"] == "one or both transactions not found in this workspace"
    # No writes happened anywhere.
    for tx in (same_acc_1, same_acc_2, same_type_1, same_type_2):
        fresh = await _fetch(session, tx.id)
        assert fresh.transfer_pair_id is None


async def test_propose_link_already_linked_preview_error(
    session: AsyncSession, ctx: CallContext, test_user, test_account, second_account
):
    ws = test_account.workspace_id
    d1 = await _mk_tx(session, user=test_user, account=test_account, ws=ws,
                      description="STORE A", amount="100.00", tx_type="debit")
    d2 = await _mk_tx(session, user=test_user, account=second_account, ws=ws,
                      description="SALARY 100", amount="100.00")
    await transaction_service.link_existing_as_transfer(session, ws, [d1.id, d2.id])
    d3 = await _mk_tx(session, user=test_user, account=test_account, ws=ws,
                      description="STORE B", amount="200.00", tx_type="debit")

    result = await _call("propose_link_transfer")(
        session=session, ctx=ctx, transaction_ids=[str(d1.id), str(d3.id)]
    )
    assert result["error"] == "Transaction is already part of a transfer"
    assert result["transaction_id"] == str(d1.id)
    assert result["transfer_pair_id"] == str(d1.transfer_pair_id)


async def test_propose_link_apply_is_idempotent_on_state(
    session: AsyncSession, ext_ctx: CallContext, test_user, test_account, second_account
):
    debit, credit = await _seed_linkable_pair(
        session, (test_user, test_account, second_account)
    )
    first = await _call("propose_link_transfer")(
        session=session, ctx=ext_ctx,
        transaction_ids=[str(debit.id), str(credit.id)], apply=True,
    )
    assert first["applied"] is True
    pair_id = first["transfer_pair_id"]
    # Re-applying the same preview fails on state, no second write.
    second = await _call("propose_link_transfer")(
        session=session, ctx=ext_ctx,
        transaction_ids=[str(debit.id), str(credit.id)], apply=True,
    )
    assert second["error"] == "Transaction is already part of a transfer"
    assert "applied" not in second
    fresh_debit = await _fetch(session, debit.id)
    assert str(fresh_debit.transfer_pair_id) == pair_id


# --- propose_unlink_transfer -------------------------------------------------


async def _seed_linked_pair(session, test_user, test_account, second_account):
    ws = test_account.workspace_id
    debit = await _mk_tx(session, user=test_user, account=test_account, ws=ws,
                         description="STORE A", amount="100.00", tx_type="debit")
    credit = await _mk_tx(session, user=test_user, account=second_account, ws=ws,
                          description="SALARY 100", amount="100.00")
    await transaction_service.link_existing_as_transfer(session, ws, [debit.id, credit.id])
    return debit, credit


async def test_propose_unlink_preview_does_not_write(
    session: AsyncSession, ctx: CallContext, test_user, test_account, second_account
):
    debit, credit = await _seed_linked_pair(session, test_user, test_account, second_account)
    result = await _call("propose_unlink_transfer")(
        session=session, ctx=ctx, transfer_pair_id=str(debit.transfer_pair_id)
    )
    assert "error" not in result, result
    assert result["kind"] == "unlink_transfer"
    assert "applied" not in result
    assert result["transfer_pair_id"] == str(debit.transfer_pair_id)
    assert {leg["id"] for leg in result["legs"]} == {str(debit.id), str(credit.id)}
    for tx in (debit, credit):
        fresh = await _fetch(session, tx.id)
        assert fresh.transfer_pair_id is not None


async def test_propose_unlink_apply_external_writes(
    session: AsyncSession, ext_ctx: CallContext, test_user, test_account, second_account
):
    debit, credit = await _seed_linked_pair(session, test_user, test_account, second_account)
    pair_id = str(debit.transfer_pair_id)
    result = await _call("propose_unlink_transfer")(
        session=session, ctx=ext_ctx, transfer_pair_id=pair_id, apply=True
    )
    assert "error" not in result, result
    assert result["applied"] is True
    assert result["unlinked_count"] == 2
    fresh_debit = await _fetch(session, debit.id)
    fresh_credit = await _fetch(session, credit.id)
    assert fresh_debit.transfer_pair_id is None
    assert fresh_credit.transfer_pair_id is None


async def test_propose_unlink_unknown_pair(session: AsyncSession, ctx: CallContext):
    result = await _call("propose_unlink_transfer")(
        session=session, ctx=ctx, transfer_pair_id=str(uuid.uuid4())
    )
    assert result == {"error": "transfer pair not found in this workspace"}


async def test_propose_unlink_cross_workspace(
    session: AsyncSession, ctx: CallContext, test_user, other_workspace, other_account
):
    # A pair that only exists in the other workspace.
    ws = other_workspace
    debit = await _mk_tx(session, user=test_user, account=other_account, ws=ws,
                         description="STORE A", amount="100.00", tx_type="debit")
    credit = await _mk_tx(session, user=test_user, account=other_account, ws=ws,
                          description="SALARY 100", amount="100.00")
    # Manually share a pair id: the service refuses same-account links, so
    # set the column directly for this scoping test.
    pair_id = uuid.uuid4()
    debit.transfer_pair_id = pair_id
    credit.transfer_pair_id = pair_id
    await session.commit()

    result = await _call("propose_unlink_transfer")(
        session=session, ctx=ctx, transfer_pair_id=str(pair_id)
    )
    assert result == {"error": "transfer pair not found in this workspace"}
    # The other-workspace pair is untouched.
    fresh = await _fetch(session, debit.id)
    assert str(fresh.transfer_pair_id) == str(pair_id)
