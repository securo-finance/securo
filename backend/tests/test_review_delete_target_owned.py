"""Current-behavior positive control; future deletion is private/synthetic only."""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from fastapi_users_db_sqlalchemy.generics import GUID
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.account import Account
from app.models.transaction import Transaction
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember
from app.services import transaction_service
from app.services.admin_service import delete_user


@pytest.mark.asyncio
async def test_delete_user_with_only_target_owned_reviewed_transactions_succeeds(monkeypatch):
    """Reject deny-all guards without weakening the cross-owned provenance RED.

    No shared DB fixtures, external DSN, or mocked deletion behavior. Execution
    requires separate qualification of isolated source, imports, and environment.
    """
    original_bind = GUID.process_bind_param

    def sqlite_guid_bind(self, value, dialect):
        bound = original_bind(self, value, dialect)
        if dialect.name == "sqlite" and bound is not None:
            return uuid.UUID(bound).hex
        return bound

    # Match the original falsifier's test-local SQLite UUID serialization only;
    # keep actual application models, FK actions, review transition, and deletion.
    monkeypatch.setattr(GUID, "process_bind_param", sqlite_guid_bind)
    instant = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)

    class FixedReviewClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz) if tz is not None else instant.replace(tzinfo=None)

    def identity(label):
        return uuid.uuid5(uuid.NAMESPACE_URL, f"pr2-target-owned-control/{label}")

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    try:
        async with engine.connect() as connection:
            await connection.execute(text("PRAGMA foreign_keys=ON"))
            assert (await connection.execute(text("PRAGMA foreign_keys"))).scalar_one() == 1
            await connection.run_sync(Base.metadata.create_all)
            await connection.commit()

        async with AsyncSession(engine, expire_on_commit=False) as session:
            admin = User(
                id=identity("admin"),
                email="admin@target-owned.example",
                hashed_password="synthetic-unused-no-login",
                is_active=True,
                is_superuser=True,
                is_verified=True,
            )
            target = User(
                id=identity("target"),
                email="target@target-owned.example",
                hashed_password="synthetic-unused-no-login",
                is_active=True,
                is_superuser=False,
                is_verified=True,
            )
            session.add_all([admin, target])
            await session.flush()
            workspace = Workspace(
                id=identity("workspace"),
                name="Synthetic admin-owned workspace",
                created_by_user_id=admin.id,
                created_at=instant,
            )
            session.add(workspace)
            await session.flush()
            for actor, role in ((admin, "owner"), (target, "editor")):
                session.add(WorkspaceMember(
                    id=identity(f"{role}/membership"),
                    workspace_id=workspace.id,
                    user_id=actor.id,
                    role=role,
                    joined_at=instant,
                ))
            accounts, transactions = [], []
            for actor, label in ((target, "target"), (admin, "control")):
                account = Account(
                    id=identity(f"{label}/account"),
                    user_id=actor.id,
                    workspace_id=workspace.id,
                    name=f"Synthetic {label} account",
                    type="checking",
                    balance=Decimal("10.00"),
                    currency="USD",
                )
                session.add(account)
                await session.flush()
                transaction = Transaction(
                    id=identity(f"{label}/transaction"),
                    user_id=actor.id,
                    workspace_id=workspace.id,
                    account_id=account.id,
                    description=f"Synthetic {label} transaction",
                    amount=Decimal("10.00"),
                    currency="USD",
                    date=date(2026, 9, 26),
                    effective_date=date(2026, 9, 26),
                    type="debit",
                    source="import",
                    review_status="needs_review",
                    created_at=instant,
                )
                session.add(transaction)
                accounts.append(account)
                transactions.append(transaction)
            await session.commit()
            target_account, control_account = accounts
            target_tx, control_tx = transactions
            # Freeze only the review clock, never the transition or delete logic.
            with monkeypatch.context() as clock_patch:
                clock_patch.setattr(transaction_service, "datetime", FixedReviewClock)
                for transaction, actor in ((target_tx, target), (control_tx, admin)):
                    assert transaction.review_status == "needs_review"
                    assert transaction.reviewed_at is None
                    assert transaction.reviewed_by_id is None
                    assert await transaction_service.set_review_status(
                        session, workspace.id, transaction, "reviewed", actor.id
                    ) == 1
            await session.commit()

            # Core reads avoid stale ORM identity-map results after bulk deletion.
            async def snapshot():
                state = {}
                for model in (User, Account, Transaction, Workspace, WorkspaceMember):
                    rows = (await session.execute(
                        select(model.__table__).order_by(model.__table__.c.id)
                    )).mappings().all()
                    state[model.__tablename__] = {row["id"]: dict(row) for row in rows}
                return state

            admin_id, target_id = admin.id, target.id
            target_account_id, control_account_id = target_account.id, control_account.id
            target_tx_id, control_tx_id = target_tx.id, control_tx.id
            before = await snapshot()
            assert set(before["users"]) == {admin_id, target_id}
            assert set(before["accounts"]) == {target_account_id, control_account_id}
            assert set(before["transactions"]) == {target_tx_id, control_tx_id}
            assert set(before["workspaces"]) == {workspace.id}
            assert before["workspaces"][workspace.id]["created_by_user_id"] == admin_id
            assert set(before["workspace_members"]) == {
                identity("owner/membership"), identity("editor/membership")
            }
            for tx_id, account_id, actor_id in (
                (target_tx_id, target_account_id, target_id),
                (control_tx_id, control_account_id, admin_id),
            ):
                row = before["transactions"][tx_id]
                assert row["user_id"] == actor_id
                assert row["workspace_id"] == workspace.id
                assert row["account_id"] == account_id
                assert before["accounts"][account_id]["user_id"] == actor_id
                assert before["accounts"][account_id]["workspace_id"] == workspace.id
                assert row["review_status"] == "reviewed"
                assert row["reviewed_at"] is not None
                # SQLite may return naive timestamps; compare fixed UTC components.
                assert row["reviewed_at"].replace(tzinfo=timezone.utc) == instant
                assert row["reviewed_by_id"] == actor_id
            assert {
                row["id"] for row in before["transactions"].values()
                if row["reviewed_by_id"] == target_id
            } == {target_tx_id}, "Target review provenance must exist and be target-owned"
            assert (await session.execute(text("PRAGMA foreign_keys"))).scalar_one() == 1
            assert (await session.execute(text("PRAGMA foreign_key_check"))).all() == []

            # Intentionally no exception interception: refusal is a control failure.
            assert await delete_user(session, target_id, admin_id) is True

            after = await snapshot()
            assert after["users"] == {admin_id: before["users"][admin_id]}
            assert after["accounts"] == {
                control_account_id: before["accounts"][control_account_id]
            }
            assert after["transactions"] == {
                control_tx_id: before["transactions"][control_tx_id]
            }
            assert after["workspaces"] == before["workspaces"]
            assert after["workspace_members"] == {
                identity("owner/membership"):
                    before["workspace_members"][identity("owner/membership")]
            }
            assert (await session.execute(text("PRAGMA foreign_keys"))).scalar_one() == 1
            assert (await session.execute(text("PRAGMA foreign_key_check"))).all() == []
    finally:
        await engine.dispose()
