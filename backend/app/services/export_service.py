import io
import json
import zipfile
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.dialects.postgresql import insert as pg_insert

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

# IMPORTANT: The order matters. Parents must be inserted before children.
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
                serialized = []
                for r in rows:
                    d = serialize_model(r)
                    # Remove connection_id de Account, Asset e AssetGroup
                    if name in {"accounts", "assets", "asset_groups"}:
                        d.pop("connection_id", None)
                    serialized.append(d)
                entity_counts[name] = len(serialized)
                zf.writestr(
                    f"{name}{cls.JSON_EXT}",
                    json.dumps(serialized, indent=2, ensure_ascii=False),
                )

            metadata = {
                "export_date": datetime.utcnow().isoformat(),
                "format_version": cls.EXPORT_FORMAT_VERSION,
                "entity_counts": entity_counts,
            }
            zf.writestr(
                f"metadata{cls.JSON_EXT}",
                json.dumps(metadata, indent=2, ensure_ascii=False),
            )

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


class ExportService:
    """Service layer for securely exporting and incrementally merging (upserting) native User database models."""

    def __init__(self, session: AsyncSession, user_id: UUID):
        self.session = session
        self.user_id = user_id

    async def export_data(self) -> io.BytesIO:
        """Extracts all user-related data from the database into structured zip bytes."""
        entities: dict[str, list[DeclarativeBase]] = {}

        for key, model in EXPORT_MODELS_ORDER:
            if model == AssetValue:
                asset_ids = [a.id for a in entities.get("assets", [])]
                if asset_ids:
                    entities[key] = (
                        (
                            await self.session.execute(
                                select(AssetValue).where(AssetValue.asset_id.in_(asset_ids))
                            )
                        )
                        .scalars()
                        .all()
                    )
                else:
                    entities[key] = []
            else:
                entities[key] = (
                    (await self.session.execute(select(model).where(model.user_id == self.user_id)))
                    .scalars()
                    .all()
                )

        return BackupArchiveHandler.create_zip_archive(entities)

    async def _get_existing_foreign_keys(
        self, data_map: dict[str, list[dict[str, Any]]]
    ) -> dict[str, set[str]]:
        # Busca todos os IDs válidos do usuário para cada FK relevante
        async def get_ids(model):
            ids = await self.session.execute(select(model.id).where(model.user_id == self.user_id))
            return {str(i) for i in ids.scalars().all()}

        payees = await get_ids(Payee)
        asset_groups = await get_ids(AssetGroup)
        category_groups = await get_ids(CategoryGroup)
        accounts = await get_ids(Account)
        categories = await get_ids(Category)
        assets = await get_ids(Asset)
        bank_connections = await get_ids(BankConnection)

        return {
            "payees": payees,
            "asset_groups": asset_groups,
            "category_groups": category_groups,
            "accounts": accounts,
            "categories": categories,
            "assets": assets,
            "connections": bank_connections,
        }

    def _sanitize_row(
        self,
        model: type[DeclarativeBase],
        row: dict[str, Any],
        data_map: dict[str, list[dict[str, Any]]],
        existing_keys: dict[str, set[str]],
    ) -> dict[str, Any]:
        clean_row = deserialize_row(model, row)
        # Always overwrite user_id to ensure data consistency
        if "user_id" in row:
            clean_row["user_id"] = self.user_id

        # MAINTENANCE: If you add new models or foreign key fields, update this map to ensure all FKs are validated for user ownership.
        # This is critical for multi-user security and data integrity in backup/restore.
        fk_map = {
            "account_id": "accounts",
            "category_id": "categories",
            "asset_id": "assets",
            "group_id": "asset_groups" if model.__name__ == "Asset" else "category_groups",
            "payee_id": "payees",
            "connection_id": "connections",
        }
        for fk_field, key_set in fk_map.items():
            if fk_field in clean_row and clean_row[fk_field] is not None:
                if fk_field == "group_id" and model.__name__ != "Asset":
                    key_set = "category_groups"
                if str(clean_row[fk_field]) not in existing_keys.get(key_set, set()):
                    clean_row[fk_field] = None

        return clean_row

    async def _insert_new_user_data(
        self,
        data_map: dict[str, list[dict[str, Any]]],
        existing_keys: dict[str, set[str]],
    ):
        for key, model in EXPORT_MODELS_ORDER:
            if key not in data_map or not data_map[key]:
                continue

            pk_columns = [col.name for col in model.__table__.primary_key.columns]
            if not pk_columns:
                continue

            stmt = (
                pg_insert(model)
                .values(
                    [
                        self._sanitize_row(model, row, data_map, existing_keys)
                        for row in data_map[key]
                    ]
                )
                .on_conflict_do_nothing(index_elements=pk_columns)
            )
            await self.session.execute(stmt)

        await self.session.flush()

    async def restore_data(self, data_map: dict[str, list[dict[str, Any]]]):
        existing_keys = await self._get_existing_foreign_keys(data_map)
        await self._insert_new_user_data(data_map, existing_keys)
