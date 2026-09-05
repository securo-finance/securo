from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.core.workspace_context import WorkspaceContext, current_workspace
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
from app.schemas.export import BackupRequest
from app.services.backup_service import build_backup_archive

router = APIRouter(prefix="/api/export", tags=["export"])


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


async def _collect(ctx: WorkspaceContext, session: AsyncSession) -> dict[str, object]:
    """Every entity in the workspace, keyed by the file it becomes."""
    ws_id = ctx.workspace.id

    accounts = (await session.execute(select(Account).where(Account.workspace_id == ws_id))).scalars().all()
    transactions = (await session.execute(select(Transaction).where(Transaction.workspace_id == ws_id))).scalars().all()
    categories = (await session.execute(select(Category).where(Category.workspace_id == ws_id))).scalars().all()
    category_groups = (await session.execute(select(CategoryGroup).where(CategoryGroup.workspace_id == ws_id))).scalars().all()
    rules = (await session.execute(select(Rule).where(Rule.workspace_id == ws_id))).scalars().all()
    recurring_transactions = (await session.execute(select(RecurringTransaction).where(RecurringTransaction.workspace_id == ws_id))).scalars().all()
    budgets = (await session.execute(select(Budget).where(Budget.workspace_id == ws_id))).scalars().all()
    assets = (await session.execute(select(Asset).where(Asset.workspace_id == ws_id))).scalars().all()
    import_logs = (await session.execute(select(ImportLog).where(ImportLog.workspace_id == ws_id))).scalars().all()

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

    files: dict[str, object] = {}
    entity_counts = {}
    for name, rows in entities.items():
        serialized = [_serialize(r) for r in rows]
        entity_counts[name] = len(serialized)
        files[f"{name}.json"] = serialized

    files["metadata.json"] = {
        "export_date": datetime.now(timezone.utc).isoformat(),
        "format_version": "1.0",
        "workspace_id": str(ws_id),
        "workspace_name": ctx.workspace.name,
        "entity_counts": entity_counts,
    }
    return files


def _as_download(archive: bytes) -> StreamingResponse:
    today = date.today().isoformat()
    return StreamingResponse(
        iter([archive]),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="securo-backup-{today}.zip"'},
    )


@router.get("/backup")
async def backup(
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Export every entity in the current workspace as a JSON zip.

    Backup is scoped to one workspace at a time — users with multiple
    workspaces back each one up separately. AssetValue inherits its
    workspace from its Asset and is filtered transitively.
    """
    return _as_download(build_backup_archive(await _collect(ctx, session)))


@router.post("/backup")
async def backup_protected(
    body: BackupRequest,
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """The same archive, encrypted with AES-256 when a password is given.

    A POST because the password belongs in a body: a query string is written
    to browser history, proxy logs and server access logs. Securo never stores
    the password and cannot recover the archive without it.
    """
    password = body.password.get_secret_value() if body.password else None
    return _as_download(build_backup_archive(await _collect(ctx, session), password))
