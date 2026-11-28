"""The operator opens an account on a managed instance (#1965) and looks it up (#1966).

One call writes the person, their workspaces and their limits in one
transaction, so no account ever exists without its limits. The account is born
with no password anyone knows: it cannot be signed in to until the person sets
one (#1967). The operator's reference is unique, and a retry with the same
reference and address answers the account it already opened.

The lookup carries who the account is, the workspaces it created and its
limits next to the current counts, and nothing from inside a workspace. The
counts come from `quota_service.check_fit`, the same functions the limits are
enforced with, so the lookup and a refusal can never disagree.
"""
from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping, Optional, Sequence

from fastapi_users.password import PasswordHelper
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.admin_access_log import AdminAccessLog, AdminAccessLogViewer
from app.models.user import User
from app.models.workspace import Workspace
from app.services import account_lookup_service, invoice_service, quota_service, workspace_service

#: Who acted, in the record (ADR 0042): the operator is no account.
OPERATOR_ACTOR = "operator"
ACTION = "account.open"
REASON = "Opened by the operator of the managed instance"


class OpenAccountError(Exception):
    """A refusal with a stable code the operator's tooling can branch on."""

    def __init__(self, code: str, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class WorkspaceRequest:
    name: str
    kind: str
    default_currency: str
    locale: Optional[str] = None
    legal_name: Optional[str] = None
    tax_jurisdiction: Optional[str] = None
    tax_ids: Sequence[Mapping[str, Any]] = ()


@dataclass(frozen=True)
class OpenedAccount:
    user: User
    workspaces: list[Workspace]
    created: bool


def _unknown_password() -> str:
    """The hash of a secret thrown away at once: no password matches it.

    The column cannot be empty, and an empty or malformed hash would make the
    sign-in route fail instead of refusing. The account removal shell uses the
    same shape.
    """
    return PasswordHelper().hash(secrets.token_urlsafe(32))


def _wallet_name(language: str) -> str:
    return "Carteira" if language.lower().startswith("pt") else "Wallet"


async def _by_reference(session: AsyncSession, reference: str) -> Optional[User]:
    return await session.scalar(select(User).where(User.external_reference == reference))


async def _owned_workspaces(session: AsyncSession, user_id: uuid.UUID) -> list[Workspace]:
    rows = await session.scalars(
        select(Workspace)
        .where(Workspace.created_by_user_id == user_id)
        .order_by(Workspace.created_at.asc(), Workspace.id)
    )
    return list(rows)


async def _existing(session: AsyncSession, email: str, reference: str) -> Optional[OpenedAccount]:
    """The account this call already opened, or a refusal when the call names another one."""
    by_reference = await _by_reference(session, reference)
    if by_reference is not None:
        if by_reference.email.lower() != email.strip().lower():
            raise OpenAccountError(
                "external_reference_taken",
                "This reference already names an account with another email.",
            )
        return OpenedAccount(
            user=by_reference,
            workspaces=await _owned_workspaces(session, by_reference.id),
            created=False,
        )
    if await account_lookup_service.find_account_by_email(session, email) is not None:
        raise OpenAccountError("email_taken", "An account with this email already exists.")
    return None


async def _open_workspace(session: AsyncSession, user: User, request: WorkspaceRequest, language: str) -> Workspace:
    """A workspace as the app creates one, plus the wallet a new account starts with."""
    workspace = await workspace_service.create_workspace(
        session,
        name=request.name,
        creator=user,
        kind=request.kind,
        default_currency=request.default_currency,
        locale=request.locale or language,
        tax_jurisdiction=request.tax_jurisdiction,
        self_membership=True,
        commit_defaults=False,
    )
    workspace.legal_name = request.legal_name
    if request.tax_ids:
        try:
            await invoice_service.set_issuer_tax_ids(
                session, workspace.id, [dict(t) for t in request.tax_ids]
            )
        except (invoice_service.InvoiceError, ValueError) as exc:
            code = getattr(exc, "code", "invalid_tax_id")
            raise OpenAccountError(code, str(exc), status_code=422) from exc
    session.add(
        Account(
            user_id=user.id,
            workspace_id=workspace.id,
            name=_wallet_name(workspace.locale or language),
            type="checking",
            balance=Decimal("0.00"),
            currency=workspace.default_currency,
        )
    )
    await session.flush()
    return workspace


def _record(
    user: User, workspaces: list[Workspace], quotas: Optional[Mapping[str, Optional[int]]], ip: Optional[str]
) -> list:
    entry = AdminAccessLog(
        id=uuid.uuid4(),
        actor_id=None,
        actor_email=OPERATOR_ACTOR,
        target_user_id=user.id,
        target_email=user.email,
        action=ACTION,
        changes={
            "external_reference": {"from": None, "to": user.external_reference},
            "workspaces": {
                "from": None,
                "to": [{"id": str(w.id), "kind": w.kind} for w in workspaces],
            },
            # None when no limits were written: the account follows the defaults.
            "quotas": {
                "from": None,
                "to": {c: quotas.get(c) for c in quota_service.COUNTERS} if quotas is not None else None,
            },
        },
        reason=REASON,
        ip=ip,
    )
    # The person the account belongs to reads the record of its opening.
    return [entry, AdminAccessLogViewer(entry_id=entry.id, viewer_id=user.id)]


async def open_account(
    session: AsyncSession,
    *,
    email: str,
    external_reference: str,
    language: str,
    workspaces: Sequence[WorkspaceRequest],
    quotas: Optional[Mapping[str, Optional[int]]] = None,
    ip: Optional[str] = None,
) -> OpenedAccount:
    """Open the account, or answer the one this reference already opened.

    Commits. Anything refused on the way (a limit the workspaces do not fit, a
    malformed tax id) rolls the whole account back.
    """
    existing = await _existing(session, email, external_reference)
    if existing is not None:
        return existing

    currency = workspaces[0].default_currency
    user = User(
        id=uuid.uuid4(),
        email=email.strip(),
        hashed_password=_unknown_password(),
        # No password is set until the person picks one (#1967).
        password_set_at=None,
        is_active=True,
        is_superuser=False,
        # Nobody has shown they read this mailbox yet: the operator typed it.
        is_verified=False,
        external_reference=external_reference,
        preferences={"language": language, "date_format": "MM/DD/YYYY", "currency_display": currency},
    )
    session.add(user)
    try:
        await session.flush()
        # The limits first, so the workspaces below are held to them. With no
        # limits sent the account gets no row of its own: a row, even all
        # null, would win over the instance's defaults.
        if quotas is not None:
            await quota_service.set_quota(session, user.id, quotas)
        opened = [await _open_workspace(session, user, w, language) for w in workspaces]
        session.add_all(_record(user, opened, quotas, ip))
        await session.commit()
    except IntegrityError:
        # A parallel call with the same reference or address committed first.
        await session.rollback()
        existing = await _existing(session, email, external_reference)
        if existing is None:
            raise
        return existing
    except BaseException:
        await session.rollback()
        raise
    return OpenedAccount(user=user, workspaces=opened, created=True)


# --- Looking an account up (#1966) ---------------------------------------------


@dataclass(frozen=True)
class OwnedWorkspace:
    id: uuid.UUID
    name: str
    kind: str
    is_archived: bool
    members: int


@dataclass(frozen=True)
class CounterUsage:
    counter: str
    current: int
    maximum: Optional[int]


@dataclass(frozen=True)
class AccountView:
    user: User
    workspaces: list[OwnedWorkspace]
    usage: list[CounterUsage]


async def find_account(
    session: AsyncSession,
    *,
    user_id: Optional[uuid.UUID] = None,
    email: Optional[str] = None,
    external_reference: Optional[str] = None,
) -> Optional[User]:
    if user_id is not None:
        return await session.get(User, user_id)
    if email is not None:
        return await account_lookup_service.find_account_by_email(session, email)
    if external_reference is not None:
        return await _by_reference(session, external_reference)
    return None


def password_is_set(user: User) -> bool:
    """Whether the person picked a password; an account the operator opened has none until then."""
    return user.password_set_at is not None


async def describe(session: AsyncSession, user: User) -> AccountView:
    owned = await _owned_workspaces(session, user.id)
    seats = await quota_service.count_members_of(session, [w.id for w in owned])
    workspaces = [OwnedWorkspace(w.id, w.name, w.kind, w.is_archived, seats.get(w.id, 0)) for w in owned]
    quota = await quota_service.get_quota(session, user.id)
    limits = {c: getattr(quota, c) if quota else None for c in quota_service.COUNTERS}
    fit = await quota_service.check_fit(session, user.id, limits)
    usage = [CounterUsage(f.counter, f.current, f.target) for f in fit.counters]
    return AccountView(user=user, workspaces=workspaces, usage=usage)

