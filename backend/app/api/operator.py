"""The operator's side of a managed instance (ADR 0049).

Outside the customer's admin on purpose: on a hosted instance the customer's
administrator is a superuser (ADR 0045), and the per-account limits are the
operator's. Guarded by OPERATOR_TOKEN alone; unset, these routes answer 404 as
if they did not exist.
"""
import hmac
import uuid
from typing import Literal, Optional

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import UserManager, get_user_manager
from app.core.config import get_settings
from app.core.database import get_async_session
from app.core.rate_limit import RateLimiter, client_ip
from app.models.user import User
from app.models.workspace import WorkspaceKind
from app.schemas.invoice import IssuerTaxIdInput
from app.services import email_service, operator_account_service, quota_service

router = APIRouter(prefix="/api/operator", tags=["operator"])

FirstAccessDelivery = Literal["email", "response"]


def _require_email() -> None:
    """A link to send by email needs email on (ADR 0044)."""
    if not get_settings().email_enabled:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "email_disabled",
                "message": "Email is off on this instance: ask for the first-access link in the response.",
            },
        )


def operator_only(request: Request) -> None:
    expected = get_settings().operator_token.get_secret_value()
    if not expected:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    scheme, _, given = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(given.encode(), expected.encode()):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Operator token required")


def managed_only() -> None:
    """Off a managed instance the account routes do not exist (ADR 0004)."""
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


class QuotaRead(QuotaValues):
    user_id: uuid.UUID


class CounterFitRead(BaseModel):
    counter: str
    current: int
    target: Limit
    excess: int


class FitCheckRead(BaseModel):
    allowed: bool
    counters: list[CounterFitRead]


async def _user(session: AsyncSession, user_id: uuid.UUID) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


@router.get("/quotas/{user_id}", response_model=QuotaRead, dependencies=[Depends(operator_only)])
async def read_quota(user_id: uuid.UUID, session: AsyncSession = Depends(get_async_session)):
    await _user(session, user_id)
    quota = await quota_service.get_quota(session, user_id)
    values = {c: getattr(quota, c) if quota else None for c in quota_service.COUNTERS}
    return QuotaRead(user_id=user_id, **values)


@router.put("/quotas/{user_id}", response_model=QuotaRead, dependencies=[Depends(operator_only)])
async def write_quota(
    user_id: uuid.UUID, body: QuotaValues, session: AsyncSession = Depends(get_async_session)
):
    await _user(session, user_id)
    quota = await quota_service.set_quota(session, user_id, body.model_dump())
    await session.commit()
    return QuotaRead(user_id=user_id, **{c: getattr(quota, c) for c in quota_service.COUNTERS})


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
    #: How the person gets the link to set their password: by email (needs
    #: EMAIL_ENABLED), or in this response for an instance that delivers it
    #: some other way.
    first_access: FirstAccessDelivery = "email"


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
    #: Only with `first_access: "response"` and while no password is set.
    first_access_link: Optional[str] = None


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
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_async_session),
    user_manager: UserManager = Depends(get_user_manager),
):
    """Open an account with its workspaces and limits in one transaction.

    The account has no password anyone knows until the person sets one, with
    the first-access link sent to its address or handed back in the response
    (#1967). An email that already has an account is refused (`email_taken`),
    never merged.
    """
    if body.first_access == "email":
        # Refused before anything is written: an account nobody can enter
        # helps no one.
        _require_email()
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
    link = None
    if opened.user.password_set_at is None:
        if body.first_access == "response":
            link = _first_access_link(user_manager, opened.user)
        elif opened.created:
            # A retry answers the account it already opened and sends nothing
            # again; the resend route is there for that.
            background_tasks.add_task(email_service.deliver, _first_access_message(user_manager, opened.user))
    # The limits that hold for the account: its own row, or none (unbounded).
    quota = await quota_service.get_quota(session, opened.user.id)
    return OpenedAccountRead(
        user_id=opened.user.id,
        email=opened.user.email,
        external_reference=opened.user.external_reference or body.external_reference,
        created=opened.created,
        workspaces=[OpenedWorkspaceRead.model_validate(w, from_attributes=True) for w in opened.workspaces],
        quotas=QuotaValues(**{c: getattr(quota, c) if quota else None for c in quota_service.COUNTERS}),
        first_access_link=link,
    )


# --- The first access (#1967) ---------------------------------------------------


def _first_access_link(user_manager: UserManager, user: User) -> str:
    """The link that sets the account's first password, on the recovery screen.

    Never logged: whoever holds it can take the account until it is used.
    """
    return email_service.password_reset_link(user_manager.first_access_token(user))


def _first_access_message(user_manager: UserManager, user: User) -> email_service.Email:
    settings = get_settings()
    preferences = user.preferences if isinstance(user.preferences, dict) else {}
    return email_service.first_access_email(
        to=user.email,
        link=_first_access_link(user_manager, user),
        language=preferences.get("language"),
        token_minutes=settings.first_access_token_minutes,
        app_name=settings.app_name,
    )


class FirstAccessLinkRequest(BaseModel):
    delivery: FirstAccessDelivery = "email"


class FirstAccessLinkRead(BaseModel):
    #: Only when the link was asked for in the response.
    link: Optional[str] = None


first_access_rate_limit = RateLimiter(
    max_requests=10, window_seconds=3600, scope="operator_first_access", unavailable_status=503
)


@router.post(
    "/accounts/{user_id}/first-access-link",
    response_model=FirstAccessLinkRead,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(managed_only), Depends(operator_only), Depends(first_access_rate_limit)],
)
async def resend_first_access_link(
    user_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    body: FirstAccessLinkRequest = Body(default_factory=FirstAccessLinkRequest),
    session: AsyncSession = Depends(get_async_session),
    user_manager: UserManager = Depends(get_user_manager),
):
    """Send the first-access link again, for an account that has not set a password yet.

    By email it answers the same 202 whether or not a message went out. Asked
    in the response, an account that already set its password is refused with
    `password_already_set`: there is no link to hand out.
    """
    user = await _user(session, user_id)
    if body.delivery == "response":
        if user.password_set_at is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "password_already_set", "message": "This account already has a password."},
            )
        return FirstAccessLinkRead(link=_first_access_link(user_manager, user))
    _require_email()
    if user.password_set_at is None and user.is_active:
        background_tasks.add_task(email_service.deliver, _first_access_message(user_manager, user))
    return FirstAccessLinkRead()
