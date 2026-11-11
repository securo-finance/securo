import uuid
from decimal import Decimal
from typing import Optional, cast

from fastapi import Depends, Request
from fastapi_users import BaseUserManager, FastAPIUsers, UUIDIDMixin, schemas
from fastapi_users.authentication import (
    AuthenticationBackend,
    BearerTransport,
    JWTStrategy,
)
from fastapi_users.db import SQLAlchemyUserDatabase
from sqlalchemy.ext.asyncio import AsyncSession
from starlette._utils import get_route_path

from app.core.auth_policy import require_local_auth_enabled
from app.core.config import get_settings
from app.core.database import get_async_session
from app.models.user import User

settings = get_settings()


async def get_user_db(session: AsyncSession = Depends(get_async_session)):
    yield SQLAlchemyUserDatabase(session, User)


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
        return await super().update(user_update, user, safe=safe, request=request)

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
        lang = (user.preferences or {}).get("language", "en")

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
    yield UserManager(user_db)


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
