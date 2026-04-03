import io
import json
import zipfile
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import current_active_user
from app.core.database import get_async_session
from app.models.account import Account
from app.models.asset import Asset
from app.models.asset_value import AssetValue
from app.models.budget import Budget
from app.models.category import Category
from app.models.category_group import CategoryGroup
from app.models.import_log import ImportLog
from app.models.recurring_transaction import RecurringTransaction
from app.models.rule import Rule
from app.models.transaction import Transaction
from app.models.user import User

router = APIRouter(prefix="/api/export", tags=["export"])
EXPORT_FORMAT_VERSION = "2.0"


def _serialize(obj) -> dict:
    """Convert a SQLAlchemy model instance to a JSON-serializable dict."""
    d = {}
    for col in obj.__table__.columns:
        val = getattr(obj, col.key)
        if isinstance(val, UUID):
            val = str(val)
        elif isinstance(val, (datetime, date)):
            val = val.isoformat()
        elif isinstance(val, Decimal):
            val = str(val)
        d[col.key] = val
    return d


@router.get("/backup")
async def backup(
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    user_id = user.id

    # Query all entities for the current user
    accounts = (await session.execute(select(Account).where(Account.user_id == user_id))).scalars().all()
    transactions = (await session.execute(select(Transaction).where(Transaction.user_id == user_id))).scalars().all()
    categories = (await session.execute(select(Category).where(Category.user_id == user_id))).scalars().all()
    category_groups = (await session.execute(select(CategoryGroup).where(CategoryGroup.user_id == user_id))).scalars().all()
    rules = (await session.execute(select(Rule).where(Rule.user_id == user_id))).scalars().all()
    recurring_transactions = (await session.execute(select(RecurringTransaction).where(RecurringTransaction.user_id == user_id))).scalars().all()
    budgets = (await session.execute(select(Budget).where(Budget.user_id == user_id))).scalars().all()
    assets = (await session.execute(select(Asset).where(Asset.user_id == user_id))).scalars().all()
    import_logs = (await session.execute(select(ImportLog).where(ImportLog.user_id == user_id))).scalars().all()

    # AssetValue lacks user_id — filter via asset_ids
    asset_ids = [a.id for a in assets]
    if asset_ids:
        asset_values = (await session.execute(select(AssetValue).where(AssetValue.asset_id.in_(asset_ids)))).scalars().all()
    else:
        asset_values = []

    entities = {
        "accounts": accounts,
        "transactions": transactions,
        "categories": categories,
        "category_groups": category_groups,
        "rules": rules,
        "recurring_transactions": recurring_transactions,
        "budgets": budgets,
        "assets": assets,
        "asset_values": asset_values,
        "import_logs": import_logs,
    }

    # Build in-memory ZIP
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        entity_counts = {}
        for name, rows in entities.items():
            serialized = [_serialize(r) for r in rows]
            entity_counts[name] = len(serialized)
            zf.writestr(f"{name}.json", json.dumps(serialized, indent=2, ensure_ascii=False))

        metadata = {
            "export_date": datetime.now(UTC).isoformat(),
            "format_version": EXPORT_FORMAT_VERSION,
            "compatibility": {
                "legacy_category_groups_included": True,
                "category_budget_source": "categories",
                "notes": "v2 readers use category-owned budgets; legacy category groups remain in the backup for recovery during transition.",
            },
            "entity_counts": entity_counts,
        }
        zf.writestr("metadata.json", json.dumps(metadata, indent=2, ensure_ascii=False))

    buf.seek(0)
    today = date.today().isoformat()
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="flux-backup-{today}.zip"'},
    )
