import uuid
from datetime import date
from decimal import Decimal
from typing import Optional, cast

from sqlalchemy import CursorResult, case, select, func, update, delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payee import Payee, PayeeMapping, PayeeTaxId
from app.models.transaction import Transaction
from app.models.category import Category
from app.fiscal.registry import TaxIdKind, normalise_and_validate
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
    workspace_id: uuid.UUID,
    source: str = "sync",
) -> Payee:
    """Find a normalized workspace payee or create it.

    `source` is stamped only on rows this call creates. An existing payee is
    returned untouched, so a counterparty somebody entered by hand keeps
    saying so even after sync sees the same name — the same protection that
    already keeps sync from overwriting manual edits.

    Defaults to `sync` because that is what this function is for: the bulk
    path that turns bank descriptors into rows. The CSV importer passes
    `import` explicitly.
    """
    name = name.strip()
    if not name:
        raise ValueError("Payee name cannot be empty")

    if len(name) > 255:
        name = name[:255]
    # Mirrors the uq_payees_workspace_id_lower_name index exactly, so the
    # lookup hits the same row the unique constraint would reject.
    lookup = select(Payee).where(
        Payee.workspace_id == workspace_id,
        func.lower(func.trim(Payee.name)) == name.lower(),
    )
    result = await session.execute(lookup)
    payee = result.scalar_one_or_none()
    if payee:
        return payee

    payee = Payee(
        user_id=user_id,
        workspace_id=workspace_id,
        name=name,
        source=source,
    )
    try:
        async with session.begin_nested():
            session.add(payee)
            await session.flush()
        return payee
    except IntegrityError:
        # Another sync/import created it after our lookup. The savepoint keeps
        # the caller's transaction usable; return the winner instead.
        result = await session.execute(lookup)
        existing = result.scalar_one_or_none()
        if existing:
            return existing
        raise


async def _apply_tax_ids(
    session: AsyncSession,
    payee: Payee,
    workspace_id: uuid.UUID,
    incoming: list,
) -> None:
    """Replace this payee's fiscal documents with `incoming`.

    Replace rather than merge: the caller sends the set that should remain,
    which makes removing a document the same operation as changing one and
    leaves no way to end up with a stale row nobody meant to keep.

    Validation is by document kind and deliberately not by the workspace's
    jurisdiction. A Brazilian consultancy billing a Berlin client stores a
    German VAT number, and it is checked as a VAT number.
    """
    normalised: dict[TaxIdKind, str] = {}
    for item in incoming:
        kind = TaxIdKind(item.kind)
        value, error = normalise_and_validate(kind, item.value)
        # An emptied field means "drop this document", not "store nothing".
        if error == "empty":
            continue
        if error:
            raise ValueError(f"invalid_tax_id:{kind.value}:{error}")
        # One document per kind, enforced by a unique constraint. Keeping the
        # last silently would discard the caller's earlier value: two CNPJs on
        # one counterparty is a mistake worth reporting, not resolving.
        if kind in normalised:
            raise ValueError(f"duplicate_tax_id:{kind.value}")
        normalised[kind] = value

    # Queried rather than read off `payee.tax_ids`: a freshly flushed payee
    # would lazy-load the collection, and lazy IO inside an async session is
    # exactly the MissingGreenlet this codebase must not hand a user.
    rows = await session.execute(
        select(PayeeTaxId).where(PayeeTaxId.payee_id == payee.id)
    )
    existing = {row.kind: row for row in rows.scalars().all()}
    for kind_value, row in existing.items():
        if TaxIdKind(kind_value) not in normalised:
            await session.delete(row)
    for kind, value in normalised.items():
        row = existing.get(kind.value)
        if row is None:
            session.add(
                PayeeTaxId(
                    payee_id=payee.id,
                    workspace_id=workspace_id,
                    kind=kind.value,
                    value=value,
                )
            )
        elif row.value != value:
            row.value = value
    await session.flush()


async def create_payee(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    data: PayeeCreate,
) -> Payee:
    name = data.name.strip()
    if not name:
        raise ValueError("Payee name cannot be empty")

    # Check uniqueness
    existing = await session.execute(
        select(Payee).where(
            Payee.workspace_id == workspace_id,
            func.lower(func.trim(Payee.name)) == name.lower(),
        )
    )
    if existing.scalar_one_or_none():
        raise ValueError("A payee with this name already exists")

    fields = data.model_dump(exclude={"tax_ids"})
    fields["name"] = name
    # Stamped here rather than taken from the request: this is the path a
    # person went through a form to reach.
    payee = Payee(user_id=user_id, workspace_id=workspace_id, source="manual", **fields)
    session.add(payee)
    await session.flush()
    await _apply_tax_ids(session, payee, workspace_id, data.tax_ids)

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
    # Documents are handled separately: they live in their own table and are
    # replaced as a set, not assigned onto the payee row.
    tax_ids = update_data.pop("tax_ids", None)

    # Check name uniqueness if name is being changed
    if "name" in update_data:
        name = (update_data["name"] or "").strip()
        if not name:
            raise ValueError("Payee name cannot be empty")
        update_data["name"] = name
        existing = await session.execute(
            select(Payee).where(
                Payee.workspace_id == workspace_id,
                func.lower(func.trim(Payee.name)) == name.lower(),
                Payee.id != payee_id,
            )
        )
        if existing.scalar_one_or_none():
            raise ValueError("A payee with this name already exists")

    for key, value in update_data.items():
        setattr(payee, key, value)

    if tax_ids is not None:
        await _apply_tax_ids(session, payee, workspace_id, data.tax_ids or [])

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


async def bulk_delete_payees(session: AsyncSession, workspace_id: uuid.UUID, payee_ids: list[uuid.UUID]) -> int:
    # Check which payees exist in this workspace first
    payees_query = await session.execute(
        select(Payee.id).where(Payee.id.in_(payee_ids), Payee.workspace_id == workspace_id)
    )
    valid_ids = [row[0] for row in payees_query.all()]

    if not valid_ids:
        return 0

    # Null out transaction references
    await session.execute(
        update(Transaction)
        .where(Transaction.payee_id.in_(valid_ids))
        .values(payee_id=None)
    )

    # Delete mappings pointing to this payee
    await session.execute(
        delete(PayeeMapping).where(PayeeMapping.target_id.in_(valid_ids))
    )

    # Delete payees
    result = await session.execute(
        delete(Payee).where(Payee.id.in_(valid_ids))
    )
    
    await session.commit()
    return cast(CursorResult, result).rowcount


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
    reassigned = cast(CursorResult, result).rowcount

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
            func.count(Transaction.id).label("tx_count"),
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

    payee.transaction_count = row.tx_count
    return {
        "payee": payee,
        "total_spent": row.total_spent,
        "total_received": row.total_received,
        "transaction_count": row.tx_count,
        "most_common_category": most_common_category,
        "last_transaction_date": row.last_date,
    }
