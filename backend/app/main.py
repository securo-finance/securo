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
from app.api.import_transactions import router as import_router
from app.api.info import router as info_router
from app.api.recurring_transactions import router as recurring_router
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
from app.api.payees import router as payees_router
from app.api.settings import router as settings_router
from app.api.transactions import router as transactions_router
from app.api.two_factor import router as two_factor_router
from app.api.user_lookup import router as user_lookup_router
from app.api.workspaces import router as workspaces_router
from app.api.admin import router as admin_router, check_registration_enabled
from app.core.auth import fastapi_users
from app.core.config import get_settings
from app.core.rate_limit import login_rate_limit, register_rate_limit, password_reset_rate_limit
from app.core.redis import close_redis
from app.schemas.user import UserCreate, UserRead, UserUpdate

logger = logging.getLogger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: dispatch sync for all stale bank connections
    try:
        from app.worker import celery_app  # noqa: F811

        celery_app.send_task("app.tasks.sync_tasks.sync_all_connections")
        logger.info("Startup: dispatched sync_all_connections task to Celery")
    except Exception:
        logger.exception("Startup: failed to dispatch sync task")
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
    fastapi_users.get_register_router(UserRead, UserCreate),
    prefix="/api/auth",
    tags=["auth"],
    dependencies=[Depends(check_registration_enabled), Depends(register_rate_limit)],
)
app.include_router(
    fastapi_users.get_reset_password_router(),
    prefix="/api/auth",
    tags=["auth"],
    dependencies=[Depends(password_reset_rate_limit)],
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
app.include_router(payees_router)
app.include_router(settings_router)
app.include_router(workspaces_router)
app.include_router(admin_router)
app.include_router(info_router)


# Optional agents/MCP/LLM module — fully gated by AGENTS_ENABLED so users
# who don't want this feature pay zero cost (no imports, no routes, no
# background tasks). The module itself is self-contained in app/agents/.
if os.getenv("AGENTS_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on"):
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


@app.get("/api/health")
async def health_check():
    return {"status": "healthy"}
