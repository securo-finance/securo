"""PR-2 deletion falsifier: preserve provenance without choosing a deletion policy."""

import json
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from fastapi_users_db_sqlalchemy.generics import GUID
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.account import Account
from app.models.transaction import Transaction
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember
from app.services.admin_service import delete_user
from app.services.transaction_service import set_review_status


@pytest.mark.asyncio
async def test_delete_reviewer_with_cross_owned_transaction_preserves_audit(monkeypatch):
    """Exercise real SET NULL, not a mocked delete or a hand-corrupted row.

    The engine is private to this test: enabling FK enforcement must not leak
    into the shared SQLite fixtures. A rejected deletion may preserve the audit;
    no HTTP code or block/tombstone/clear policy is prescribed here.
    """
    # fastapi-users' GUID stores hyphenated SQLite user IDs, whereas the
    # application's PostgreSQL UUID foreign keys bind hex without hyphens.
    # Normalize only this test's SQLite GUID serialization so real FK actions
    # can run; preserve UUID identity and the actual model/FK/delete behavior.
    original_bind = GUID.process_bind_param

    def sqlite_guid_bind(self, value, dialect):
        bound = original_bind(self, value, dialect)
        if dialect.name == "sqlite" and bound is not None:
            return uuid.UUID(bound).hex
        return bound

    monkeypatch.setattr(GUID, "process_bind_param", sqlite_guid_bind)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    try:
        async with engine.connect() as connection:
            await connection.execute(text("PRAGMA foreign_keys=ON"))
            assert (await connection.execute(text("PRAGMA foreign_keys"))).scalar_one() == 1
            await connection.run_sync(Base.metadata.create_all)
            await connection.commit()

        async with AsyncSession(engine, expire_on_commit=False) as session:
            owner = User(
                id=uuid.uuid5(uuid.NAMESPACE_URL, "pr2-deletion/owner"),
                email="owner@deletion.example",
                hashed_password="synthetic-unused-no-login",
                is_active=True,
                is_superuser=True,
                is_verified=True,
            )
            reviewer = User(
                id=uuid.uuid5(uuid.NAMESPACE_URL, "pr2-deletion/reviewer"),
                email="reviewer@deletion.example",
                hashed_password="synthetic-unused-no-login",
                is_active=True,
                is_superuser=False,
                is_verified=True,
            )
            session.add_all([owner, reviewer])
            await session.flush()
            shared = Workspace(
                id=uuid.uuid5(uuid.NAMESPACE_URL, "pr2-deletion/shared"),
                name="Synthetic shared workspace",
                created_by_user_id=owner.id,
            )
            private = Workspace(
                id=uuid.uuid5(uuid.NAMESPACE_URL, "pr2-deletion/private"),
                name="Synthetic other tenant control",
                created_by_user_id=owner.id,
            )
            session.add_all([shared, private])
            await session.flush()
            session.add_all([
                WorkspaceMember(workspace_id=shared.id, user_id=owner.id, role="owner"),
                WorkspaceMember(workspace_id=shared.id, user_id=reviewer.id, role="editor"),
                WorkspaceMember(workspace_id=private.id, user_id=owner.id, role="owner"),
            ])
            transactions = []
            for workspace, label in ((shared, "cross-owned"), (private, "control")):
                account = Account(
                    id=uuid.uuid5(uuid.NAMESPACE_URL, f"pr2-deletion/{label}/account"),
                    user_id=owner.id,
                    workspace_id=workspace.id,
                    name=f"Synthetic {label} account",
                    type="checking",
                    balance=Decimal("10.00"),
                    currency="USD",
                )
                session.add(account)
                await session.flush()
                transaction = Transaction(
                    id=uuid.uuid5(uuid.NAMESPACE_URL, f"pr2-deletion/{label}/transaction"),
                    user_id=owner.id,
                    workspace_id=workspace.id,
                    account_id=account.id,
                    description=f"Synthetic {label} transaction",
                    amount=Decimal("10.00"),
                    currency="USD",
                    date=date(2026, 9, 26),
                    type="debit",
                    source="import",
                    review_status="needs_review",
                    created_at=datetime(2026, 9, 26, tzinfo=timezone.utc),
                )
                session.add(transaction)
                transactions.append(transaction)
            await session.commit()
            target, control = transactions
            assert target.user_id != reviewer.id
            assert target.workspace_id == shared.id
            assert control.workspace_id != shared.id
            for transaction, actor in ((target, reviewer), (control, owner)):
                assert await set_review_status(
                    session, transaction.workspace_id, transaction, "reviewed", actor.id
                ) == 1
            await session.commit()

            # Core table reads bypass the ORM identity map, including after DELETE.
            async def snapshot():
                rows = (await session.execute(
                    select(Transaction.__table__).order_by(Transaction.id)
                )).mappings().all()
                return {row["id"]: dict(row) for row in rows}

            before = await snapshot()
            assert before[target.id]["reviewed_at"] is not None
            assert before[target.id]["reviewed_by_id"] == reviewer.id
            assert (await session.execute(text("PRAGMA foreign_key_check"))).all() == []
            target_id, control_id, reviewer_id, owner_id = (
                target.id, control.id, reviewer.id, owner.id
            )
            outcome = None
            rejected = False
            try:
                outcome = await delete_user(session, reviewer_id, owner_id)
            except (ValueError, IntegrityError) as exc:
                rejected = True
                outcome = f"{type(exc).__name__}: {exc}"
                await session.rollback()

            after = await snapshot()
            fk_on = (await session.execute(text("PRAGMA foreign_keys"))).scalar_one()
            remaining_users = (await session.execute(select(User.id).order_by(User.id))).scalars().all()
            print(json.dumps({
                "foreign_keys": fk_on,
                "delete_outcome": outcome,
                "remaining_user_ids": [str(user_id) for user_id in remaining_users],
                "before": {str(key): value for key, value in before.items()},
                "after": {str(key): value for key, value in after.items()},
            }, default=str, sort_keys=True, indent=2))
            assert fk_on == 1
            assert (await session.execute(text("PRAGMA foreign_key_check"))).all() == []
            assert set(after) == set(before), "Deletion removed a surviving owner's transaction"
            assert after[control_id] == before[control_id], "Other tenant's row changed"
            assert owner_id in remaining_users
            if rejected:
                assert reviewer_id in remaining_users
                assert after == before, "Rejected deletion did not preserve the audit"
            preserved = after[target_id]
            assert {
                key: value for key, value in preserved.items()
                if key not in {"reviewed_at", "reviewed_by_id"}
            } == {
                key: value for key, value in before[target_id].items()
                if key not in {"reviewed_at", "reviewed_by_id"}
            }
            # Deliberately ordinary RED on the current branch: SET NULL removes
            # the actor but retains the timestamp. Do not xfail or bless that state.
            assert (preserved["reviewed_at"] is None) == (preserved["reviewed_by_id"] is None), (
                f"Reviewer deletion broke paired provenance: before={before[target_id]!r}; "
                f"after={preserved!r}; delete_outcome={outcome!r}"
            )
            assert (
                preserved["reviewed_at"], preserved["reviewed_by_id"]
            ) == (
                before[target_id]["reviewed_at"], before[target_id]["reviewed_by_id"]
            ), "Deletion lost the existing audit; a retention-policy decision is required"
    finally:
        await engine.dispose()
