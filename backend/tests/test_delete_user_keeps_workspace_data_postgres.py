"""Deleting a user takes their own data and nobody else's (ADR 0047).

On Postgres: the foreign keys are half of the behavior, and SQLite here has none.
"""

import asyncio
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.core.database import Base
from app.models.access_token import AccessToken
from app.models.account import Account
from app.models.asset import Asset
from app.models.bank_connection import BankConnection
from app.models.budget import Budget
from app.models.category import Category
from app.models.category_group import CategoryGroup
from app.models.collection import Collection
from app.models.goal import Goal
from app.models.group import Group, GroupMember
from app.models.group_settlement import GroupSettlement
from app.models.invoice import Invoice
from app.models.payee import Payee, PayeeMapping
from app.models.rule import Rule
from app.models.transaction import Transaction
from app.models.transaction_split import TransactionSplit
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember
from app.schemas.admin import AdminUserUpdate
from app.services import admin_service, user_removal_service, workspace_service

DAY = date(2026, 10, 5)
#: Every deletion is logged with why it happened (ADR 0042).
REASON = "Support ticket 12"

KEPT = (
    Account, Asset, BankConnection, Budget, Category, CategoryGroup, Collection, Goal, Group,
    GroupMember, Invoice, Payee, PayeeMapping, Rule, Transaction, TransactionSplit,
)


def _user(name, **over):
    return User(
        **{
            "id": uuid.uuid4(), "email": f"{name}@example.invalid", "hashed_password": "unused",
            "is_active": True, "is_superuser": False, "is_verified": True,
            "preferences": {"currency_display": "BRL", "display_name": name},
            **over,
        }
    )


async def _add(session, *rows):
    for row in rows:
        session.add(row)
        await session.flush()


async def _workspace(session, name, **roles):
    """A workspace and its members, `owner=person` style."""
    workspace = Workspace(id=uuid.uuid4(), name=name)
    await _add(session, workspace)
    for role, person in roles.items():
        await _add(
            session,
            WorkspaceMember(workspace_id=workspace.id, user_id=person.id, role=role),
        )
    return workspace


async def _fill(session, workspace, author):
    """One of everything in `workspace`, written by `author`."""
    base = {"user_id": author.id, "workspace_id": workspace.id}
    connection = BankConnection(
        **base, id=uuid.uuid4(), provider="test", external_id=str(uuid.uuid4()),
        institution_name="Bank",
    )
    account = Account(
        **base, id=uuid.uuid4(), name="Checking", type="checking", currency="BRL",
        connection_id=connection.id,
    )
    category_group = CategoryGroup(**base, id=uuid.uuid4(), name="Home")
    category = Category(**base, id=uuid.uuid4(), name="Rent", group_id=category_group.id)
    payee = Payee(**base, id=uuid.uuid4(), name="Landlord")
    asset = Asset(**base, id=uuid.uuid4(), name="Car", type="vehicle", currency="BRL")
    group = Group(**base, id=uuid.uuid4(), name="Trip")
    member = GroupMember(
        id=uuid.uuid4(), group_id=group.id, workspace_id=workspace.id, name="Them",
        linked_user_id=author.id,
    )
    transaction = Transaction(
        **base, id=uuid.uuid4(), account_id=account.id, category_id=category.id,
        payee_id=payee.id, description="Rent", amount=Decimal("100"), date=DAY,
        effective_date=DAY, type="debit", source="manual", currency="BRL",
    )
    await _add(
        session, connection, account, category_group, category, payee,
        PayeeMapping(**base, id=payee.id, target_id=payee.id),
        asset, group, member, transaction,
        TransactionSplit(
            transaction_id=transaction.id, workspace_id=workspace.id,
            group_member_id=member.id, share_amount=Decimal("50"),
        ),
        Rule(**base, name="Rent"),
        Budget(**base, category_id=category.id, amount=Decimal("100"), month=DAY),
        Goal(**base, name="Car", target_amount=Decimal("100"), asset_id=asset.id),
        Collection(**base, name="Home"),
        Invoice(
            **base, payee_id=payee.id, total=Decimal("10"), currency="BRL",
            issue_date=DAY, due_date=DAY,
        ),
    )
    return {
        "account": account, "connection": connection, "member": member,
        "transaction": transaction, "group": group,
    }


async def _counts(session, workspace_id):
    return {
        model.__name__: await session.scalar(
            select(func.count()).select_from(model).where(model.workspace_id == workspace_id)
        )
        for model in KEPT
    }


async def _people(session, *names):
    people = [_user("admin", is_superuser=True), *(_user(name) for name in names)]
    await _add(session, *people)
    return people


async def test_deleting_an_editor_keeps_what_they_added(postgres_sessions):
    async with postgres_sessions() as session:
        admin, owner, editor = await _people(session, "owner", "editor")
        editor_id = editor.id
        shared = await _workspace(session, "Household", owner=owner, editor=editor)
        rows = await _fill(session, shared, editor)
        # The owner's own row in an account the editor created.
        kept = Transaction(
            user_id=owner.id, workspace_id=shared.id, account_id=rows["account"].id,
            description="Salary", amount=Decimal("900"), date=DAY, effective_date=DAY,
            type="credit", source="manual", currency="BRL",
        )
        await _add(session, kept)
        before = await _counts(session, shared.id)
        assert min(before.values()) >= 1
        await session.commit()

        assert await admin_service.delete_user(session, editor_id, admin.id, reason=REASON)
        session.expunge_all()

        assert await _counts(session, shared.id) == before
        assert await admin_service.get_user(session, editor_id) is None
        listed, total = await admin_service.list_users(session)
        assert {user.email for user in listed} == {"admin@example.invalid", "owner@example.invalid"}
        assert total == 2
        assert not await admin_service.delete_user(session, editor_id, admin.id, reason=REASON)
        left = (await session.scalars(
            select(WorkspaceMember.user_id).where(WorkspaceMember.workspace_id == shared.id)
        )).all()
        assert left == [owner.id]
        # Nothing of the person is in the row that stays, and their address is free.
        row = await session.get_one(User, editor_id)
        assert not row.is_active and row.hashed_password != "unused"
        assert row.preferences == {"currency_display": "BRL"}
        await _add(session, _user("editor"))


async def test_a_deleted_account_cannot_be_edited_back(postgres_sessions):
    """The closed row is not an account: an edit finds nothing and changes nothing."""
    async with postgres_sessions() as session:
        admin, owner, editor = await _people(session, "owner", "editor")
        admin_id, editor_id = admin.id, editor.id
        shared = await _workspace(session, "Household", owner=owner, editor=editor)
        await _fill(session, shared, editor)
        await session.commit()
        assert await admin_service.delete_user(session, editor_id, admin_id, reason=REASON)
        session.expunge_all()

        edit = AdminUserUpdate(
            reason=REASON, is_active=True, is_superuser=True,
            email="back@example.com",
        )
        assert await admin_service.update_user(session, editor_id, edit, admin_id) is None
        await session.rollback()
        session.expunge_all()

        row = await session.get_one(User, editor_id)
        assert not row.is_active and not row.is_superuser
        assert row.pending_email is None
        assert not await admin_service.delete_user(session, editor_id, admin_id, reason=REASON)


async def test_personal_rows_go_with_the_editor(postgres_sessions):
    async with postgres_sessions() as session:
        admin, owner, editor = await _people(session, "owner", "editor")
        editor_id = editor.id
        shared = await _workspace(session, "Household", owner=owner, editor=editor)
        await _fill(session, shared, editor)
        await _add(
            session,
            AccessToken(
                user_id=editor_id, workspace_id=shared.id, name="Phone",
                token_hash="x" * 64, token_prefix="sk",
            ),
        )
        await session.commit()

        assert await admin_service.delete_user(session, editor_id, admin.id, reason=REASON)

        assert await session.scalar(select(func.count()).select_from(AccessToken)) == 0


async def test_an_editor_with_nothing_written_is_deleted_outright(postgres_sessions):
    async with postgres_sessions() as session:
        admin, owner, editor = await _people(session, "owner", "editor")
        editor_id = editor.id
        shared = await _workspace(session, "Household", owner=owner, editor=editor)
        await session.commit()

        assert await admin_service.delete_user(session, editor_id, admin.id, reason=REASON)
        session.expunge_all()

        assert await session.get(User, editor_id) is None
        assert await session.get(Workspace, shared.id) is not None


async def test_deleting_a_sole_owner_deletes_their_workspace_only(postgres_sessions):
    async with postgres_sessions() as session:
        admin, owner, other = await _people(session, "owner", "other")
        owner_id = owner.id
        own = await _workspace(session, "Mine", owner=owner)
        theirs = await _workspace(session, "Theirs", owner=other)
        mine = await _fill(session, own, owner)
        others = await _fill(session, theirs, other)
        # What the other workspace holds on this one.
        others["account"].connection_id = mine["connection"].id
        await _add(
            session,
            TransactionSplit(
                transaction_id=others["transaction"].id, workspace_id=theirs.id,
                group_member_id=mine["member"].id, share_amount=Decimal("25"),
            ),
            GroupSettlement(
                group_id=mine["group"].id, workspace_id=theirs.id,
                from_member_id=mine["member"].id, to_member_id=mine["member"].id,
                amount=Decimal("5"), currency="BRL", date=DAY,
            ),
        )
        before = await _counts(session, theirs.id)
        own_id, theirs_id = own.id, theirs.id
        await session.commit()

        assert await admin_service.delete_user(session, owner_id, admin.id, reason=REASON)
        session.expunge_all()

        assert await session.get(User, owner_id) is None
        assert await session.get(Workspace, own_id) is None
        assert set((await _counts(session, own_id)).values()) == {0}
        before["TransactionSplit"] -= 1
        assert await _counts(session, theirs_id) == before


async def test_a_managed_workspace_nobody_else_reaches_is_deleted(postgres_sessions):
    async with postgres_sessions() as session:
        admin, manager = await _people(session, "manager")
        managed = await _workspace(session, "Client")
        managed.managed_by_user_id = manager.id
        managed_id, manager_id = managed.id, manager.id
        await _fill(session, managed, manager)
        await session.commit()

        assert await admin_service.delete_user(session, manager_id, admin.id, reason=REASON)
        session.expunge_all()

        assert await session.get(Workspace, managed_id) is None
        assert await session.get(User, manager_id) is None


@pytest.mark.parametrize("by", ["owner", "manager"])
async def test_a_workspace_somebody_else_runs_is_kept(postgres_sessions, by):
    async with postgres_sessions() as session:
        admin, leaving, staying = await _people(session, "leaving", "staying")
        shared = await _workspace(session, "Household", owner=leaving)
        if by == "owner":
            await _add(
                session,
                WorkspaceMember(workspace_id=shared.id, user_id=staying.id, role="owner"),
            )
        else:
            shared.managed_by_user_id = staying.id
        await _fill(session, shared, leaving)
        before = await _counts(session, shared.id)
        shared_id = shared.id
        await session.commit()

        assert await admin_service.delete_user(session, leaving.id, admin.id, reason=REASON)
        session.expunge_all()

        assert await session.get(Workspace, shared_id) is not None
        assert await _counts(session, shared_id) == before


async def test_a_managers_client_with_its_own_owner_is_kept(postgres_sessions):
    async with postgres_sessions() as session:
        admin, manager, client = await _people(session, "manager", "client")
        managed = await _workspace(session, "Client", owner=client)
        managed.managed_by_user_id = manager.id
        await _fill(session, managed, manager)
        before = await _counts(session, managed.id)
        managed_id = managed.id
        await session.commit()

        assert await admin_service.delete_user(session, manager.id, admin.id, reason=REASON)
        session.expunge_all()

        kept = await session.get_one(Workspace, managed_id)
        assert kept.managed_by_user_id is None
        assert await _counts(session, managed_id) == before


async def test_the_only_owner_of_a_workspace_with_members_is_refused(postgres_sessions):
    async with postgres_sessions() as session:
        admin, owner, editor = await _people(session, "owner", "editor")
        shared = await _workspace(session, "Household", owner=owner, editor=editor)
        alone = await _workspace(session, "Alone", owner=owner)
        await _fill(session, shared, owner)
        await _fill(session, alone, owner)
        owner_id, shared_id, alone_id = owner.id, shared.id, alone.id
        before = [await _counts(session, shared_id), await _counts(session, alone_id)]
        await session.commit()

        with pytest.raises(ValueError, match="Household has other members"):
            await admin_service.delete_user(session, owner_id, admin.id, reason=REASON)
        await session.rollback()
        session.expunge_all()

        assert [await _counts(session, shared_id), await _counts(session, alone_id)] == before
        row = await session.get_one(User, owner_id)
        assert row.is_active and row.email == "owner@example.invalid"


@pytest.mark.parametrize("change", ["demote", "remove"])
async def test_a_co_owner_cannot_leave_while_the_other_is_deleted(
    postgres_sessions, monkeypatch, change,
):
    async with postgres_sessions() as session:
        admin, leaving, staying = await _people(session, "leaving", "staying")
        shared = await _workspace(session, "Household", owner=leaving)
        await _add(
            session, WorkspaceMember(workspace_id=shared.id, user_id=staying.id, role="owner")
        )
        ids = (shared.id, staying.id)
        await session.commit()

        decided, finish = asyncio.Event(), asyncio.Event()
        is_author = user_removal_service._is_author

        async def paused(*args):
            decided.set()
            await finish.wait()
            return await is_author(*args)

        monkeypatch.setattr(user_removal_service, "_is_author", paused)

        async def change_owner():
            async with postgres_sessions() as other:
                if change == "demote":
                    await workspace_service.update_member_role(other, *ids, "editor")
                else:
                    await workspace_service.remove_member(other, *ids)
                await other.commit()

        deleting = asyncio.create_task(
            admin_service.delete_user(session, leaving.id, admin.id, reason=REASON)
        )
        await asyncio.wait_for(decided.wait(), 5)
        changing = asyncio.create_task(change_owner())
        await asyncio.wait([changing], timeout=0.5)
        finish.set()
        assert await deleting
        with pytest.raises(HTTPException, match="sole owner"):
            await changing


async def test_a_member_cannot_be_promoted_while_they_are_deleted(postgres_sessions, monkeypatch):
    async with postgres_sessions() as session:
        admin, owner, editor = await _people(session, "owner", "editor")
        shared = await _workspace(session, "Household", owner=owner, editor=editor)
        ids = (shared.id, editor.id)
        await session.commit()

        decided, finish = asyncio.Event(), asyncio.Event()
        decide = user_removal_service._workspaces_to_delete

        async def paused(*args):
            found = await decide(*args)
            decided.set()
            await finish.wait()
            return found

        monkeypatch.setattr(user_removal_service, "_workspaces_to_delete", paused)

        async def promote():
            async with postgres_sessions() as other:
                await workspace_service.update_member_role(other, *ids, "owner")
                await other.commit()

        deleting = asyncio.create_task(
            admin_service.delete_user(session, ids[1], admin.id, reason=REASON)
        )
        await asyncio.wait_for(decided.wait(), 5)
        promoting = asyncio.create_task(promote())
        await asyncio.wait([promoting], timeout=0.5)
        finish.set()
        assert await deleting
        with pytest.raises(HTTPException, match="Member not found"):
            await promoting


@pytest.mark.parametrize("wrote", [False, True])
async def test_parallel_deletions_delete_once(postgres_sessions, wrote):
    async with postgres_sessions() as session:
        admin, owner, editor = await _people(session, "owner", "editor")
        shared = await _workspace(session, "Household", owner=owner, editor=editor)
        if wrote:
            await _fill(session, shared, editor)
        admin_id, editor_id = admin.id, editor.id
        await session.commit()

    async def remove():
        async with postgres_sessions() as other:
            return await admin_service.delete_user(other, editor_id, admin_id, reason=REASON)

    results = await asyncio.gather(*(remove() for _ in range(6)), return_exceptions=True)
    assert sorted(results, key=str) == [False] * 5 + [True]


def test_a_closed_row_keeps_only_its_id_and_currency():
    """A column added to users is stripped, or this says it is not."""
    user = _user("gone")
    for column in User.__table__.columns:
        if column.key in {"id", "preferences"}:
            continue
        python_type = column.type.python_type
        value = {bool: True, str: "theirs", datetime: datetime.now(timezone.utc)}[python_type]
        setattr(user, column.key, value)
    before = {column.key: getattr(user, column.key) for column in User.__table__.columns}

    user_removal_service._close(user)

    assert {key for key, value in before.items() if getattr(user, key) == value} == {"id"}
    assert user.preferences == {"currency_display": "BRL"}


async def test_an_active_account_is_never_hidden_by_its_address(postgres_sessions):
    async with postgres_sessions() as session:
        [admin] = await _people(session)
        user = _user("x", email=f"removed-{admin.id}@removed.invalid")
        await _add(session, user)

        assert await admin_service.get_user(session, user.id) is not None


def test_every_required_reference_to_a_user_is_classified():
    """A new table naming its author decides what a deletion does with it."""
    import app.agents.models  # noqa: F401  # their tables exist with the module off too

    required = {
        table.name
        for table in Base.metadata.tables.values()
        for column in table.columns
        for key in column.foreign_keys
        if key.column.table.name == "users" and not column.nullable
    }
    personal = set(user_removal_service.PERSONAL_TABLES)
    authored = set(user_removal_service.AUTHORED_TABLES)
    assert not personal & authored
    assert required == personal | authored


def test_deleting_a_workspace_reaches_every_table_of_it():
    for table in Base.metadata.tables.values():
        column = table.columns.get("workspace_id")
        for key in column.foreign_keys if column is not None else ():
            assert key.ondelete == "CASCADE", table.name
