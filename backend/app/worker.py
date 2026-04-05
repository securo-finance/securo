from celery import Celery
from celery.schedules import crontab

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "securo",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="America/Sao_Paulo",
    enable_utc=True,
)

celery_app.conf.beat_schedule = {
    "sync-all-connections-daily": {
        "task": "app.tasks.sync_tasks.sync_all_connections",
        "schedule": crontab(hour=4, minute=0),
    },
    "generate-recurring-daily": {
        "task": "app.tasks.recurring_tasks.generate_all_recurring",
        "schedule": 60 * 60,  # every hour; generate_pending is idempotent (advances next_occurrence)
    },
    "apply-asset-growth-daily": {
        "task": "app.tasks.asset_tasks.apply_asset_growth_rules",
        "schedule": 60 * 60,  # every hour; idempotent (checks last value date)
    },
    "sync-fx-rates-daily": {
        "task": "app.tasks.fx_rate_tasks.sync_fx_rates",
        "schedule": 60 * 60 * 12,  # twice daily (~60 API calls/month)
    },
    "restamp-recurring-fx-daily": {
        "task": "app.tasks.fx_rate_tasks.restamp_recurring_fx",
        "schedule": 60 * 60 * 12,  # twice daily, after FX rate sync
    },
}

celery_app.conf.include = [
    "app.tasks.sync_tasks",
    "app.tasks.recurring_tasks",
    "app.tasks.asset_tasks",
    "app.tasks.fx_rate_tasks",
]
