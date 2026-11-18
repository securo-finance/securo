"""Packing a workspace backup into a zip, optionally encrypted.

The archive is the user's own copy of their data, so it stays a plain zip of
plain JSON by default: anything that can open a zip can read it, today and in
ten years. A password swaps the container for an AES-256 one (WinZip AES, the
scheme 7-Zip, Keka and WinRAR all speak) so the file stays openable by ordinary
tools while the contents travel safely through whatever cloud or drive the user
keeps it on.

AES rather than the legacy ZipCrypto that "password-protected zip" often means:
ZipCrypto's key is recoverable from a few known plaintext bytes, and a backup
full of JSON is nothing but known plaintext.

The archive is also the input of `restore_service`, so the two halves agree on
one map: `EXPORTED_ENTITIES` below names every table the archive carries, in
the order a restore has to insert them.
"""
import io
import json
import logging
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

import pyzipper
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.workspace_context import WorkspaceContext
from app.models.account import Account
from app.models.account_reserved_balance import AccountReservedBalance
from app.models.asset import Asset
from app.models.asset_group import AssetGroup
from app.models.asset_value import AssetValue
from app.models.budget import Budget
from app.models.category import Category
from app.models.category_group import CategoryGroup
from app.models.collection import Collection, collection_accounts, collection_asset_groups
from app.models.credit_card_bill import CreditCardBill
from app.models.goal import Goal
from app.models.group import Group, GroupMember
from app.models.group_settlement import GroupSettlement
from app.models.import_log import ImportLog
from app.models.payee import Payee, PayeeMapping, PayeeTaxId
from app.models.recurring_transaction import RecurringTransaction
from app.models.recurring_transaction_category_line import RecurringTransactionCategoryLine
from app.models.rule import Rule
from app.models.transaction import Transaction
from app.models.transaction_attachment import TransactionAttachment
from app.models.transaction_category_line import TransactionCategoryLine
from app.models.transaction_split import TransactionSplit
from app.models.workspace import Workspace, WorkspaceTaxId
from app.providers import get_storage_provider
from app.services import schema_versions

logger = logging.getLogger(__name__)

_FORMAT_VERSION = "1.1"

#: The zip encrypts entry *contents*, never the central directory, so the file
#: names and their sizes stay readable without the password. Nothing sensitive
#: goes in a name — they are the fixed entity names below plus `metadata.json`.
_COMPRESSION = zipfile.ZIP_DEFLATED

#: Folder inside the archive that holds the attachment bytes, one entry per
#: attachment id (`attachments/<id>`). The rows travel in JSON like every other
#: table; this is only the blob beside it.
ATTACHMENTS_PREFIX = "attachments/"

#: Columns pointing at a table the archive does not carry. They are written as
#: null, because a restore into a fresh instance has nothing to point at:
#: connections belong to an instance's provider setup, and institutions are a
#: shared catalogue. The account keeps its name, numbers and balance; it just
#: starts life unlinked from the bank feed.
_EXTERNAL_REFERENCES: dict[str, tuple[str, ...]] = {
    "accounts": ("connection_id", "institution_id"),
    "assets": ("connection_id",),
    "asset_groups": ("connection_id", "institution_id"),
}


@dataclass(frozen=True)
class ExportedEntity:
    """One table in the archive: its file name and the model behind it."""

    name: str
    model: type


#: Every table the archive carries. `restore_service` inserts in this order,
#: so a table always comes after the tables its foreign keys point at.
EXPORTED_ENTITIES: tuple[ExportedEntity, ...] = (
    ExportedEntity("category_groups", CategoryGroup),
    ExportedEntity("categories", Category),
    ExportedEntity("payees", Payee),
    ExportedEntity("payee_tax_ids", PayeeTaxId),
    ExportedEntity("payee_mapping", PayeeMapping),
    ExportedEntity("asset_groups", AssetGroup),
    ExportedEntity("assets", Asset),
    ExportedEntity("asset_values", AssetValue),
    ExportedEntity("accounts", Account),
    ExportedEntity("account_reserved_balances", AccountReservedBalance),
    ExportedEntity("credit_card_bills", CreditCardBill),
    ExportedEntity("import_logs", ImportLog),
    ExportedEntity("recurring_transactions", RecurringTransaction),
    ExportedEntity("recurring_transaction_category_lines", RecurringTransactionCategoryLine),
    ExportedEntity("groups", Group),
    ExportedEntity("group_members", GroupMember),
    ExportedEntity("transactions", Transaction),
    ExportedEntity("transaction_category_lines", TransactionCategoryLine),
    ExportedEntity("transaction_splits", TransactionSplit),
    ExportedEntity("transaction_attachments", TransactionAttachment),
    ExportedEntity("group_settlements", GroupSettlement),
    ExportedEntity("budgets", Budget),
    ExportedEntity("goals", Goal),
    ExportedEntity("collections", Collection),
    ExportedEntity("rules", Rule),
    ExportedEntity("workspace_tax_ids", WorkspaceTaxId),
)

#: Join tables have no model of their own; they are read and written as rows.
JOIN_TABLES = ("collection_accounts", "collection_asset_groups")
_JOIN_TABLE_OBJECTS = {
    "collection_accounts": collection_accounts,
    "collection_asset_groups": collection_asset_groups,
}


@dataclass
class ExportPayload:
    """The JSON files of a backup plus the attachment blobs beside them.

    `files` holds one JSON value per file — a list of rows for an entity, an
    object for metadata — which is why it is typed loosely: the only thing
    every caller agrees on is that it serializes.
    """

    files: dict[str, Any] = field(default_factory=dict)
    attachments: dict[str, bytes] = field(default_factory=dict)


def serialize_row(obj, blank_columns: tuple[str, ...] = ()) -> dict:
    """Convert a SQLAlchemy model instance to a JSON-serializable dict.

    Decimals become strings and dates ISO strings, so the JSON is exact and
    readable; `restore_service` coerces each value back through the column's
    own type rather than guessing from the text.
    """
    row = {}
    for column in obj.__table__.columns:
        if column.key in blank_columns:
            row[column.key] = None
            continue
        value = getattr(obj, column.key)
        if isinstance(value, uuid.UUID):
            value = str(value)
        elif isinstance(value, (datetime, date)):
            value = value.isoformat()
        elif isinstance(value, Decimal):
            value = str(value)
        row[column.key] = value
    return row


def _workspace_block(workspace: Workspace) -> dict:
    """The settings a restored workspace needs to look like the original.

    Deliberately not the whole row: identity (`id`, creator, archive flag) is
    the restore's to decide, and copying it would suggest the new workspace is
    the old one.
    """
    return {
        "name": workspace.name,
        "kind": workspace.kind,
        "default_currency": workspace.default_currency,
        "locale": workspace.locale,
        "timezone": workspace.timezone,
        "tax_jurisdiction": workspace.tax_jurisdiction,
        "legal_name": workspace.legal_name,
        "address": workspace.address,
        "icon": workspace.icon,
        "color": workspace.color,
    }


async def _rows(session: AsyncSession, entity: ExportedEntity, workspace_id: uuid.UUID) -> list:
    """One workspace's rows of one table.

    Every exported table carries `workspace_id` (ADR 0002) and
    `test_every_exported_entity_is_workspace_scoped` holds the list to it. The
    filter is not optional: without it an export would carry other people's
    books, so a table that ever loses the column has to fail loudly instead of
    being exported whole.
    """
    model = entity.model
    stmt = select(model).where(getattr(model, "workspace_id") == workspace_id)
    return list((await session.execute(stmt)).scalars().all())


async def collect_workspace_export(ctx: WorkspaceContext, session: AsyncSession) -> ExportPayload:
    """Every entity in the workspace, keyed by the file it becomes."""
    workspace = ctx.workspace
    ws_id = workspace.id
    payload = ExportPayload()
    entity_counts: dict[str, int] = {}

    for entity in EXPORTED_ENTITIES:
        rows = await _rows(session, entity, ws_id)
        blank = _EXTERNAL_REFERENCES.get(entity.name, ())
        payload.files[f"{entity.name}.json"] = [serialize_row(row, blank) for row in rows]
        entity_counts[entity.name] = len(rows)

    # Both join tables hang off a collection, and collections are already
    # filtered by workspace, so the collection id is all the scoping they need.
    collection_ids = {row["id"] for row in payload.files["collections.json"]}
    for table_name in JOIN_TABLES:
        table = _JOIN_TABLE_OBJECTS[table_name]
        rows = (await session.execute(select(table))).mappings().all()
        kept = [
            {key: str(value) for key, value in dict(row).items()}
            for row in rows
            if str(row["collection_id"]) in collection_ids
        ]
        payload.files[f"{table_name}.json"] = kept
        entity_counts[table_name] = len(kept)

    payload.files["workspace.json"] = _workspace_block(workspace)

    storage = get_storage_provider()
    attachment_rows = payload.files["transaction_attachments.json"]
    missing_attachments = 0
    for row in attachment_rows:
        try:
            payload.attachments[f"{ATTACHMENTS_PREFIX}{row['id']}"] = await storage.download(row["storage_key"])
        except Exception:  # noqa: BLE001 - a missing file must not fail the export
            missing_attachments += 1
            logger.warning("Backup: attachment %s is missing from storage", row["id"])

    payload.files["metadata.json"] = {
        "export_date": datetime.now(timezone.utc).isoformat(),
        "format_version": _FORMAT_VERSION,
        "schema_revision": schema_versions.head_revision(),
        "workspace_id": str(ws_id),
        "workspace_name": workspace.name,
        "entity_counts": entity_counts,
        "attachments_missing": missing_attachments,
    }
    return payload


def build_backup_archive(
    files: dict[str, object],
    password: str | None = None,
    attachments: dict[str, bytes] | None = None,
) -> bytes:
    """Serialize `files` (name → JSON-serializable payload) into a zip.

    Returns the archive bytes. With a password the entries are encrypted with
    AES-256; without one the result is byte-for-byte the plain zip Securo has
    always produced. `attachments` are written as their raw bytes, under the
    names they already carry (see `ATTACHMENTS_PREFIX`).
    """
    buf = io.BytesIO()
    if password:
        archive = pyzipper.AESZipFile(
            buf, "w", compression=_COMPRESSION, encryption=pyzipper.WZ_AES
        )
        archive.setpassword(password.encode("utf-8"))
    else:
        archive = zipfile.ZipFile(buf, "w", _COMPRESSION)

    with archive as zf:
        for name, payload in files.items():
            zf.writestr(name, json.dumps(payload, indent=2, ensure_ascii=False))
        for name, blob in (attachments or {}).items():
            zf.writestr(name, blob)

    return buf.getvalue()
