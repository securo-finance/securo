import io
import json
import zipfile
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import current_active_user
from app.core.database import get_async_session
from app.models.account import Account
from app.models.asset import Asset
from app.models.asset_group import AssetGroup
from app.models.asset_value import AssetValue
from app.models.budget import Budget
from app.models.category import Category
from app.models.category_group import CategoryGroup
from app.models.goal import Goal
from app.models.import_log import ImportLog
from app.models.payee import Payee, PayeeMapping
from app.models.recurring_transaction import RecurringTransaction
from app.models.bank_connection import BankConnection
from app.models.rule import Rule
from app.models.transaction import Transaction
from app.models.user import User

router = APIRouter(prefix="/api/export", tags=["export"])


from app.core.utils import serialize_model, deserialize_row


@router.get("/backup")
async def backup(
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    user_id = user.id

    # Query all entities for the current user
    accounts = (
        (await session.execute(select(Account).where(Account.user_id == user_id))).scalars().all()
    )
    transactions = (
        (await session.execute(select(Transaction).where(Transaction.user_id == user_id)))
        .scalars()
        .all()
    )
    categories = (
        (await session.execute(select(Category).where(Category.user_id == user_id))).scalars().all()
    )
    category_groups = (
        (await session.execute(select(CategoryGroup).where(CategoryGroup.user_id == user_id)))
        .scalars()
        .all()
    )
    rules = (await session.execute(select(Rule).where(Rule.user_id == user_id))).scalars().all()
    recurring_transactions = (
        (
            await session.execute(
                select(RecurringTransaction).where(RecurringTransaction.user_id == user_id)
            )
        )
        .scalars()
        .all()
    )
    budgets = (
        (await session.execute(select(Budget).where(Budget.user_id == user_id))).scalars().all()
    )
    assets = (await session.execute(select(Asset).where(Asset.user_id == user_id))).scalars().all()
    import_logs = (
        (await session.execute(select(ImportLog).where(ImportLog.user_id == user_id)))
        .scalars()
        .all()
    )
    asset_groups = (
        (await session.execute(select(AssetGroup).where(AssetGroup.user_id == user_id)))
        .scalars()
        .all()
    )
    goals = (await session.execute(select(Goal).where(Goal.user_id == user_id))).scalars().all()
    payees = (await session.execute(select(Payee).where(Payee.user_id == user_id))).scalars().all()
    payee_mappings = (
        (await session.execute(select(PayeeMapping).where(PayeeMapping.user_id == user_id)))
        .scalars()
        .all()
    )

    # AssetValue lacks user_id — filter via asset_ids
    asset_ids = [a.id for a in assets]
    if asset_ids:
        asset_values = (
            (await session.execute(select(AssetValue).where(AssetValue.asset_id.in_(asset_ids))))
            .scalars()
            .all()
        )
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
        "asset_groups": asset_groups,
        "goals": goals,
        "payees": payees,
        "payee_mappings": payee_mappings,
        "asset_values": asset_values,
        "import_logs": import_logs,
    }

    # Build in-memory ZIP
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        entity_counts = {}
        for name, rows in entities.items():
            serialized = [serialize_model(r) for r in rows]
            entity_counts[name] = len(serialized)
            zf.writestr(f"{name}.json", json.dumps(serialized, indent=2, ensure_ascii=False))

        metadata = {
            "export_date": datetime.utcnow().isoformat(),
            "format_version": "1.0",
            "entity_counts": entity_counts,
        }
        zf.writestr("metadata.json", json.dumps(metadata, indent=2, ensure_ascii=False))

    buf.seek(0)
    today = date.today().isoformat()
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="securo-backup-{today}.zip"'},
    )


@router.post("/restore")
async def restore(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    try:
        content = await file.read()
        buf = io.BytesIO(content)
        data_map = {}
        with zipfile.ZipFile(buf, "r") as zf:
            for name in zf.namelist():
                if name.endswith(".json") and name != "metadata.json":
                    entity_name = name[:-5]
                    data_map[entity_name] = json.loads(zf.read(name))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid backup file: {str(e)}")

    user_id = user.id

    try:
        entity_order = [
            ("category_groups", CategoryGroup),
            ("categories", Category),
            ("accounts", Account),
            ("payees", Payee),
            ("payee_mappings", PayeeMapping),
            ("asset_groups", AssetGroup),
            ("assets", Asset),
            ("asset_values", AssetValue),
            ("transactions", Transaction),
            ("recurring_transactions", RecurringTransaction),
            ("rules", Rule),
            ("budgets", Budget),
            ("goals", Goal),
            ("import_logs", ImportLog),
        ]

        # 1. Delete existing data (children first, reverse dependency order)
        for key, model in reversed(entity_order):
            if key in data_map:
                if model == AssetValue:
                    # Asset values are tied to assets via ids, not user_id directly
                    asset_ids_result = (
                        (await session.execute(select(Asset.id).where(Asset.user_id == user_id)))
                        .scalars()
                        .all()
                    )
                    if asset_ids_result:
                        await session.execute(
                            delete(AssetValue).where(AssetValue.asset_id.in_(asset_ids_result))
                        )
                elif hasattr(model, "user_id"):
                    await session.execute(delete(model).where(model.user_id == user_id))

        await session.flush()

        # 2. Insert new data (parents first)
        existing_payees = set()
        if "payees" not in data_map:
            existing_payees = set(
                str(id_val)
                for id_val in (
                    await session.execute(select(Payee.id).where(Payee.user_id == user_id))
                )
                .scalars()
                .all()
            )

        existing_asset_groups = set()
        if "asset_groups" not in data_map:
            existing_asset_groups = set(
                str(id_val)
                for id_val in (
                    await session.execute(
                        select(AssetGroup.id).where(AssetGroup.user_id == user_id)
                    )
                )
                .scalars()
                .all()
            )

        existing_connections = set(
            str(id_val)
            for id_val in (
                await session.execute(
                    select(BankConnection.id).where(BankConnection.user_id == user_id)
                )
            )
            .scalars()
            .all()
        )

        for key, model in entity_order:
            if key in data_map and data_map[key]:
                items = []
                for row in data_map[key]:
                    # Convert datetimes, uuids and decimals back utilizing the central util function
                    clean_row = deserialize_row(model, row)

                    # Map imported data to the currently authenticated user
                    if "user_id" in row:
                        clean_row["user_id"] = user_id
                    # Clean missing foreign keys for old backups
                    if model == Transaction and "payees" not in data_map:
                        if (
                            clean_row.get("payee_id")
                            and str(clean_row["payee_id"]) not in existing_payees
                        ):
                            clean_row["payee_id"] = None

                    if model == Asset and "asset_groups" not in data_map:
                        if (
                            clean_row.get("group_id")
                            and str(clean_row["group_id"]) not in existing_asset_groups
                        ):
                            clean_row["group_id"] = None

                    # Clean up missing connection_ids across all models
                    if hasattr(model, "connection_id") or "connection_id" in row:
                        if (
                            clean_row.get("connection_id")
                            and str(clean_row["connection_id"]) not in existing_connections
                        ):
                            clean_row["connection_id"] = None

                    items.append(model(**clean_row))

                if items:
                    session.add_all(items)
                    await session.flush()

        await session.commit()
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to restore backup: {str(e)}")

    return {"status": "success"}
