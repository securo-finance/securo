import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.accounts import router as accounts_router
from app.api.budgets import router as budgets_router
from app.api.goals import router as goals_router
from app.api.groups import router as groups_router
from app.api.categories import router as categories_router
from app.api.category_groups import router as category_groups_router
from app.api.connections import router as connections_router
from app.api.custom_auth import router as custom_auth_router
from app.api.dashboard import router as dashboard_router
from app.api.import_logs import router as import_logs_router
from app.api.oidc_auth import router as oidc_auth_router
from app.api.passkeys import router as passkeys_router
from app.api.import_transactions import router as import_router
from app.api.info import router as info_router
from app.api.recurring_transactions import router as recurring_router
from app.api.reconciliation import router as reconciliation_router
from app.api.rules import router as rules_router
from app.api.assets import router as assets_router
from app.api.asset_groups import router as asset_groups_router
from app.api.collections import router as collections_router
from app.api.reports import router as reports_router
from app.api.search import router as search_router
from app.api.setup import router as setup_router
from app.api.currencies import router as currencies_router
from app.api.export import router as export_router
from app.api.fx_rates import router as fx_rates_router
from app.api.attachments import router as attachments_router
from app.api.fiscal import router as fiscal_router
from app.api.invoice_attachments import router as invoice_attachments_router
from app.api.invoices import router as invoices_router
from app.api.public_invoices import router as public_invoices_router
from app.api.payees import router as payees_router
from app.api.settings import router as settings_router
from app.api.transactions import router as transactions_router
from app.api.two_factor import router as two_factor_router
from app.api.user_lookup import router as user_lookup_router
from app.api.workspaces import router as workspaces_router
from app.api.admin import router as admin_router, check_registration_enabled
from app.core.auth import fastapi_users
from app.core.auth_policy import require_local_auth_enabled
from app.core.config import get_settings
from app.core.feature_flags import feature_flag
from app.core.rate_limit import login_rate_limit, register_rate_limit, password_reset_rate_limit
from app.core.redis import close_redis
from app.schemas.user import UserCreate, UserRead, UserUpdate

logger = logging.getLogger(__name__)
settings = get_settings()


async def _warm_tesouro_cache() -> None:
    """Pre-load the Tesouro Direto price cache so the first bond search is
    instant instead of waiting on the cold ~25s CSV download.

    Gated to instances that actually serve Brazilian users (a workspace with
    BRL as its default currency) so a non-Brazilian deployment never calls the
    Brazilian government endpoint just because the feature ships on by default.
    """
    try:
        if not get_settings().tesouro_direto_enabled:
            return
        from sqlalchemy import select

        from app.core.database import async_session_maker
        from app.models.workspace import Workspace

        async with async_session_maker() as session:
            has_brl = await session.scalar(
                select(Workspace.id).where(Workspace.default_currency == "BRL").limit(1)
            )
        if not has_brl:
            return

        from app.providers.tesouro_direto import get_tesouro_direto_provider

        await get_tesouro_direto_provider().get_available_bonds()
        logger.info("Startup: warmed Tesouro Direto price cache")
    except Exception:
        logger.exception("Startup: Tesouro Direto cache warm failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: dispatch sync for all stale bank connections
    try:
        from app.worker import celery_app  # noqa: F811

        celery_app.send_task("app.tasks.sync_tasks.sync_all_connections")
        logger.info("Startup: dispatched sync_all_connections task to Celery")
    except Exception:
        logger.exception("Startup: failed to dispatch sync task")
    # Background pre-warm of the Tesouro cache (non-blocking; gated to BRL
    # instances inside the helper). Kept on app.state so it isn't GC'd.
    app.state.tesouro_warm_task = asyncio.create_task(_warm_tesouro_cache())
    yield
    # Shutdown
    await close_redis()


app = FastAPI(
    title=settings.app_name,
    openapi_url="/api/openapi.json",
    docs_url="/api/docs",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Auth routes — custom login/logout with 2FA support (mounted first to take precedence)
app.include_router(
    custom_auth_router,
    prefix="/api/auth",
    tags=["auth"],
    dependencies=[Depends(login_rate_limit)],
)
app.include_router(
    two_factor_router,
    prefix="/api/auth",
    tags=["auth"],
)
app.include_router(
    passkeys_router,
    prefix="/api/auth",
    tags=["auth"],
)
app.include_router(oidc_auth_router)
app.include_router(
    fastapi_users.get_register_router(UserRead, UserCreate),
    prefix="/api/auth",
    tags=["auth"],
    dependencies=[
        Depends(require_local_auth_enabled),
        Depends(check_registration_enabled),
        Depends(register_rate_limit),
    ],
)
app.include_router(
    fastapi_users.get_reset_password_router(),
    prefix="/api/auth",
    tags=["auth"],
    dependencies=[Depends(require_local_auth_enabled), Depends(password_reset_rate_limit)],
)
# user_lookup must precede the fastapi-users router below so the
# `/api/users/lookup` path isn't captured by the catch-all `/{id}`
# route fastapi-users mounts.
app.include_router(user_lookup_router)
app.include_router(
    fastapi_users.get_users_router(UserRead, UserUpdate),
    prefix="/api/users",
    tags=["users"],
)

# Domain routes
app.include_router(categories_router)
app.include_router(category_groups_router)
app.include_router(rules_router)
app.include_router(reconciliation_router)
app.include_router(transactions_router)
app.include_router(import_router)
app.include_router(import_logs_router)
app.include_router(accounts_router)
app.include_router(connections_router)
app.include_router(recurring_router)
app.include_router(budgets_router)
app.include_router(goals_router)
app.include_router(groups_router)
app.include_router(assets_router)
app.include_router(asset_groups_router)
app.include_router(collections_router)
app.include_router(dashboard_router)
app.include_router(reports_router)
app.include_router(search_router)
app.include_router(setup_router)
app.include_router(currencies_router)
app.include_router(fx_rates_router)
app.include_router(export_router)
app.include_router(attachments_router)
app.include_router(fiscal_router)
app.include_router(payees_router)
app.include_router(invoices_router)
app.include_router(invoice_attachments_router)
app.include_router(public_invoices_router)
app.include_router(settings_router)
app.include_router(workspaces_router)
app.include_router(admin_router)
app.include_router(info_router)


# Optional agents/MCP/LLM module — fully gated by AGENTS_ENABLED so users
# who don't want this feature pay zero cost (no imports, no routes, no
# background tasks). The module itself is self-contained in app/agents/.
if feature_flag("AGENTS_ENABLED"):
    try:
        from app.agents.api.info import router as agents_info_router
        from app.agents.api.agents import router as agents_router
        from app.agents.api.connections import router as agents_connections_router
        from app.agents.api.conversations import router as agents_conversations_router
        from app.agents.api.chat import router as agents_chat_router
        from app.agents.api.knowledge import router as agents_knowledge_router
        from app.agents.api.mcp_tokens import router as agents_mcp_tokens_router

        # Mount literal-prefix routers (conversations, connections,
        # mcp-tokens) BEFORE the generic agents router so paths like
        # /api/agents/connections don't get captured by /api/agents/{agent_id}.
        app.include_router(agents_info_router)
        app.include_router(agents_connections_router)
        app.include_router(agents_conversations_router)
        app.include_router(agents_mcp_tokens_router)
        app.include_router(agents_router)
        app.include_router(agents_chat_router)
        app.include_router(agents_knowledge_router)
        logger.info("Agents feature enabled — mounted /api/agents routes")
    except Exception:
        logger.exception("Agents feature flag is on but import failed; routes not mounted")


# The MCP signing secret's default in app/agents/config.py, and the one
# docker-compose.yml has been shipping. Both are in the repository, so both
# are useless as signing keys.
_PUBLISHED_PLACEHOLDER_SECRETS = frozenset(
    {"change-me-in-production", "dev-mcp-secret-change-in-production"}
)


# An all-in-one deployment has no separate mcp-server container, so this
# process answers POST /mcp itself. Its own flag, not a rider on
# AGENTS_ENABLED: in the multi-container setup the MCP surface is deliberately
# a separate app on a separately-published port, and turning agents on must
# not silently widen what this API exposes. Off by default — mounting the
# router does import app.agents (mcp_server.auth reads the shared JWT
# settings), so a deployment that doesn't ask for the MCP surface still pays
# nothing for it. Its own try/except: an MCP import failure must not take the
# /api/agents routes down with it, or the other way round.
if feature_flag("AGENTS_MCP_INPROCESS"):
    # Serving /mcp means accepting JWTs, and the signing secret falls back to
    # a placeholder (app/agents/config.py) that is published in this
    # repository. Mounting on one of those would put an endpoint on the API's
    # own port that anyone could mint a token for, so refuse — and say so,
    # because this deployment explicitly asked for the surface. Checked here
    # rather than in the compose file so a deployment that leaves the flag off
    # never has to invent a secret it will not use.
    _mcp_secret = os.getenv("AGENTS_MCP_JWT_SECRET", "").strip()
    if not _mcp_secret or _mcp_secret in _PUBLISHED_PLACEHOLDER_SECRETS:
        logger.error(
            "AGENTS_MCP_INPROCESS is on but AGENTS_MCP_JWT_SECRET is unset or still a "
            "placeholder; refusing to serve POST /mcp. Generate one, e.g. openssl rand -hex 32."
        )
    else:
        try:
            from mcp_server.main import router as mcp_router

            app.include_router(mcp_router)
            logger.info("Serving the built-in MCP server in-process at POST /mcp")
        except Exception:
            logger.exception("AGENTS_MCP_INPROCESS is on but the MCP router failed to import")


@app.get("/api/health")
async def health_check():
    return {"status": "healthy"}


# Serve the built SPA from this process, for the all-in-one image. Inert
# unless FRONTEND_DIST_PATH points at a real directory, so the published
# backend image — which contains no frontend build — behaves exactly as
# before.
#
# `app.frontend()` rather than mounting StaticFiles at "/": a Mount at the
# root matches every path, so an unknown /api/... would be answered with
# index.html and HTTP 200 instead of a JSON 404, and the trailing-slash
# redirect would never be reached. The frontend route is low-priority — it is
# consulted after every real route, after 405 handling, and after the
# redirect — and its index.html fallback only fires for requests that accept
# text/html. A caveat worth knowing: that makes `curl /transactions` a 404,
# because curl sends `Accept: */*`. Health checks should use /api/health.
_frontend_dist = settings.frontend_dist_path.strip()
if _frontend_dist and os.path.isdir(_frontend_dist):
    from app.core.spa import FrontendCacheHeadersMiddleware

    app.add_middleware(FrontendCacheHeadersMiddleware)
    # The isdir() above is the check; check_dir would only re-run it and
    # raise at import time on a race.
    app.frontend("/", directory=_frontend_dist, check_dir=False)
    logger.info("Serving the frontend in-process from %s", _frontend_dist)
elif _frontend_dist:
    logger.warning(
        "FRONTEND_DIST_PATH=%s is not a directory; the frontend is not served",
        _frontend_dist,
    )
