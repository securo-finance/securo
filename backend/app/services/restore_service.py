"""Reading a Securo backup back into a brand-new workspace.

A restore never writes into an existing workspace. It creates one, makes the
caller its owner, and copies the archive into it. That is the difference
between "we asked before overwriting" and "there is no code path that
overwrites": the destructive case does not exist to be confirmed.

Every row gets a fresh primary key. A single map from (table, old id) to new id
is what makes the copy land: foreign keys are rewritten through it, and so are
the ids that live inside JSON — a rule's `set_category` action is a UUID in a
text field with no foreign key behind it, and a restore that copied it verbatim
would leave the rule pointing at a category the new workspace does not have.
Rules silently drop such an action at apply time, so the damage would be
invisible.

The whole copy runs in one transaction. A file that fails halfway leaves no
half-restored workspace behind.
"""
import json
import logging
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, BinaryIO

import pyzipper
from sqlalchemy import Boolean, Date, DateTime, Integer, Numeric, insert
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import is_valid_timezone
from app.models.transaction import Transaction
from app.models.transaction_attachment import TransactionAttachment
from app.models.user import User
from app.models.workspace import WORKSPACE_KINDS, Workspace, WorkspaceMember
from app.providers import get_storage_provider
from app.services import schema_versions
from app.services.attachment_service import sanitize_filename, served_content_type
from app.services.backup_service import (
    ATTACHMENTS_PREFIX,
    EXPORTED_ENTITIES,
    JOIN_TABLES,
    _JOIN_TABLE_OBJECTS,
)

logger = logging.getLogger(__name__)

#: The format this code writes and reads. A file from a newer one is refused
#: rather than half-understood; a 1.0 file predates the tables that make the
#: foreign keys resolvable, so it is refused too, with the reason in the text.
SUPPORTED_FORMAT = "1.1"
_REFUSED_OLDER_FORMATS = {"1.0": "Backups in format 1.0 cannot be restored: export a new one from the instance that has the data."}

_METADATA_FILE = "metadata"
_WORKSPACE_FILE = "workspace"
_ATTACHMENTS_ENTITY = "transaction_attachments"
_ATTACHMENTS_MISSING = "attachment file is missing from the archive"
#: Everything a broken, truncated or wrongly-encrypted file can raise.
#: `pyzipper` ships its own `BadZipFile`, a different class from the stdlib
#: one, so both are named: catching only the stdlib one lets an upload that is
#: not a zip at all escape as a 500.
_UNREADABLE_ARCHIVE = (
    zipfile.BadZipFile,
    pyzipper.BadZipFile,
    RuntimeError,
    json.JSONDecodeError,
    EOFError,
)


class RestoreError(ValueError):
    """A backup this instance cannot or must not restore."""


@dataclass
class ArchiveContents:
    """What was read out of the zip, before any row is written."""

    metadata: dict[str, Any]
    workspace: dict[str, Any]
    entities: dict[str, list[dict[str, Any]]]
    attachments: dict[str, bytes] = field(default_factory=dict)


@dataclass
class RestoreResult:
    workspace_id: uuid.UUID
    workspace_name: str
    entity_counts: dict[str, int]
    attachments_restored: int
    attachments_missing: int


#: Which tables a column points at, by the entity file name. Every id is
#: rewritten through `id_map` when the row is inserted.
_REFERENCES: dict[str, dict[str, str]] = {
    "categories": {"group_id": "category_groups"},
    "payee_tax_ids": {"payee_id": "payees"},
    "payee_mapping": {"target_id": "payees"},
    "assets": {"group_id": "asset_groups"},
    "asset_values": {"asset_id": "assets"},
    "accounts": {"secured_asset_id": "assets"},
    "account_reserved_balances": {"account_id": "accounts"},
    "credit_card_bills": {"account_id": "accounts"},
    "import_logs": {"account_id": "accounts"},
    "recurring_transactions": {"account_id": "accounts", "category_id": "categories"},
    "recurring_transaction_category_lines": {
        "recurring_transaction_id": "recurring_transactions",
        "category_id": "categories",
    },
    "group_members": {"group_id": "groups"},
    "transactions": {
        "account_id": "accounts",
        "category_id": "categories",
        "payee_id": "payees",
        "import_id": "import_logs",
        "bill_id": "credit_card_bills",
        "recurring_transaction_id": "recurring_transactions",
    },
    "transaction_category_lines": {"transaction_id": "transactions", "category_id": "categories"},
    "transaction_splits": {"transaction_id": "transactions", "group_member_id": "group_members"},
    _ATTACHMENTS_ENTITY: {"transaction_id": "transactions"},
    "group_settlements": {
        "group_id": "groups",
        "from_member_id": "group_members",
        "to_member_id": "group_members",
        "transaction_id": "transactions",
        "receiver_transaction_id": "transactions",
    },
    "budgets": {"category_id": "categories"},
    "goals": {"account_id": "accounts", "asset_id": "assets", "asset_group_id": "asset_groups"},
}

#: Columns filled after every row exists, because they point at a row of their
#: own table: a transfer's other leg, and the series an installment belongs to.
#: Both are plain UUIDs with no foreign key, so a naive copy would leave them
#: pointing at ids the new workspace never got.
_DEFERRED_SELF_REFERENCES = ("transfer_pair_id", "installment_series_id")

#: Ids living inside a rule's JSON. The key is the condition's `field` or the
#: action's `op`; both name a category or a payee in a text value.
_JSON_ID_FIELDS: dict[str, str] = {
    "category_id": "categories",
    "payee_id": "payees",
    "account_id": "accounts",
    "bill_id": "credit_card_bills",
    "set_category": "categories",
    "set_payee": "payees",
}

_COLUMN_USER = "user_id"


def open_archive(stream: BinaryIO, password: str | None = None) -> ArchiveContents:
    """Read the zip into its parts. Raises `RestoreError` on anything unreadable."""
    attachments: dict[str, bytes] = {}
    files: dict[str, Any] = {}
    try:
        with pyzipper.AESZipFile(stream) as archive:
            if password:
                archive.setpassword(password.encode("utf-8"))
            for name in archive.namelist():
                if name.startswith(ATTACHMENTS_PREFIX):
                    attachments[name[len(ATTACHMENTS_PREFIX):]] = archive.read(name)
                elif name.endswith(".json") and "/" not in name:
                    files[name[: -len(".json")]] = json.loads(archive.read(name))
    except _UNREADABLE_ARCHIVE as exc:
        raise RestoreError("Could not open the archive: wrong password or damaged file.") from exc

    metadata = files.get(_METADATA_FILE)
    if not isinstance(metadata, dict):
        raise RestoreError("This file is not a Securo backup: metadata.json is missing.")
    workspace = files.get(_WORKSPACE_FILE)
    if not isinstance(workspace, dict):
        raise RestoreError("This backup has no workspace settings.")

    expected = [entity.name for entity in EXPORTED_ENTITIES] + list(JOIN_TABLES)
    missing = [name for name in expected if name not in files]
    if missing:
        raise RestoreError(f"This backup is incomplete: {', '.join(sorted(missing))} missing.")
    return ArchiveContents(
        metadata=metadata,
        workspace=workspace,
        entities={name: files[name] for name in expected},
        attachments=attachments,
    )


def _check_versions(metadata: dict[str, Any]) -> None:
    version = str(metadata.get("format_version") or "")
    if version in _REFUSED_OLDER_FORMATS:
        raise RestoreError(_REFUSED_OLDER_FORMATS[version])
    if version != SUPPORTED_FORMAT:
        raise RestoreError(
            f"This backup is in format {version or 'unknown'}; this version of Securo restores format {SUPPORTED_FORMAT}."
        )

    revision = str(metadata.get("schema_revision") or "")
    if not revision:
        raise RestoreError("This backup does not say which schema it was made with, so it cannot be restored.")
    if not schema_versions.is_known(revision):
        raise RestoreError(
            f"This backup was made with database schema {revision}, which this instance does not have. "
            "Update this instance before restoring."
        )
    if not schema_versions.is_at_or_older(revision):
        raise RestoreError(
            f"This backup was made with a newer database schema ({revision}); "
            f"this instance is at {schema_versions.head_revision()}. Update this instance before restoring."
        )


def _check_recurring_duplicates(contents: ArchiveContents) -> None:
    """Refuse an archive whose bills collide once they all belong to one person.

    Every restored row is written for whoever runs the restore, so two members'
    identical bills in the source workspace become the same bill here, which the
    duplicate guard refuses. Saying which bill it is beats a constraint error
    raised halfway through the copy.
    """
    seen: set[tuple[str, str, str]] = set()
    for row in contents.entities.get("recurring_transactions", []):
        key = (str(row.get("description")), str(row.get("frequency")), str(row.get("start_date")))
        if key in seen:
            description, frequency, start_date = key
            raise RestoreError(
                f'This backup holds two identical recurring bills ("{description}", '
                f"{frequency} from {start_date}); this version keeps one per person in a workspace."
            )
        seen.add(key)


def _coerce(column, value: Any) -> Any:
    """Turn the JSON value back into what the column's own type expects."""
    if value is None:
        return None
    column_type = column.type
    if isinstance(column_type, PGUUID):
        return uuid.UUID(str(value))
    if isinstance(column_type, Numeric):
        return Decimal(str(value))
    if isinstance(column_type, DateTime):
        return datetime.fromisoformat(str(value))
    if isinstance(column_type, Date):
        return date.fromisoformat(str(value))
    if isinstance(column_type, Boolean):
        return bool(value)
    if isinstance(column_type, Integer):
        return int(value)
    return value


def _mapped(id_map: dict[tuple[str, str], uuid.UUID], target: str, old_value: Any) -> uuid.UUID | None:
    if old_value in (None, ""):
        return None
    new_id = id_map.get((target, str(old_value)))
    if new_id is None:
        raise RestoreError(
            f"This backup is inconsistent: a {target} row it points at is not in the archive."
        )
    return new_id


def _remap_rule_json(node: Any, id_map: dict[tuple[str, str], uuid.UUID]) -> Any:
    """Rewrite every category/payee/account id a rule keeps inside its JSON."""
    if isinstance(node, list):
        return [_remap_rule_json(item, id_map) for item in node]
    if not isinstance(node, dict):
        return node
    out = dict(node)
    target = _JSON_ID_FIELDS.get(str(out.get("field") or out.get("op") or ""))
    if target and isinstance(out.get("value"), str):
        new_id = id_map.get((target, out["value"]))
        if new_id is not None:
            out["value"] = str(new_id)
    if isinstance(out.get("conditions"), list):
        out["conditions"] = _remap_rule_json(out["conditions"], id_map)
    return out


def _insert_entity(
    session: AsyncSession,
    entity_name: str,
    model: type,
    rows: list[dict[str, Any]],
    *,
    workspace: Workspace,
    user: User,
    id_map: dict[tuple[str, str], uuid.UUID],
) -> tuple[list[Any], dict[str, Any]]:
    """Add one table's rows; returns the instances and old rows by old id."""
    columns = {column.key: column for column in getattr(model, "__table__").columns}
    references = _REFERENCES.get(entity_name, {})
    instances: list[Any] = []
    by_old_id: dict[str, Any] = {}

    for row in rows:
        old_id = row.get("id")
        if not old_id:
            raise RestoreError(f"This backup is inconsistent: a row in {entity_name} has no id.")
        new_id = uuid.uuid4()
        values: dict[str, Any] = {"id": new_id}
        for key, column in columns.items():
            if key == "id" or key in _DEFERRED_SELF_REFERENCES:
                continue
            if key == "workspace_id":
                values[key] = workspace.id
            elif key == _COLUMN_USER:
                values[key] = user.id
            elif key == "linked_user_id":
                # The member was linked to a Securo user who is not in the
                # archive; the member stays as a shadow record.
                values[key] = None
            elif entity_name == "asset_groups" and key == "external_id" and row.get(key):
                # A provider wallet's key is unique per (user, source) across
                # every workspace, by design: the sync adopts the same wallet
                # when the bank moves it. A restored copy is a different
                # wallet in a different book, so its key is namespaced to the
                # new workspace instead of stealing the original's identity.
                values[key] = f"restored:{workspace.id}:{row[key]}"[:255]
            elif key in references:
                values[key] = _mapped(id_map, references[key], row.get(key))
            elif key in row:
                values[key] = _coerce(column, row[key])

        if entity_name == "rules":
            values["conditions"] = _remap_rule_json(row.get("conditions") or [], id_map)
            values["actions"] = _remap_rule_json(row.get("actions") or [], id_map)

        instance = model(**values)
        session.add(instance)
        instances.append(instance)
        by_old_id[str(old_id)] = instance
        id_map[(entity_name, str(old_id))] = new_id

    return instances, by_old_id


def _finish_transactions(transactions: dict[str, Transaction], rows: list[dict[str, Any]], id_map) -> None:
    """Point transfer legs at each other and keep installment series together."""
    rows_by_id = {str(row.get("id")): row for row in rows}
    series: dict[str, uuid.UUID] = {}
    for old_id, transaction in transactions.items():
        row = rows_by_id.get(old_id, {})
        pair = row.get("transfer_pair_id")
        transaction.transfer_pair_id = id_map.get(("transactions", str(pair))) if pair else None
        old_series = row.get("installment_series_id")
        if old_series:
            transaction.installment_series_id = series.setdefault(str(old_series), uuid.uuid4())


async def _restore_attachments(
    session: AsyncSession,
    workspace: Workspace,
    user: User,
    rows: list[dict[str, Any]],
    blobs: dict[str, bytes],
    id_map: dict[tuple[str, str], uuid.UUID],
) -> tuple[int, int]:
    """Write the archived bytes through the storage provider under fresh keys.

    A missing blob is counted and skipped, never fatal: a backup whose storage
    lost a file is still the best copy of everything else the user has.
    """
    restored = missing = 0
    storage = get_storage_provider()
    for row in rows:
        transaction_id = _mapped(id_map, "transactions", row.get("transaction_id"))
        blob = blobs.get(str(row.get("id")))
        if transaction_id is None or blob is None:
            missing += 1
            continue
        filename = sanitize_filename(str(row.get("filename") or "file"))
        # The key starts with the workspace id (ADR 0019) and with the new
        # transaction, because both ids changed in the restore.
        storage_key = f"{workspace.id}/{transaction_id}/{uuid.uuid4().hex[:8]}_{filename}"
        try:
            stored = await storage.upload(storage_key, blob, served_content_type(filename))
        except Exception:  # noqa: BLE001 - one bad file must not fail the restore
            logger.warning("Restore: could not store attachment %s", row.get("id"), exc_info=True)
            missing += 1
            continue
        session.add(
            TransactionAttachment(
                id=id_map[(_ATTACHMENTS_ENTITY, str(row["id"]))],
                workspace_id=workspace.id,
                user_id=user.id,
                transaction_id=transaction_id,
                filename=filename,
                storage_key=stored.storage_key,
                content_type=stored.content_type,
                size=stored.size,
            )
        )
        restored += 1
    return restored, missing


async def restore_workspace(
    session: AsyncSession, user: User, contents: ArchiveContents
) -> RestoreResult:
    """Copy the archive into a workspace owned by `user`. Caller commits."""
    _check_versions(contents.metadata)
    _check_recurring_duplicates(contents)

    settings_block = contents.workspace
    kind = settings_block.get("kind") if settings_block.get("kind") in WORKSPACE_KINDS else "personal"
    timezone_name = settings_block.get("timezone")
    workspace = Workspace(
        name=str(settings_block.get("name") or contents.metadata.get("workspace_name") or "Restored workspace")[:100],
        kind=kind,
        created_by_user_id=user.id,
        default_currency=str(settings_block.get("default_currency") or "USD")[:3],
        locale=settings_block.get("locale"),
        timezone=timezone_name if timezone_name and is_valid_timezone(timezone_name) else None,
        tax_jurisdiction=settings_block.get("tax_jurisdiction"),
        legal_name=settings_block.get("legal_name"),
        address=settings_block.get("address"),
        icon=settings_block.get("icon"),
        color=settings_block.get("color"),
    )
    session.add(workspace)
    await session.flush()
    session.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner"))
    await session.flush()

    id_map: dict[tuple[str, str], uuid.UUID] = {}
    counts: dict[str, int] = {}
    transaction_instances: dict[str, Transaction] = {}

    for entity in EXPORTED_ENTITIES:
        rows = contents.entities.get(entity.name, [])
        if entity.name == _ATTACHMENTS_ENTITY:
            # Reserved for `_restore_attachments`, which needs the bytes.
            for row in rows:
                if row.get("id"):
                    id_map[(_ATTACHMENTS_ENTITY, str(row["id"]))] = uuid.uuid4()
            continue
        instances, by_old_id = _insert_entity(
            session,
            entity.name,
            entity.model,
            rows,
            workspace=workspace,
            user=user,
            id_map=id_map,
        )
        if entity.name == "transactions":
            transaction_instances = {old: instance for old, instance in by_old_id.items()}
        counts[entity.name] = len(instances)
        await session.flush()

    _finish_transactions(transaction_instances, contents.entities["transactions"], id_map)
    await session.flush()

    for table_name in JOIN_TABLES:
        other_column = "account_id" if table_name == "collection_accounts" else "asset_group_id"
        other_entity = "accounts" if table_name == "collection_accounts" else "asset_groups"
        values = []
        for row in contents.entities.get(table_name, []):
            collection_id = _mapped(id_map, "collections", row.get("collection_id"))
            other_id = _mapped(id_map, other_entity, row.get(other_column))
            if collection_id and other_id:
                values.append({"collection_id": collection_id, other_column: other_id})
        if values:
            await session.execute(insert(_JOIN_TABLE_OBJECTS[table_name]), values)

    attachment_rows = contents.entities.get(_ATTACHMENTS_ENTITY, [])
    restored, missing = await _restore_attachments(
        session, workspace, user, attachment_rows, contents.attachments, id_map
    )
    counts[_ATTACHMENTS_ENTITY] = restored
    await session.flush()

    return RestoreResult(
        workspace_id=workspace.id,
        workspace_name=workspace.name,
        entity_counts=counts,
        attachments_restored=restored,
        attachments_missing=missing,
    )
