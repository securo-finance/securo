"""The operator's side of a managed instance (ADR 0049).

Outside the customer's admin on purpose: on a hosted instance the customer's
administrator is a superuser (ADR 0045), and the per-account limits are the
operator's. Guarded by OPERATOR_TOKEN alone; unset, these routes answer 404 as
if they did not exist.
"""
import hmac
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_async_session
from app.core.rate_limit import client_ip
from app.models.user import User
from app.models.workspace import WorkspaceKind
from app.schemas.invoice import IssuerTaxIdInput
from app.services import operator_account_service, quota_service

router = APIRouter(prefix="/api/operator", tags=["operator"])


def operator_only(request: Request) -> None:
    expected = get_settings().operator_token.get_secret_value()
    if not expected:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    scheme, _, given = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(given.encode(), expected.encode()):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Operator token required")


def managed_only() -> None:
    """Off a managed instance the route that opens accounts does not exist (ADR 0004)."""
    if not get_settings().managed_instance:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")


Limit = Optional[int]

# The columns are 32-bit integers; past this the database refuses with a 500.
_INT32_MAX = 2_147_483_647


class QuotaValues(BaseModel):
    """A null counter is unbounded."""

    max_personal_workspaces: Limit = Field(default=None, ge=0, le=_INT32_MAX)
    max_business_workspaces: Limit = Field(default=None, ge=0, le=_INT32_MAX)
    max_bank_connections: Limit = Field(default=None, ge=0, le=_INT32_MAX)
    max_members_per_workspace: Limit = Field(default=None, ge=0, le=_INT32_MAX)


LimitsSource = quota_service.Source


class QuotaRead(QuotaValues):
    """The limits that hold for the account, and where they come from."""

    user_id: uuid.UUID
    source: LimitsSource


class QuotaDefaultsRead(QuotaValues):
    """Null on all four with `is_set` false: no default, accounts without a row are unbounded."""

    is_set: bool


class CounterFitRead(BaseModel):
    counter: str
    current: int
    target: Limit
    excess: int


class FitCheckRead(BaseModel):
    allowed: bool
    counters: list[CounterFitRead]
    source: LimitsSource


async def _user(session: AsyncSession, user_id: uuid.UUID) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


@router.get("/quotas/{user_id}", response_model=QuotaRead, dependencies=[Depends(operator_only)])
async def read_quota(user_id: uuid.UUID, session: AsyncSession = Depends(get_async_session)):
    await _user(session, user_id)
    limits, source = await quota_service.effective_limits(session, user_id)
    return QuotaRead(user_id=user_id, source=source, **limits)


@router.put("/quotas/{user_id}", response_model=QuotaRead, dependencies=[Depends(operator_only)])
async def write_quota(
    user_id: uuid.UUID, body: QuotaValues, session: AsyncSession = Depends(get_async_session)
):
    await _user(session, user_id)
    quota = await quota_service.set_quota(session, user_id, body.model_dump())
    await session.commit()
    return QuotaRead(
        user_id=user_id, source=quota_service.OWN, **{c: getattr(quota, c) for c in quota_service.COUNTERS}
    )


def _defaults_read(defaults: Optional[dict]) -> QuotaDefaultsRead:
    return QuotaDefaultsRead(is_set=defaults is not None, **(defaults or {}))


@router.get("/quota-defaults", response_model=QuotaDefaultsRead, dependencies=[Depends(operator_only)])
async def read_quota_defaults(session: AsyncSession = Depends(get_async_session)):
    return _defaults_read(await quota_service.get_defaults(session))


@router.put("/quota-defaults", response_model=QuotaDefaultsRead, dependencies=[Depends(operator_only)])
async def write_quota_defaults(body: QuotaValues, session: AsyncSession = Depends(get_async_session)):
    """The limits every account without a row of its own is held to. Rewrites no account."""
    defaults = await quota_service.set_defaults(session, body.model_dump())
    await session.commit()
    return _defaults_read(defaults)


@router.delete(
    "/quota-defaults", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(operator_only)]
)
async def clear_quota_defaults(session: AsyncSession = Depends(get_async_session)):
    """No default: accounts without a row are unbounded again."""
    await quota_service.set_defaults(session, None)
    await session.commit()


@router.post(
    "/quotas/{user_id}/fit-check",
    response_model=FitCheckRead,
    dependencies=[Depends(operator_only)],
)
async def fit_check(
    user_id: uuid.UUID, body: QuotaValues, session: AsyncSession = Depends(get_async_session)
):
    """Whether the account already fits the target limits. Writes nothing."""
    await _user(session, user_id)
    result = await quota_service.check_fit(session, user_id, body.model_dump())
    return FitCheckRead(
        allowed=result.allowed,
        counters=[CounterFitRead(**vars(f)) for f in result.counters],
        source=result.source,
    )


# --- Opening an account (#1965) -------------------------------------------------


class OpenWorkspace(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    kind: WorkspaceKind
    default_currency: str = Field(min_length=3, max_length=3)
    #: The language of the seeded categories and rules; the account's when left out.
    locale: Optional[str] = Field(default=None, max_length=10)
    legal_name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    tax_jurisdiction: Optional[str] = Field(default=None, max_length=10)
    tax_ids: list[IssuerTaxIdInput] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def _legal_fields_belong_to_a_business(self) -> "OpenWorkspace":
        if self.kind != "business" and (self.legal_name or self.tax_jurisdiction or self.tax_ids):
            raise ValueError("legal_name, tax_jurisdiction and tax_ids are for a business workspace")
        return self


class OpenAccount(BaseModel):
    email: EmailStr
    #: The operator's own name for the account. Unique: a retry with the same
    #: reference answers the account it already opened.
    external_reference: str = Field(min_length=1, max_length=255)
    language: str = Field(default="en", min_length=2, max_length=10)
    workspaces: list[OpenWorkspace] = Field(min_length=1, max_length=20)
    #: Left out, the account gets no limits of its own and follows the
    #: instance's defaults.
    quotas: Optional[QuotaValues] = None


class OpenedWorkspaceRead(BaseModel):
    id: uuid.UUID
    name: str
    kind: str
    default_currency: str
    locale: Optional[str] = None


class OpenedAccountRead(BaseModel):
    user_id: uuid.UUID
    email: str
    external_reference: str
    #: False when this reference had already opened the account: nothing changed.
    created: bool
    workspaces: list[OpenedWorkspaceRead]
    quotas: QuotaValues


@router.post(
    "/accounts",
    response_model=OpenedAccountRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(managed_only), Depends(operator_only)],
)
async def open_account(
    body: OpenAccount,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_async_session),
):
    """Open an account with its workspaces and limits in one transaction.

    The account has no password anyone knows until the person sets one. An
    email that already has an account is refused (`email_taken`), never merged.
    """
    try:
        opened = await operator_account_service.open_account(
            session,
            email=str(body.email),
            external_reference=body.external_reference,
            language=body.language,
            workspaces=[
                operator_account_service.WorkspaceRequest(
                    name=w.name,
                    kind=w.kind,
                    default_currency=w.default_currency.upper(),
                    locale=w.locale,
                    legal_name=w.legal_name,
                    tax_jurisdiction=w.tax_jurisdiction,
                    tax_ids=[t.model_dump() for t in w.tax_ids],
                )
                for w in body.workspaces
            ],
            quotas=body.quotas.model_dump() if body.quotas is not None else None,
            ip=client_ip(request),
        )
    except operator_account_service.OpenAccountError as exc:
        raise HTTPException(
            status_code=exc.status_code, detail={"code": exc.code, "message": exc.message}
        ) from exc
    if not opened.created:
        response.status_code = status.HTTP_200_OK
    # The limits that hold for the account: its own row, or none (unbounded).
    quota = await quota_service.get_quota(session, opened.user.id)
    return OpenedAccountRead(
        user_id=opened.user.id,
        email=opened.user.email,
        external_reference=opened.user.external_reference or body.external_reference,
        created=opened.created,
        workspaces=[OpenedWorkspaceRead.model_validate(w, from_attributes=True) for w in opened.workspaces],
        quotas=QuotaValues(**{c: getattr(quota, c) if quota else None for c in quota_service.COUNTERS}),
    )
