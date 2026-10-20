"""Tests for the payee update and merge MCP proposal tools."""

import uuid
from datetime import date
from decimal import Decimal

import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import mcp_server.tools  # noqa: F401
from app.models.account import Account
from app.models.payee import Payee, PayeeTaxId
from app.models.transaction import Transaction
from mcp_server.auth import CallContext
from mcp_server.registry import REGISTRY


@pytest_asyncio.fixture
async def external_ctx(test_user) -> CallContext:
    return CallContext(
        user_id=test_user.id,
        conversation_id=uuid.uuid4(),
        external=True,
    )


@pytest_asyncio.fixture
async def cpf_payee(session: AsyncSession, test_user, test_workspace) -> dict:
    payee = Payee(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="111.444.777-35",
        source="sync",
    )
    session.add(payee)
    await session.commit()
    await session.refresh(payee)
    return {
        "id": payee.id,
        "name": payee.name,
        "user_id": payee.user_id,
        "workspace_id": payee.workspace_id,
    }


@pytest_asyncio.fixture
async def target_payee(session: AsyncSession, test_user, test_workspace) -> dict:
    payee = Payee(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Alex Example",
        source="manual",
    )
    session.add(payee)
    await session.commit()
    await session.refresh(payee)
    return {
        "id": payee.id,
        "name": payee.name,
        "user_id": payee.user_id,
        "workspace_id": payee.workspace_id,
    }


async def _account_id(session: AsyncSession, workspace_id: uuid.UUID) -> uuid.UUID:
    account_id = await session.scalar(
        select(Account.id).where(Account.workspace_id == workspace_id).limit(1)
    )
    assert account_id is not None
    return account_id


async def test_payee_tools_are_registered():
    assert "propose_update_payee" in REGISTRY
    assert "propose_merge_payees" in REGISTRY
    assert "propose_create_payee" in REGISTRY
    assert REGISTRY["propose_update_payee"].is_proposal
    assert REGISTRY["propose_merge_payees"].is_proposal
    assert REGISTRY["propose_create_payee"].is_proposal


async def test_preview_update_payee_does_not_write(
    session: AsyncSession, external_ctx: CallContext, cpf_payee: dict
):
    payee_id = cpf_payee["id"]
    handler = REGISTRY["propose_update_payee"].handler
    result = await handler(
        session=session,
        ctx=external_ctx,
        payee_id=str(payee_id),
        name="Alex Example",
        tax_ids=[{"kind": "cpf", "value": "111.444.777-35"}],
    )

    assert result["kind"] == "update_payee"
    assert result["target"]["name"] == "111.444.777-35"
    assert result["changes"]["name"] == "Alex Example"
    assert result["changes"]["tax_ids"] == [{"kind": "cpf", "value": "11144477735"}]

    session.expire_all()
    fresh = (
        await session.execute(select(Payee).where(Payee.id == payee_id))
    ).scalar_one()
    tax_rows = (
        await session.execute(
            select(PayeeTaxId).where(PayeeTaxId.payee_id == payee_id)
        )
    ).scalars().all()
    assert fresh.name == "111.444.777-35"
    assert tax_rows == []


async def test_apply_update_payee_writes_name_and_cpf(
    session: AsyncSession, external_ctx: CallContext, cpf_payee: dict
):
    payee_id = cpf_payee["id"]
    handler = REGISTRY["propose_update_payee"].handler
    result = await handler(
        session=session,
        ctx=external_ctx,
        payee_id=str(payee_id),
        name="Alex Example",
        tax_ids=[{"kind": "cpf", "value": "111.444.777-35"}],
        apply=True,
    )

    assert result["applied"] is True
    assert result["payee"]["name"] == "Alex Example"
    assert result["payee"]["tax_ids"] == [{"kind": "cpf", "value": "11144477735"}]

    session.expire_all()
    fresh = (
        await session.execute(select(Payee).where(Payee.id == payee_id))
    ).scalar_one()
    tax_rows = (
        await session.execute(
            select(PayeeTaxId).where(PayeeTaxId.payee_id == payee_id)
        )
    ).scalars().all()
    assert fresh.name == "Alex Example"
    assert [(row.kind, row.value) for row in tax_rows] == [("cpf", "11144477735")]


async def test_invalid_cpf_is_refused_before_write(
    session: AsyncSession, external_ctx: CallContext, cpf_payee: dict
):
    handler = REGISTRY["propose_update_payee"].handler
    result = await handler(
        session=session,
        ctx=external_ctx,
        payee_id=str(cpf_payee["id"]),
        tax_ids=[{"kind": "cpf", "value": "123.456.789-00"}],
    )

    assert "error" in result
    assert result["error"].startswith("invalid_tax_id:cpf")


async def test_preview_merge_payees_counts_transactions(
    session: AsyncSession,
    external_ctx: CallContext,
    cpf_payee: dict,
    target_payee: dict,
    test_account,
):
    source_id = cpf_payee["id"]
    target_id = target_payee["id"]
    account_id = await _account_id(session, cpf_payee["workspace_id"])
    transaction = Transaction(
        id=uuid.uuid4(),
        user_id=cpf_payee["user_id"],
        workspace_id=cpf_payee["workspace_id"],
        account_id=account_id,
        description="PIX RECEBIDO ALEX EXAMPLE",
        amount=Decimal("200.00"),
        currency="BRL",
        date=date(2026, 8, 29),
        effective_date=date(2026, 8, 29),
        type="credit",
        source="sync",
        status="posted",
        payee_id=source_id,
    )
    session.add(transaction)
    await session.commit()

    handler = REGISTRY["propose_merge_payees"].handler
    result = await handler(
        session=session,
        ctx=external_ctx,
        source_ids=[str(source_id)],
        target_id=str(target_id),
    )

    assert result["kind"] == "merge_payees"
    assert result["target"]["id"] == str(target_id)
    assert result["sources"][0]["transaction_count"] == 1
    assert result["transactions_to_reassign"] == 1

    session.expire_all()
    still_there = (
        await session.execute(select(Payee).where(Payee.id == source_id))
    ).scalar_one_or_none()
    assert still_there is not None


async def test_apply_merge_payees_reassigns_and_deletes_source(
    session: AsyncSession,
    external_ctx: CallContext,
    cpf_payee: dict,
    target_payee: dict,
    test_account,
):
    source_id = cpf_payee["id"]
    target_id = target_payee["id"]
    account_id = await _account_id(session, cpf_payee["workspace_id"])
    transaction = Transaction(
        id=uuid.uuid4(),
        user_id=cpf_payee["user_id"],
        workspace_id=cpf_payee["workspace_id"],
        account_id=account_id,
        description="PIX RECEBIDO ALEX EXAMPLE",
        amount=Decimal("200.00"),
        currency="BRL",
        date=date(2026, 8, 29),
        effective_date=date(2026, 8, 29),
        type="credit",
        source="sync",
        status="posted",
        payee_id=source_id,
    )
    session.add(transaction)
    transaction_id = transaction.id
    await session.commit()

    handler = REGISTRY["propose_merge_payees"].handler
    result = await handler(
        session=session,
        ctx=external_ctx,
        source_ids=[str(source_id)],
        target_id=str(target_id),
        apply=True,
    )

    assert result["applied"] is True
    assert result["transactions_reassigned"] == 1

    session.expire_all()
    source = (
        await session.execute(select(Payee).where(Payee.id == source_id))
    ).scalar_one_or_none()
    moved = (
        await session.execute(
            select(Transaction).where(Transaction.id == transaction_id)
        )
    ).scalar_one()
    assert source is None
    assert moved.payee_id == target_id


async def test_merge_refuses_target_as_source(
    session: AsyncSession, external_ctx: CallContext, target_payee: dict
):
    target_id = target_payee["id"]
    handler = REGISTRY["propose_merge_payees"].handler
    result = await handler(
        session=session,
        ctx=external_ctx,
        source_ids=[str(target_id)],
        target_id=str(target_id),
    )

    assert result["error"] == "target payee cannot also be a source"


async def test_preview_create_payee_does_not_write(
    session: AsyncSession, external_ctx: CallContext
):
    handler = REGISTRY["propose_create_payee"].handler
    result = await handler(
        session=session,
        ctx=external_ctx,
        name="Alex Example",
        type="person",
    )

    assert result["kind"] == "create_payee"
    assert result["proposed"]["name"] == "Alex Example"
    assert result["proposed"]["type"] == "person"
    assert result["existing"] is None
    assert "applied" not in result

    session.expire_all()
    found = (
        await session.execute(
            select(Payee).where(Payee.name == "Alex Example")
        )
    ).scalar_one_or_none()
    assert found is None


async def test_apply_create_payee_writes_payee_and_cpf(
    session: AsyncSession, external_ctx: CallContext
):
    handler = REGISTRY["propose_create_payee"].handler
    result = await handler(
        session=session,
        ctx=external_ctx,
        name="Alex Example",
        type="person",
        tax_ids=[{"kind": "cpf", "value": "111.444.777-35"}],
        apply=True,
    )

    assert result["applied"] is True
    assert result["payee"]["name"] == "Alex Example"
    assert result["payee"]["source"] == "manual"
    assert result["payee"]["type"] == "person"
    assert result["payee"]["tax_ids"] == [{"kind": "cpf", "value": "11144477735"}]

    session.expire_all()
    fresh = (
        await session.execute(
            select(Payee).where(Payee.id == uuid.UUID(result["payee"]["id"]))
        )
    ).scalar_one()
    tax_rows = (
        await session.execute(
            select(PayeeTaxId).where(PayeeTaxId.payee_id == fresh.id)
        )
    ).scalars().all()
    assert fresh.name == "Alex Example"
    assert [(row.kind, row.value) for row in tax_rows] == [("cpf", "11144477735")]


async def test_create_payee_refuses_duplicate_name(
    session: AsyncSession, external_ctx: CallContext, target_payee: dict
):
    handler = REGISTRY["propose_create_payee"].handler
    result = await handler(
        session=session, ctx=external_ctx, name="  alex example  "
    )

    assert result["error"] == "duplicate_payee_name"
    assert result["existing"]["id"] == str(target_payee["id"])
    assert result["applied"] is False

    session.expire_all()
    count = await session.scalar(
        select(func.count())
        .select_from(Payee)
        .where(Payee.workspace_id == target_payee["workspace_id"])
    )
    assert count == 1


async def test_create_payee_refuses_invalid_cpf_before_write(
    session: AsyncSession, external_ctx: CallContext
):
    handler = REGISTRY["propose_create_payee"].handler
    result = await handler(
        session=session,
        ctx=external_ctx,
        name="Alex Example",
        tax_ids=[{"kind": "cpf", "value": "123.456.789-00"}],
    )

    assert result["error"].startswith("invalid_tax_id:cpf")
    session.expire_all()
    found = (
        await session.execute(
            select(Payee).where(Payee.name == "Alex Example")
        )
    ).scalar_one_or_none()
    assert found is None


async def test_internal_ctx_cannot_apply_create_payee(
    session: AsyncSession, test_user
):
    internal_ctx = CallContext(user_id=test_user.id, conversation_id=uuid.uuid4())
    handler = REGISTRY["propose_create_payee"].handler
    result = await handler(
        session=session,
        ctx=internal_ctx,
        name="Alex Example",
        apply=True,
    )

    assert "applied" not in result
    session.expire_all()
    found = (
        await session.execute(
            select(Payee).where(Payee.name == "Alex Example")
        )
    ).scalar_one_or_none()
    assert found is None
