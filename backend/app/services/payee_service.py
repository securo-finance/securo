import uuid
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import case, select, func, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payee import Payee, PayeeMapping
from app.models.transaction import Transaction
from app.models.category import Category
from app.schemas.payee import PayeeCreate, PayeeUpdate


async def get_payees(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    q: Optional[str] = None,
    type: Optional[str] = None,
    is_favorite: Optional[bool] = None,
) -> list[Payee]:
    """List all payees in a workspace with transaction counts."""
    count_subq = (
        select(
            Transaction.payee_id,
            func.count(Transaction.id).label("tx_count"),
        )
        .where(Transaction.payee_id.isnot(None))
        .group_by(Transaction.payee_id)
        .subquery()
    )
    tx_count = func.coalesce(count_subq.c.tx_count, 0)
    stmt = (
        select(Payee, tx_count.label("transaction_count"))
        .outerjoin(count_subq, Payee.id == count_subq.c.payee_id)
        .where(Payee.workspace_id == workspace_id)
    )

    if q:
        from sqlalchemy import or_
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(or_(Payee.name.ilike(pattern), Payee.notes.ilike(pattern)))

    if type:
        stmt = stmt.where(Payee.type == type)

    if is_favorite is not None:
        stmt = stmt.where(Payee.is_favorite == is_favorite)

    stmt = stmt.order_by(Payee.name)
    result = await session.execute(stmt)
    payees = []
    for row in result.all():
        payee = row[0]
        payee.transaction_count = row[1]
        payees.append(payee)
    return payees


async def get_payee(session: AsyncSession, payee_id: uuid.UUID, workspace_id: uuid.UUID) -> Optional[Payee]:
    result = await session.execute(
        select(Payee).where(Payee.id == payee_id, Payee.workspace_id == workspace_id)
    )
    return result.scalar_one_or_none()


async def get_or_create_payee(
    session: AsyncSession,
    user_id: uuid.UUID,
    name: str,
    *,
    workspace_id: Optional[uuid.UUID] = None,
) -> Payee:
    """Find a payee by name (case-insensitive) or create a new one.

    `user_id` is kept first for backwards compatibility with import/connection
    sync paths. When `workspace_id` is provided, the lookup scopes by workspace;
    otherwise the autostamp listener fills it in on insert.
    """
    name = name.strip()
    if not name:
        raise ValueError("Payee name cannot be empty")

    lookup = select(Payee).where(func.lower(Payee.name) == name.lower())
    if workspace_id is not None:
        lookup = lookup.where(Payee.workspace_id == workspace_id)
    else:
        lookup = lookup.where(Payee.user_id == user_id)
    result = await session.execute(lookup)
    payee = result.scalar_one_or_none()
    if payee:
        return payee

    payee = Payee(user_id=user_id, name=name)
    if workspace_id is not None:
        payee.workspace_id = workspace_id
    session.add(payee)
    await session.flush()
    return payee


async def create_payee(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    data: PayeeCreate,
) -> Payee:
    # Check uniqueness
    existing = await session.execute(
        select(Payee).where(Payee.workspace_id == workspace_id, func.lower(Payee.name) == data.name.strip().lower())
    )
    if existing.scalar_one_or_none():
        raise ValueError("A payee with this name already exists")

    payee = Payee(user_id=user_id, workspace_id=workspace_id, **data.model_dump())
    session.add(payee)
    await session.flush()

    # Self-mapping for merge tracking
    mapping = PayeeMapping(id=payee.id, user_id=user_id, workspace_id=workspace_id, target_id=payee.id)
    session.add(mapping)

    await session.commit()
    await session.refresh(payee)
    payee.transaction_count = 0
    return payee


async def update_payee(
    session: AsyncSession, payee_id: uuid.UUID, workspace_id: uuid.UUID, data: PayeeUpdate
) -> Optional[Payee]:
    payee = await get_payee(session, payee_id, workspace_id)
    if not payee:
        return None

    update_data = data.model_dump(exclude_unset=True)

    # Check name uniqueness if name is being changed
    if "name" in update_data and update_data["name"]:
        existing = await session.execute(
            select(Payee).where(
                Payee.workspace_id == workspace_id,
                func.lower(Payee.name) == update_data["name"].strip().lower(),
                Payee.id != payee_id,
            )
        )
        if existing.scalar_one_or_none():
            raise ValueError("A payee with this name already exists")

    for key, value in update_data.items():
        setattr(payee, key, value)

    await session.commit()
    await session.refresh(payee)
    return payee


async def delete_payee(session: AsyncSession, payee_id: uuid.UUID, workspace_id: uuid.UUID) -> bool:
    payee = await get_payee(session, payee_id, workspace_id)
    if not payee:
        return False

    # Null out transaction references
    await session.execute(
        update(Transaction)
        .where(Transaction.payee_id == payee_id)
        .values(payee_id=None)
    )

    # Delete mappings pointing to this payee
    await session.execute(
        delete(PayeeMapping).where(PayeeMapping.target_id == payee_id)
    )

    await session.delete(payee)
    await session.commit()
    return True


async def merge_payees(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    target_id: uuid.UUID,
    source_ids: list[uuid.UUID],
) -> int:
    """Merge source payees into target. Returns number of transactions reassigned."""
    # Validate target
    target = await get_payee(session, target_id, workspace_id)
    if not target:
        raise ValueError("Target payee not found")

    # Validate sources
    for source_id in source_ids:
        if source_id == target_id:
            continue
        source = await get_payee(session, source_id, workspace_id)
        if not source:
            raise ValueError(f"Source payee {source_id} not found")

    # Reassign transactions
    result = await session.execute(
        update(Transaction)
        .where(Transaction.payee_id.in_(source_ids))
        .values(payee_id=target_id)
    )
    reassigned = result.rowcount

    # Update mappings: point source mappings to target
    for source_id in source_ids:
        if source_id == target_id:
            continue
        await session.execute(
            update(PayeeMapping)
            .where(PayeeMapping.target_id == source_id)
            .values(target_id=target_id)
        )

    # Delete source payees
    for source_id in source_ids:
        if source_id == target_id:
            continue
        source = await get_payee(session, source_id, workspace_id)
        if source:
            await session.delete(source)

    await session.commit()
    return reassigned


async def get_payee_summary(
    session: AsyncSession,
    payee_id: uuid.UUID,
    workspace_id: uuid.UUID,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> dict:
    """Return spending analytics for a payee."""
    payee = await get_payee(session, payee_id, workspace_id)
    if not payee:
        raise ValueError("Payee not found")

    base = select(Transaction).where(
        Transaction.payee_id == payee_id,
        Transaction.workspace_id == workspace_id,
    )
    if start_date:
        base = base.where(Transaction.date >= start_date)
    if end_date:
        base = base.where(Transaction.date <= end_date)

    # Totals
    totals = await session.execute(
        select(
            func.coalesce(func.sum(
                case(
                    (Transaction.type == "debit", Transaction.amount),
                    else_=Decimal("0"),
                )
            ), Decimal("0")).label("total_spent"),
            func.coalesce(func.sum(
                case(
                    (Transaction.type == "credit", Transaction.amount),
                    else_=Decimal("0"),
                )
            ), Decimal("0")).label("total_received"),
            func.count(Transaction.id).label("count"),
            func.max(Transaction.date).label("last_date"),
        )
        .where(
            Transaction.payee_id == payee_id,
            Transaction.workspace_id == workspace_id,
        )
    )
    row = totals.one()

    # Most common category
    cat_result = await session.execute(
        select(Transaction.category_id, func.count(Transaction.id).label("cnt"))
        .where(
            Transaction.payee_id == payee_id,
            Transaction.workspace_id == workspace_id,
            Transaction.category_id.isnot(None),
        )
        .group_by(Transaction.category_id)
        .order_by(func.count(Transaction.id).desc())
        .limit(1)
    )
    cat_row = cat_result.first()
    most_common_category = None
    if cat_row:
        cat = await session.execute(
            select(Category).where(Category.id == cat_row[0])
        )
        most_common_category = cat.scalar_one_or_none()

    payee.transaction_count = row.count
    return {
        "payee": payee,
        "total_spent": row.total_spent,
        "total_received": row.total_received,
        "transaction_count": row.count,
        "most_common_category": most_common_category,
        "last_transaction_date": row.last_date,
    }
