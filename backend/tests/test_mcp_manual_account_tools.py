"""MCP write tools for manual accounts.

The tools only write when an external caller explicitly sets ``apply``. Preview
mode must remain side-effect free, and connected accounts remain owned by their
sync provider.
"""

import math
import uuid
from datetime import date
from decimal import Decimal

import pytest_asyncio
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import mcp_server.tools  # noqa: F401
from app.agents.mcp.auth import mint_token
from app.core.app_clock import app_today
from app.models.account import Account
from app.models.transaction import Transaction
from app.models.workspace import Workspace, WorkspaceMember
from mcp_server.auth import CallContext
from mcp_server.protocol import tool_annotations
from mcp_server.registry import REGISTRY


@pytest_asyncio.fixture
async def ctx(test_user) -> CallContext:
    return CallContext(user_id=test_user.id, conversation_id=uuid.uuid4())


@pytest_asyncio.fixture
async def ext_ctx(test_user) -> CallContext:
    return CallContext(
        user_id=test_user.id,
        conversation_id=uuid.uuid4(),
        external=True,
    )


@pytest_asyncio.fixture
async def other_workspace(session: AsyncSession, test_user) -> Workspace:
    workspace = Workspace(
        id=uuid.uuid4(),
        name="Outro Espaco",
        kind="personal",
        created_by_user_id=test_user.id,
        default_currency="BRL",
        locale="pt-BR",
    )
    session.add(workspace)
    await session.flush()
    session.add(
        WorkspaceMember(
            id=uuid.uuid4(),
            workspace_id=workspace.id,
            user_id=test_user.id,
            role="owner",
        )
    )
    await session.commit()
    await session.refresh(workspace)
    return workspace


async def _manual_account(
    session: AsyncSession,
    *,
    user,
    workspace,
    name: str = "CommBank",
    account_type: str = "checking",
    balance: str = "4493.28",
    currency: str = "AUD",
    with_opening: bool = True,
    opening_date: date = date(2026, 11, 3),
) -> Account:
    opening_balance = Decimal(balance)
    account = Account(
        id=uuid.uuid4(),
        user_id=user.id,
        workspace_id=workspace.id,
        name=name,
        type=account_type,
        balance=opening_balance,
        currency=currency,
    )
    session.add(account)
    if with_opening and opening_balance != 0:
        is_credit = (opening_balance > 0) == (account_type not in {"credit_card", "loan"})
        session.add(
            Transaction(
                id=uuid.uuid4(),
                user_id=user.id,
                workspace_id=workspace.id,
                account_id=account.id,
                description="Saldo inicial",
                amount=abs(opening_balance),
                currency=currency,
                date=opening_date,
                type="credit" if is_credit else "debit",
                source="opening_balance",
            )
        )
    await session.commit()
    await session.refresh(account)
    return account


async def _account(
    session: AsyncSession, account_id: uuid.UUID
) -> Account | None:
    session.expire_all()
    return (
        await session.execute(select(Account).where(Account.id == account_id))
    ).scalar_one_or_none()


def _call(name: str):
    return REGISTRY[name].handler


def test_manual_account_tools_are_registered_as_proposals():
    expected = {
        "propose_create_manual_account",
        "propose_update_manual_account",
    }

    assert expected <= REGISTRY.keys()
    for name in expected:
        spec = REGISTRY[name]
        assert spec.is_proposal is True
        assert "apply" in spec.parameters["properties"]

    assert tool_annotations(
        "propose_create_manual_account", is_proposal=True
    )["destructiveHint"] is False
    assert "destructiveHint" not in tool_annotations(
        "propose_update_manual_account", is_proposal=True
    )


def test_manual_account_schemas_are_narrow():
    assert set(
        REGISTRY["propose_create_manual_account"].parameters["properties"]
    ) == {
        "name",
        "type",
        "currency",
        "opening_balance",
        "opening_balance_date",
        "apply",
    }
    assert set(
        REGISTRY["propose_update_manual_account"].parameters["properties"]
    ) == {
        "account_id",
        "name",
        "type",
        "opening_balance",
        "opening_balance_date",
        "apply",
    }


async def test_create_preview_normalizes_values_and_does_not_write(
    session: AsyncSession, ctx: CallContext, test_user, test_workspace
):
    result = await _call("propose_create_manual_account")(
        session=session,
        ctx=ctx,
        name="  CommBank  ",
        type="checking",
        currency="aud",
        opening_balance=4493.28,
        opening_balance_date="2026-11-10",
    )

    assert result["kind"] == "create_manual_account"
    assert result["proposed"] == {
        "name": "CommBank",
        "type": "checking",
        "currency": "AUD",
        "current_balance": 4493.28,
        "opening_balance": 4493.28,
        "opening_balance_date": "2026-11-10",
    }
    assert result["existing_accounts"] == []
    assert result["apply_endpoint"] == "POST /api/accounts"
    assert "applied" not in result

    rows = (
        await session.execute(
            select(Account).where(Account.workspace_id == test_workspace.id)
        )
    ).scalars().all()
    assert rows == []


async def test_create_apply_creates_manual_account_and_opening_balance(
    session: AsyncSession, ext_ctx: CallContext, test_user, test_workspace
):
    result = await _call("propose_create_manual_account")(
        session=session,
        ctx=ext_ctx,
        name="Ubank",
        type="savings",
        currency="AUD",
        opening_balance=3272.25,
        opening_balance_date="2026-11-10",
        apply=True,
    )

    assert result["applied"] is True
    account = await _account(session, uuid.UUID(result["id"]))
    assert account is not None
    assert account.connection_id is None
    assert (account.name, account.type, account.currency) == (
        "Ubank",
        "savings",
        "AUD",
    )
    assert account.balance == Decimal("3272.25")

    opening = (
        await session.execute(
            select(Transaction).where(
                Transaction.account_id == account.id,
                Transaction.source == "opening_balance",
            )
        )
    ).scalar_one()
    assert opening.amount == Decimal("3272.25")
    assert opening.date == date(2026, 11, 10)


async def test_create_preview_uses_the_opening_date_that_apply_will_create(
    session: AsyncSession, ext_ctx: CallContext, test_user, test_workspace
):
    preview = await _call("propose_create_manual_account")(
        session=session,
        ctx=ext_ctx,
        name="CommBank",
        type="checking",
        currency="AUD",
        opening_balance=4493.28,
    )

    assert preview["proposed"]["opening_balance_date"] == app_today().isoformat()

    applied = await _call("propose_create_manual_account")(
        session=session,
        ctx=ext_ctx,
        name="CommBank",
        type="checking",
        currency="AUD",
        opening_balance=4493.28,
        apply=True,
    )
    opening = (
        await session.execute(
            select(Transaction).where(
                Transaction.account_id == uuid.UUID(applied["id"]),
                Transaction.source == "opening_balance",
            )
        )
    ).scalar_one()
    assert opening.date == app_today()


async def test_create_preview_warns_about_a_possible_duplicate(
    session: AsyncSession, ctx: CallContext, test_user, test_workspace
):
    existing = await _manual_account(
        session,
        user=test_user,
        workspace=test_workspace,
        account_type="savings",
    )

    result = await _call("propose_create_manual_account")(
        session=session,
        ctx=ctx,
        name=" commbank ",
        type="checking",
        currency="aud",
        opening_balance=5000,
    )

    assert result["existing_accounts"] == [
        {
            "id": str(existing.id),
            "name": "CommBank",
            "type": "savings",
            "currency": "AUD",
            "current_balance": 4493.28,
            "opening_balance": 4493.28,
            "opening_balance_date": "2026-11-03",
        }
    ]


async def test_internal_runtime_cannot_apply_create(
    session: AsyncSession, ctx: CallContext, test_user, test_workspace
):
    result = await _call("propose_create_manual_account")(
        session=session,
        ctx=ctx,
        name="CommBank",
        type="checking",
        currency="AUD",
        opening_balance=4493.28,
        apply=True,
    )

    assert "applied" not in result
    rows = (
        await session.execute(
            select(Account).where(Account.workspace_id == test_workspace.id)
        )
    ).scalars().all()
    assert rows == []


async def test_read_only_external_role_cannot_apply_create(
    session: AsyncSession, test_user, test_workspace
):
    read_only = CallContext(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        external=True,
        role="viewer",
    )

    result = await _call("propose_create_manual_account")(
        session=session,
        ctx=read_only,
        name="CommBank",
        type="checking",
        currency="AUD",
        opening_balance=4493.28,
        apply=True,
    )

    assert "applied" not in result
    rows = (
        await session.execute(
            select(Account).where(Account.workspace_id == test_workspace.id)
        )
    ).scalars().all()
    assert rows == []


async def test_update_preview_shows_current_and_proposed_values(
    session: AsyncSession, ctx: CallContext, test_user, test_workspace
):
    account = await _manual_account(
        session, user=test_user, workspace=test_workspace
    )

    result = await _call("propose_update_manual_account")(
        session=session,
        ctx=ctx,
        account_id=str(account.id),
        name="  Commonwealth  ",
        opening_balance=5000,
        opening_balance_date="2026-11-10",
    )

    assert result["kind"] == "update_manual_account"
    assert result["target"] == {
        "id": str(account.id),
        "name": "CommBank",
        "type": "checking",
        "currency": "AUD",
        "current_balance": 4493.28,
        "opening_balance": 4493.28,
        "opening_balance_date": "2026-11-03",
    }
    assert result["changes"] == {
        "name": "Commonwealth",
        "opening_balance": 5000.0,
        "opening_balance_date": "2026-11-10",
    }
    assert result["existing_accounts"] == []
    assert result["apply_endpoint"] == f"PATCH /api/accounts/{account.id}"
    assert "applied" not in result

    unchanged = await _account(session, account.id)
    assert unchanged is not None
    assert (unchanged.name, unchanged.balance) == ("CommBank", Decimal("4493.28"))


async def test_account_previews_show_current_balance_separately_from_opening_balance(
    session: AsyncSession, ctx: CallContext, test_user, test_workspace
):
    account = await _manual_account(
        session, user=test_user, workspace=test_workspace
    )
    session.add(
        Transaction(
            id=uuid.uuid4(),
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            account_id=account.id,
            description="Compra",
            amount=Decimal("100.00"),
            currency="AUD",
            date=date(2026, 11, 4),
            type="debit",
            source="manual",
        )
    )
    await session.commit()

    result = await _call("propose_update_manual_account")(
        session=session,
        ctx=ctx,
        account_id=str(account.id),
        name="Commonwealth",
    )

    assert result["target"]["opening_balance"] == 4493.28
    assert result["target"]["current_balance"] == 4393.28
    assert result["target"]["opening_balance_date"] == "2026-11-03"
    assert result["changes"] == {"name": "Commonwealth"}


async def test_list_accounts_names_the_opening_and_current_balances(
    session: AsyncSession, ctx: CallContext, test_user, test_workspace
):
    account = await _manual_account(
        session, user=test_user, workspace=test_workspace
    )
    session.add(
        Transaction(
            id=uuid.uuid4(),
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            account_id=account.id,
            description="Compra",
            amount=Decimal("100.00"),
            currency="AUD",
            date=date(2026, 11, 4),
            type="debit",
            source="manual",
        )
    )
    await session.commit()

    result = await _call("list_accounts")(session=session, ctx=ctx)
    item = next(row for row in result["items"] if row["id"] == str(account.id))

    assert item["balance"] == 4393.28
    assert item["current_balance"] == 4393.28
    assert item["opening_balance"] == 4493.28


async def test_update_preview_warns_about_a_possible_duplicate(
    session: AsyncSession, ctx: CallContext, test_user, test_workspace
):
    account = await _manual_account(
        session, user=test_user, workspace=test_workspace
    )
    duplicate = await _manual_account(
        session,
        user=test_user,
        workspace=test_workspace,
        name="Commonwealth Bank",
    )

    result = await _call("propose_update_manual_account")(
        session=session,
        ctx=ctx,
        account_id=str(account.id),
        name="commonwealth bank",
    )

    assert result["existing_accounts"] == [
        {
            "id": str(duplicate.id),
            "name": "Commonwealth Bank",
            "type": "checking",
            "currency": "AUD",
            "current_balance": 4493.28,
            "opening_balance": 4493.28,
            "opening_balance_date": "2026-11-03",
        }
    ]


async def test_update_apply_changes_manual_account_and_opening_balance(
    session: AsyncSession, ext_ctx: CallContext, test_user, test_workspace
):
    account = await _manual_account(
        session, user=test_user, workspace=test_workspace
    )

    result = await _call("propose_update_manual_account")(
        session=session,
        ctx=ext_ctx,
        account_id=str(account.id),
        name="Commonwealth Bank",
        opening_balance=5100,
        opening_balance_date="2026-11-11",
        apply=True,
    )

    assert result["applied"] is True
    updated = await _account(session, account.id)
    assert updated is not None
    assert (updated.name, updated.balance) == (
        "Commonwealth Bank",
        Decimal("5100.00"),
    )

    opening = (
        await session.execute(
            select(Transaction).where(
                Transaction.account_id == account.id,
                Transaction.source == "opening_balance",
            )
        )
    ).scalar_one()
    assert opening.amount == Decimal("5100.00")
    assert opening.date == date(2026, 11, 11)


async def test_update_refuses_connected_accounts(
    session: AsyncSession, ext_ctx: CallContext, test_account
):
    result = await _call("propose_update_manual_account")(
        session=session,
        ctx=ext_ctx,
        account_id=str(test_account.id),
        opening_balance=2000,
        apply=True,
    )

    assert "bank-connected" in result["error"]
    assert not result.get("applied", False)


async def test_account_tools_refuse_manual_account_types_outside_scope(
    session: AsyncSession, ext_ctx: CallContext, test_user, test_workspace
):
    account = await _manual_account(
        session,
        user=test_user,
        workspace=test_workspace,
        name="Cartao manual",
        account_type="credit_card",
        balance="1000.00",
    )

    result = await _call("propose_update_manual_account")(
        session=session,
        ctx=ext_ctx,
        account_id=str(account.id),
        opening_balance=900,
        apply=True,
    )

    assert "outside this tool's scope" in result["error"]
    unchanged = await _account(session, account.id)
    assert unchanged is not None
    assert unchanged.balance == Decimal("1000.00")


async def test_update_refuses_closed_manual_accounts(
    session: AsyncSession, ext_ctx: CallContext, test_user, test_workspace
):
    account = await _manual_account(
        session, user=test_user, workspace=test_workspace
    )
    account.is_closed = True
    await session.commit()

    result = await _call("propose_update_manual_account")(
        session=session,
        ctx=ext_ctx,
        account_id=str(account.id),
        opening_balance=5000,
        apply=True,
    )

    assert "closed" in result["error"]
    unchanged = await _account(session, account.id)
    assert unchanged is not None
    assert unchanged.balance == Decimal("4493.28")


async def test_manual_account_amounts_must_be_finite(
    session: AsyncSession, ext_ctx: CallContext, test_user, test_workspace
):
    account = await _manual_account(
        session, user=test_user, workspace=test_workspace
    )

    create_nan = await _call("propose_create_manual_account")(
        session=session,
        ctx=ext_ctx,
        name="NaN",
        type="checking",
        currency="AUD",
        opening_balance=math.nan,
        apply=True,
    )
    update_inf = await _call("propose_update_manual_account")(
        session=session,
        ctx=ext_ctx,
        account_id=str(account.id),
        opening_balance=math.inf,
        apply=True,
    )

    assert "opening_balance must be finite" in create_nan["error"]
    assert "opening_balance must be finite" in update_inf["error"]
    unchanged = await _account(session, account.id)
    assert unchanged is not None
    assert unchanged.balance == Decimal("4493.28")


async def test_balance_date_requires_a_nonzero_balance(
    session: AsyncSession, ext_ctx: CallContext, test_user, test_workspace
):
    create = await _call("propose_create_manual_account")(
        session=session,
        ctx=ext_ctx,
        name="Empty",
        type="checking",
        currency="AUD",
        opening_balance=0,
        opening_balance_date="2026-11-10",
        apply=True,
    )
    account = await _manual_account(
        session,
        user=test_user,
        workspace=test_workspace,
        name="No opening",
        balance="0.00",
        with_opening=False,
    )
    update = await _call("propose_update_manual_account")(
        session=session,
        ctx=ext_ctx,
        account_id=str(account.id),
        opening_balance_date="2026-11-10",
        apply=True,
    )
    account_with_stored_balance = await _manual_account(
        session,
        user=test_user,
        workspace=test_workspace,
        name="Legacy",
        with_opening=False,
    )
    legacy_date_only = await _call("propose_update_manual_account")(
        session=session,
        ctx=ext_ctx,
        account_id=str(account_with_stored_balance.id),
        opening_balance_date="2026-11-10",
        apply=True,
    )

    assert "opening_balance_date requires a non-zero opening_balance" in create["error"]
    assert "opening_balance_date requires a non-zero opening_balance" in update["error"]
    assert "opening_balance_date requires an opening balance" in legacy_date_only["error"]


async def test_update_creates_an_opening_for_legacy_balance_and_date(
    session: AsyncSession, ext_ctx: CallContext, test_user, test_workspace
):
    account = await _manual_account(
        session,
        user=test_user,
        workspace=test_workspace,
        name="Legacy",
        with_opening=False,
    )

    preview = await _call("propose_update_manual_account")(
        session=session,
        ctx=ext_ctx,
        account_id=str(account.id),
        opening_balance=4493.28,
        opening_balance_date="2026-11-10",
    )
    assert preview["changes"] == {
        "opening_balance": 4493.28,
        "opening_balance_date": "2026-11-10",
    }

    result = await _call("propose_update_manual_account")(
        session=session,
        ctx=ext_ctx,
        account_id=str(account.id),
        opening_balance=4493.28,
        opening_balance_date="2026-11-10",
        apply=True,
    )

    assert result["applied"] is True
    opening = (
        await session.execute(
            select(Transaction).where(
                Transaction.account_id == account.id,
                Transaction.source == "opening_balance",
            )
        )
    ).scalar_one()
    assert opening.amount == Decimal("4493.28")
    assert opening.date == date(2026, 11, 10)


async def test_update_rejects_foreign_workspace_and_invalid_values(
    session: AsyncSession,
    ext_ctx: CallContext,
    test_user,
    test_workspace,
    other_workspace: Workspace,
):
    foreign = await _manual_account(
        session,
        user=test_user,
        workspace=other_workspace,
    )

    missing = await _call("propose_update_manual_account")(
        session=session,
        ctx=ext_ctx,
        account_id=str(foreign.id),
        opening_balance=1,
    )
    invalid_type = await _call("propose_create_manual_account")(
        session=session,
        ctx=ext_ctx,
        name="Bad",
        type="credit_card",
        currency="AUD",
        opening_balance=1,
    )
    invalid_currency = await _call("propose_create_manual_account")(
        session=session,
        ctx=ext_ctx,
        name="Bad",
        type="checking",
        currency="AUDX",
        opening_balance=1,
    )
    empty_name = await _call("propose_create_manual_account")(
        session=session,
        ctx=ext_ctx,
        name="   ",
        type="checking",
        currency="AUD",
        opening_balance=1,
    )

    assert missing == {"error": "account not found"}
    assert "type must be" in invalid_type["error"]
    assert "currency must be a three-letter code" in invalid_currency["error"]
    assert "name is required" in empty_name["error"]


async def test_update_no_changes_is_a_clear_error(
    session: AsyncSession, ctx: CallContext, test_user, test_workspace
):
    account = await _manual_account(
        session, user=test_user, workspace=test_workspace
    )

    result = await _call("propose_update_manual_account")(
        session=session,
        ctx=ctx,
        account_id=str(account.id),
        name="CommBank",
        opening_balance=4493.28,
    )

    assert "nothing to change" in result["error"]


async def _mcp_call(user_id, monkeypatch, name, arguments, *, external=False):
    import mcp_server.main as mcp_main
    from tests.conftest import TestSessionLocal

    monkeypatch.setattr(mcp_main, "async_session_maker", TestSessionLocal)
    token = mint_token(user_id=user_id, external=external)
    transport = httpx.ASGITransport(app=mcp_main.app)
    async with httpx.AsyncClient(transport=transport, base_url="https://mcp.test") as cli:
        response = await cli.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
            headers={"Authorization": f"Bearer {token}"},
        )
    result = response.json()["result"]
    assert result["isError"] is False, result
    return result["structuredContent"]


async def test_manual_account_create_over_mcp_requires_external_apply(
    session: AsyncSession,
    test_user,
    test_workspace,
    monkeypatch,
):
    arguments = {
        "name": "CommBank",
        "type": "checking",
        "currency": "AUD",
        "opening_balance": 4493.28,
        "opening_balance_date": "2026-11-10",
    }

    preview = await _mcp_call(
        test_user.id,
        monkeypatch,
        "propose_create_manual_account",
        arguments,
    )
    assert "applied" not in preview

    created = await _mcp_call(
        test_user.id,
        monkeypatch,
        "propose_create_manual_account",
        {**arguments, "apply": True},
        external=True,
    )
    assert created["applied"] is True

    account = await _account(session, uuid.UUID(created["id"]))
    assert account is not None
    assert (account.name, account.currency) == ("CommBank", "AUD")
