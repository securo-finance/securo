import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.accounts import router as accounts_router
from app.api.budgets import router as budgets_router
from app.api.savings import router as savings_router 
from app.api.categories import router as categories_router
from app.api.category_groups import router as category_groups_router
from app.api.connections import router as connections_router
from app.api.dashboard import router as dashboard_router
from app.api.import_logs import router as import_logs_router
from app.api.import_transactions import router as import_router
from app.api.recurring_transactions import router as recurring_router
from app.api.rules import router as rules_router
from app.api.setup import router as setup_router
from app.api.transactions import router as transactions_router
from app.core.auth import auth_backend, fastapi_users
from app.core.config import get_settings
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

# Auth routes
app.include_router(
    fastapi_users.get_auth_router(auth_backend),
    prefix="/api/auth",
    tags=["auth"],
)
app.include_router(
    fastapi_users.get_register_router(UserRead, UserCreate),
    prefix="/api/auth",
    tags=["auth"],
)
app.include_router(
    fastapi_users.get_reset_password_router(),
    prefix="/api/auth",
    tags=["auth"],
)
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
app.include_router(dashboard_router)
app.include_router(setup_router)
app.include_router(savings_router)


@app.get("/api/health")
async def health_check():
    return {"status": "healthy"}
