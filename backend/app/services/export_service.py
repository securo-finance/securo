import io
import json
import zipfile
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase

from app.core.utils import deserialize_row, serialize_model
from app.models.account import Account
from app.models.asset import Asset
from app.models.asset_group import AssetGroup
from app.models.asset_value import AssetValue
from app.models.bank_connection import BankConnection
from app.models.budget import Budget
from app.models.category import Category
from app.models.category_group import CategoryGroup
from app.models.goal import Goal
from app.models.import_log import ImportLog
from app.models.payee import Payee, PayeeMapping
from app.models.recurring_transaction import RecurringTransaction
from app.models.rule import Rule
from app.models.transaction import Transaction

# Order is crucial: children must be deleted before parents,
# and parents must be inserted before children.
# This list is in INSERT order (parents first).
EXPORT_MODELS_ORDER: list[tuple[str, type[DeclarativeBase]]] = [
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


class BackupArchiveHandler:
    """Manages the packaging and unpacking of Backup ZIP archives."""

    EXPORT_FORMAT_VERSION = "1.0"
    JSON_EXT = ".json"

    @classmethod
    def create_zip_archive(cls, entities: dict[str, list[DeclarativeBase]]) -> io.BytesIO:
        """Packages entities into a single compressed ZIP archive with a metadata index."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            entity_counts: dict[str, int] = {}
            for name, rows in entities.items():
                serialized = [serialize_model(r) for r in rows]
                entity_counts[name] = len(serialized)
                zf.writestr(f"{name}{cls.JSON_EXT}", json.dumps(serialized, indent=2, ensure_ascii=False))

            metadata = {
                "export_date": datetime.utcnow().isoformat(),
                "format_version": cls.EXPORT_FORMAT_VERSION,
                "entity_counts": entity_counts,
            }
            zf.writestr(f"metadata{cls.JSON_EXT}", json.dumps(metadata, indent=2, ensure_ascii=False))

        buf.seek(0)
        return buf

    @classmethod
    def parse_import_zip(cls, content: bytes) -> dict[str, list[dict[str, Any]]]:
        """Reads a zip archive payload and evaluates strictly valid json entities."""
        buf = io.BytesIO(content)
        data_map: dict[str, list[dict[str, Any]]] = {}
        with zipfile.ZipFile(buf, "r") as zf:
            for name in zf.namelist():
                if name.endswith(cls.JSON_EXT) and name != f"metadata{cls.JSON_EXT}":
                    entity_name = name[: -len(cls.JSON_EXT)]
                    data_map[entity_name] = json.loads(zf.read(name))
        return data_map


async def export_user_data(session: AsyncSession, user_id: UUID) -> io.BytesIO:
    """Extracts all user-related data from the database into structured zip bytes."""
    entities: dict[str, list[DeclarativeBase]] = {}

    for key, model in EXPORT_MODELS_ORDER:
        if model == AssetValue:
            asset_ids = [a.id for a in entities.get("assets", [])]
            if asset_ids:
                entities[key] = (
                    (await session.execute(select(AssetValue).where(AssetValue.asset_id.in_(asset_ids))))
                    .scalars()
                    .all()
                )
            else:
                entities[key] = []
        else:
            entities[key] = (
                (await session.execute(select(model).where(model.user_id == user_id)))
                .scalars()
                .all()
            )

    return BackupArchiveHandler.create_zip_archive(entities)


async def _get_existing_foreign_keys(session: AsyncSession, user_id: UUID, data_map: dict[str, list[dict[str, Any]]]) -> dict[str, set[str]]:
    existing_payees: set[str] = set()
    is_missing_payees = "payees" not in data_map
    if is_missing_payees:
        payee_ids = await session.execute(select(Payee.id).where(Payee.user_id == user_id))
        existing_payees = {str(pid) for pid in payee_ids.scalars().all()}

    existing_asset_groups: set[str] = set()
    is_missing_asset_groups = "asset_groups" not in data_map
    if is_missing_asset_groups:
        group_ids = await session.execute(select(AssetGroup.id).where(AssetGroup.user_id == user_id))
        existing_asset_groups = {str(gid) for gid in group_ids.scalars().all()}

    connection_ids = await session.execute(select(BankConnection.id).where(BankConnection.user_id == user_id))
    existing_connections: set[str] = {str(cid) for cid in connection_ids.scalars().all()}

    return {
        "payees": existing_payees,
        "asset_groups": existing_asset_groups,
        "connections": existing_connections,
    }


class RestoreContext:
    def __init__(self, user_id: UUID, data_map: dict[str, list[dict[str, Any]]], existing_keys: dict[str, set[str]]):
        self.user_id = user_id
        self.data_map = data_map
        self.existing_keys = existing_keys
        
        self.restoring_without_payees = "payees" not in data_map
        self.restoring_without_asset_groups = "asset_groups" not in data_map

    def sanitize_row(self, model: type[DeclarativeBase], row: dict[str, Any]) -> dict[str, Any]:
        clean_row = deserialize_row(model, row)

        has_user_id = "user_id" in row
        if has_user_id:
            clean_row["user_id"] = self.user_id

        is_transaction_model = model == Transaction
        if is_transaction_model and self.restoring_without_payees:
            payee_uuid = clean_row.get("payee_id")
            is_unresolved_payee = payee_uuid and str(payee_uuid) not in self.existing_keys["payees"]
            if is_unresolved_payee:
                clean_row["payee_id"] = None

        is_asset_model = model == Asset
        if is_asset_model and self.restoring_without_asset_groups:
            group_uuid = clean_row.get("group_id")
            is_unresolved_group = group_uuid and str(group_uuid) not in self.existing_keys["asset_groups"]
            if is_unresolved_group:
                clean_row["group_id"] = None

        has_connection_id = hasattr(model, "connection_id") or "connection_id" in row
        if has_connection_id:
            connection_uuid = clean_row.get("connection_id")
            is_unresolved_connection = connection_uuid and str(connection_uuid) not in self.existing_keys["connections"]
            if is_unresolved_connection:
                clean_row["connection_id"] = None

        return clean_row


async def _insert_new_user_data(session: AsyncSession, user_id: UUID, data_map: dict[str, list[dict[str, Any]]], existing_keys: dict[str, set[str]]):
    context = RestoreContext(user_id, data_map, existing_keys)

    for key, model in EXPORT_MODELS_ORDER:
        requires_insertion = key in data_map and data_map[key]
        if not requires_insertion:
            continue

        for row in data_map[key]:
            clean_row = context.sanitize_row(model, row)
            await session.merge(model(**clean_row))

        await session.flush()


async def restore_user_data(session: AsyncSession, user_id: UUID, data_map: dict[str, list[dict[str, Any]]]):
    existing_keys = await _get_existing_foreign_keys(session, user_id, data_map)
    await _insert_new_user_data(session, user_id, data_map, existing_keys)
