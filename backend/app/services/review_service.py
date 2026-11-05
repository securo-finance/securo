"""PR-2 review-workflow helpers shared by the create and ingest paths.

Tiny on purpose: read by `transaction_service`, `import_service` and
`connection_service`, so it must import nothing from any of them (no
import cycles). The transition chokepoint itself —
`transaction_service.set_review_status` — lives where the packet put it;
this module only holds the closed-set constants, the per-workspace
policy read, and the §7.2 "observed money" rule.
"""
import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workspace import Workspace
from app.models.transaction import Transaction


async def guard_clean_review_rows(
    session: AsyncSession, workspace_id: uuid.UUID, ids: set[uuid.UUID] | None = None,
) -> None:
    """Never autoflush or discard an existing target's pending edits to refresh it."""
    from fastapi import HTTPException

    for row in session.dirty:
        if (
            isinstance(row, Transaction)
            and row.workspace_id == workspace_id
            and (ids is None or row.id in ids)
            and session.is_modified(row)
        ):
            await session.rollback()
            raise HTTPException(409, "Transaction has pending edits; reload before retrying")


async def lock_review_rows(
    session: AsyncSession, workspace_id: uuid.UUID, ids: set[uuid.UUID] | None = None,
) -> list[Transaction]:
    """Fresh existing-row observations, acquired in one global ID order."""
    await guard_clean_review_rows(session, workspace_id, ids)
    query = select(Transaction).where(Transaction.workspace_id == workspace_id)
    if ids is None:
        query = query.where(Transaction.source != "opening_balance")
    else:
        query = query.where(Transaction.id.in_(ids))
    with session.no_autoflush:
        result = await session.execute(
            query.order_by(Transaction.id).with_for_update(of=Transaction)
            .execution_options(populate_existing=True, autoflush=False)
        )
    return list(result.scalars().all())

#: The two-state triage axis. Closed set, Literal-validated at the schema
#: layer and re-checked by `set_review_status` at the service layer.
REVIEW_NEEDS = "needs_review"
REVIEW_DONE = "reviewed"
REVIEW_VALUES = (REVIEW_NEEDS, REVIEW_DONE)

#: Effective defaults when a workspace has no `review_policy` JSON (or an
#: absent key inside it): imports/sync land needing review, manual
#: entries land reviewed (pre-spec noise control, §7.1).
DEFAULT_INGEST_REVIEW = REVIEW_NEEDS
DEFAULT_MANUAL_REVIEW = REVIEW_DONE

#: File-ingest source values: rows from these are observed money arriving
#: from outside, whatever the parser that produced them (§7.2).
_OBSERVED_FILE_SOURCES = {"import", "csv", "ofx", "qif", "camt"}

#: Synthetic rows derived from the user's own bill machinery ride the
#: sync source but are exempt from `needs_review` (§7.2).
_BILL_CHARGE_PREFIX = "bill_charge:"


def is_observed_money(
    source: str, external_id: Optional[str] = None
) -> bool:
    """True when a row from this creation site is observed money arriving
    from outside (§7.2): file imports and provider sync — minus
    `bill_charge:*` synthetic charges, which are the user's own bill
    machinery riding the sync source. Everything else (manual entries,
    transfers, settlements, recurring placeholders, opening balances) is
    the user's own machinery and lands quiet.
    """
    if source in _OBSERVED_FILE_SOURCES:
        return True
    if source == "sync":
        return not (external_id or "").startswith(_BILL_CHARGE_PREFIX)
    return False


def ingest_review_status(
    source: str,
    external_id: Optional[str] = None,
    review_policy: Optional[dict] = None,
) -> str:
    """Review status an ingest-site row lands as: the workspace's `ingest`
    policy when the row is observed money, `reviewed` otherwise.

    `review_policy` is read once per batch by the caller (§7.1); absent
    means the default `needs_review`.
    """
    if not is_observed_money(source, external_id):
        return REVIEW_DONE
    if review_policy is not None and review_policy.get("ingest") in REVIEW_VALUES:
        return review_policy["ingest"]
    return DEFAULT_INGEST_REVIEW


async def get_review_policy(
    session: AsyncSession, workspace_id: uuid.UUID
) -> dict:
    """Return the workspace's effective review policy with defaults filled in.

    Read once per ingest/create batch (§7.1 — no per-row cost). NULL JSON
    or absent keys mean the defaults. Values are already Literal-checked
    on write by `workspace_service.validate_review_policy`; reads re-check
    defensively so a hand-edited row can't smuggle an unknown state into
    construction sites.
    """
    policy = (
        await session.execute(
            select(Workspace.review_policy).where(Workspace.id == workspace_id)
        )
    ).scalar_one_or_none() or {}
    ingest = policy.get("ingest")
    manual = policy.get("manual")
    return {
        "ingest": ingest if ingest in REVIEW_VALUES else DEFAULT_INGEST_REVIEW,
        "manual": manual if manual in REVIEW_VALUES else DEFAULT_MANUAL_REVIEW,
    }
