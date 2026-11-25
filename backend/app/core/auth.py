import logging
import uuid
from decimal import Decimal
from typing import Optional, cast

import jwt
from sqlalchemy import case, func, select
from fastapi import Depends, Request
from fastapi_users import BaseUserManager, FastAPIUsers, UUIDIDMixin, exceptions, schemas
from fastapi_users.authentication import (
    AuthenticationBackend,
    BearerTransport,
    JWTStrategy,
)
from fastapi_users.db import SQLAlchemyUserDatabase
from fastapi_users.jwt import decode_jwt, generate_jwt
from sqlalchemy.ext.asyncio import AsyncSession
from starlette._utils import get_route_path

from app.core.auth_policy import require_local_auth_enabled
from app.core.config import get_settings
from app.core.database import get_async_session
from app.models.user import User

settings = get_settings()
logger = logging.getLogger(__name__)

#: A value with no account behind it, hashed where an unknown address asks for
#: a recovery link so the answer costs what a live account's answer costs.
_NO_SUCH_ACCOUNT = "no-such-account"


async def get_user_db(session: AsyncSession = Depends(get_async_session)):
    yield _AccountLookupUserDatabase(session, User)


class _AccountLookupUserDatabase(SQLAlchemyUserDatabase):
    """The SQLAlchemy adapter, resolving an address that names two rows."""

    async def get_by_email(self, email: str) -> Optional[User]:
        """The account the address names, also when two rows answer to it.

        An instance can already hold two accounts for one mailbox in different
        case (#568): the column's unique index is case-sensitive. The adapter
        raised ``MultipleResultsFound``, so sign-in and recovery answered 500
        for both accounts. The row written in the exact spelling that was
        typed wins, so each spelling reaches its own account; failing that the
        lowest id decides, so the answer never depends on row order. The id is
        a random UUID and cannot be what tells the two apart.
        """
        statement = (
            select(self.user_table)
            .where(func.lower(self.user_table.email) == func.lower(email))
            .order_by(
                case((self.user_table.email == email, 0), else_=1),
                self.user_table.id,
            )
            .limit(1)
        )
        return await self._get_user(statement)


class _FlushingUserDatabase(_AccountLookupUserDatabase):
    """The SQLAlchemy adapter, with an `update` that flushes and leaves the
    commit to the caller."""

    async def update(self, user, update_dict):
        for key, value in update_dict.items():
            setattr(user, key, value)
        self.session.add(user)
        await self.session.flush()
        return user


def _stored_language(preferences: object) -> str:
    """The account's language tag, or English when it is not a string.

    ``preferences`` is free-form JSON, so a number, a list or a null can be
    stored there. Treating one as a language crashed while composing the
    message, and only for accounts that exist: the 500 was itself the answer
    that the address is registered (ADR 0044).
    """
    if isinstance(preferences, dict):
        language = preferences.get("language")
        if isinstance(language, str) and language:
            return language
    return "en"


def _requested_currency(user_update: schemas.BaseUserUpdate) -> Optional[str]:
    """The display currency a profile update asks for, or None when it does
    not touch the preferences.

    An explicit `null` clears every preference, so the user falls back to the
    default currency like a dict without `currency_display` does.
    """
    fields = user_update.model_dump(exclude_unset=True)
    if "preferences" not in fields:
        return None
    preferences = fields["preferences"]
    if preferences is None:
        return settings.default_currency
    if not isinstance(preferences, dict):
        return None
    return preferences.get("currency_display", settings.default_currency)


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    reset_password_token_secret = settings.secret_key
    verification_token_secret = settings.secret_key

    @property
    def db_session(self) -> AsyncSession:
        """The session behind `user_db`, which get_user_manager always builds
        as the SQLAlchemy adapter."""
        return cast(SQLAlchemyUserDatabase, self.user_db).session

    async def update(
        self,
        user_update: schemas.BaseUserUpdate,
        user: User,
        safe: bool = False,
        request: Request | None = None,
    ) -> User:
        if user_update.password is not None:
            require_local_auth_enabled()
        # The owner choosing their own address ends any request an
        # administrator left waiting (#556): there is nothing left to accept.
        if user_update.email is not None and user_update.email != user.email:
            user.pending_email = None
            user.pending_email_requested_at = None
        new_currency = _requested_currency(user_update)
        if new_currency is None:
            return await super().update(user_update, user, safe=safe, request=request)
        # Compare with the committed currency, under the row lock the restamp
        # also takes: a switch another request committed meanwhile must not
        # let this one change the currency without restamping. The lock lasts
        # until this update commits, on either branch.
        current = (
            await self.db_session.execute(
                select(User)
                .where(User.id == user.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        if new_currency == current.primary_currency:
            return await super().update(user_update, user, safe=safe, request=request)
        return await self._update_and_restamp(
            user_update, user, new_currency, safe=safe, request=request
        )

    async def _update_and_restamp(
        self,
        user_update: schemas.BaseUserUpdate,
        user: User,
        new_currency: str,
        *,
        safe: bool,
        request: Request | None,
    ) -> User:
        """Change the display currency and the stored amounts in one commit.

        Stored primary amounts are in the old currency; totals that sum them
        would show old numbers under the new symbol (#1381). The SQLAlchemy
        adapter commits inside `update`, so for this one call the manager
        uses one that only flushes: the preference and the restamp then
        commit together or not at all, and a retry after a failure still sees
        the change. Today's quotes are fetched first, since that commits.
        """
        from app.services.primary_restamp_service import (
            fetch_missing_today_rates,
            restamp_user_primary_amounts,
        )

        session = self.db_session
        await fetch_missing_today_rates(session, user.id, new_currency)
        committing_db = self.user_db
        self.user_db = _FlushingUserDatabase(session, User)
        try:
            updated = await super().update(user_update, user, safe=safe, request=request)
            await restamp_user_primary_amounts(session, updated.id)
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            self.user_db = committing_db
        await session.refresh(updated)
        return updated

    def spend_password_hash(self) -> None:
        """Pay the hashing cost a live account pays before its link is built.

        The token carries a fingerprint of the stored password, so issuing a
        link hashes once. An address with no account never gets that far, and
        the difference shows in the response time: a fast answer says the
        address has no account. The route calls this before answering so every
        request costs the same (ADR 0044).
        """
        self.password_helper.hash(_NO_SUCH_ACCOUNT)

    async def forgot_password(
        self, user: User, request: Optional[Request] = None
    ) -> None:
        """Issue a recovery link bound to the address it is sent to.

        fastapi-users' own token names the account and a fingerprint of its
        password, so it stops working when the password changes and keeps
        working when the email does. A link only proves control of the mailbox
        it reached: if the account's address changes after it went out, the
        person holding that old mailbox is no longer the account's owner, and
        the token must die with the address (ADR 0044).
        """
        # Hash before the active check: an inactive account raises instead of
        # sending, and skipping the work here would make its answer
        # measurably faster than a live account's, which tells the two apart.
        password_fgpt = self.password_helper.hash(user.hashed_password)
        if not user.is_active:
            raise exceptions.UserInactive()

        token_data = {
            "sub": str(user.id),
            "password_fgpt": password_fgpt,
            "aud": self.reset_password_token_audience,
            "email": user.email,
        }
        token = generate_jwt(
            token_data,
            self.reset_password_token_secret,
            self.reset_password_token_lifetime_seconds,
        )
        await self.on_after_forgot_password(user, token, request)

    async def reset_password(
        self, token: str, password: str, request: Optional[Request] = None
    ) -> User:
        """Refuse a link issued for an address the account no longer has."""
        claims = self._reset_token_claims(token)
        issued_for = claims.get("email")
        if not isinstance(issued_for, str):
            # A link from before the address was part of the token: refuse it
            # rather than guess which mailbox it reached.
            raise exceptions.InvalidResetPasswordToken()
        try:
            current = await self.get(uuid.UUID(str(claims.get("sub"))))
        except (exceptions.UserNotExists, ValueError):
            raise exceptions.InvalidResetPasswordToken() from None
        if current.email != issued_for:
            raise exceptions.InvalidResetPasswordToken()
        return await super().reset_password(token, password, request)

    def _reset_token_claims(self, token: str) -> dict:
        try:
            return decode_jwt(
                token,
                self.reset_password_token_secret,
                [self.reset_password_token_audience],
            )
        except jwt.PyJWTError:
            raise exceptions.InvalidResetPasswordToken() from None

    async def on_after_forgot_password(
        self, user: User, token: str, request: Optional[Request] = None
    ):
        """Send the recovery link (ADR 0044).

        The route only gets here with EMAIL_ENABLED on. A relay that refuses
        the message is logged and swallowed, so a known and an unknown address
        still get the same answer and the endpoint reveals nothing.
        """
        from app.services import admin_service, email_service

        settings = get_settings()
        if not settings.email_enabled:
            return
        # A stored null, number or list is not a language; keep the default
        # instead of crashing while composing (which only a known address
        # would hit).
        language = _stored_language(user.preferences)
        templates = await admin_service.get_email_templates(self.db_session)
        message = email_service.password_reset_email(
            to=user.email,
            link=email_service.password_reset_link(token, settings),
            language=language,
            token_minutes=settings.password_reset_token_minutes,
            app_name=settings.app_name,
            templates=templates,
        )
        # Not sent here: handing it to the relay on this path would make a
        # known address answer later than an unknown one whenever the relay is
        # slow, which reveals that the account exists. The route picks the
        # message up and schedules the delivery after the response.
        if request is not None:
            request.state.password_reset_message = (message, settings)
        else:
            logger.warning(
                "Password recovery email for %s not sent: no request to deliver it from",
                user.email,
            )

    async def on_after_register(self, user: User, request: Optional[Request] = None):
        print(f"User {user.id} has registered.")
        # If request is None, this was called programmatically (e.g., from setup endpoint)
        # which handles wallet creation, categories, and rules itself.
        if request is None:
            return
        # Create default wallet for users registered via /auth/register.
        from app.models.account import Account
        from app.services.category_service import create_default_categories
        from app.services.rule_service import create_default_rules
        from app.services.workspace_service import create_personal_workspace_for_user

        session = self.db_session
        currency = user.primary_currency
        lang = _stored_language(user.preferences)

        # Every new user gets a Personal workspace + owner membership.
        # All their seeded data (wallet, categories, rules) reparent to it.
        workspace = await create_personal_workspace_for_user(session, user)

        wallet_name = "Carteira" if lang.startswith("pt") else "Wallet"
        wallet = Account(
            user_id=user.id,
            workspace_id=workspace.id,
            name=wallet_name,
            type="checking",
            balance=Decimal("0.00"),
            currency=currency,
        )
        session.add(wallet)
        await session.commit()

        # Create default categories and rules for the new user
        await create_default_categories(session, user.id, lang, workspace_id=workspace.id)
        await create_default_rules(session, user.id, lang, workspace_id=workspace.id)


async def get_user_manager(user_db: SQLAlchemyUserDatabase = Depends(get_user_db)):
    manager = UserManager(user_db)
    # How long a recovery link works (PASSWORD_RESET_TOKEN_MINUTES). Set here,
    # per manager, because fastapi-users declares it as a plain attribute.
    manager.reset_password_token_lifetime_seconds = (
        get_settings().password_reset_token_minutes * 60
    )
    yield manager


bearer_transport = BearerTransport(tokenUrl="/api/auth/login")


def get_jwt_strategy() -> JWTStrategy:
    return JWTStrategy(
        secret=settings.secret_key.get_secret_value(),
        lifetime_seconds=settings.access_token_expire_minutes * 60,
    )


auth_backend = AuthenticationBackend(
    name="jwt",
    transport=bearer_transport,
    get_strategy=get_jwt_strategy,
)

fastapi_users = FastAPIUsers[User, uuid.UUID](get_user_manager, [auth_backend])

_session_user = fastapi_users.current_user(active=True, optional=True)
current_superuser = fastapi_users.current_user(active=True, superuser=True)


def _bearer(request: Request) -> Optional[str]:
    header = request.headers.get("authorization") or ""
    scheme, _, value = header.partition(" ")
    return value.strip() if scheme.lower() == "bearer" and value.strip() else None


def _route_path(request: Request) -> str:
    """The path the app routes on, without a mount prefix (`root_path`).

    Uses the helper Starlette's own router uses, so scopes are checked on
    exactly the path the request is routed by.
    """
    return get_route_path(request.scope)


async def current_active_user(
    request: Request,
    user: Optional[User] = Depends(_session_user),
    session: AsyncSession = Depends(get_async_session),
) -> User:
    """The signed-in user, from a session JWT or an access token.

    An access token only reaches the routes its scopes open (see
    `app.core.access_token_scopes`); anything else answers 403, and routes
    that depend on `current_superuser` or fastapi-users' own dependencies
    never accept one. The token is kept on `request.state.access_token` so
    workspace resolution can pin the request to the token's workspace.
    """
    if user is not None:
        return user

    from fastapi import HTTPException, status

    from app.core.access_token_scopes import grants, required_scope
    from app.services.access_token_service import authenticate, looks_like_access_token

    raw = _bearer(request)
    if raw and looks_like_access_token(raw):
        token = await authenticate(session, raw)
        if token is not None:
            owner = await session.get(User, token.user_id)
            if owner is not None and owner.is_active:
                needed = required_scope(request.method, _route_path(request))
                if needed is None or not grants(token.scopes, needed):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="This access token's scopes don't cover this request.",
                    )
                request.state.access_token = token
                return owner
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
