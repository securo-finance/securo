# 0007. Background work on Celery and Redis

- Status: Accepted
- Date: 2026-03-12

## Context

Bank sync, recurring entries, FX rates and market prices have to happen without anyone opening the app, and some of them are slow or rate limited by the services they call.

## Decision

- Jobs run on Celery with Redis as broker and result backend: `celery_app` in `backend/app/worker.py`, JSON serialization, UTC. Compose runs `celery-worker` (concurrency 2) and `celery-beat`.
- The schedule is declared inline in `backend/app/worker.py`:
  - hourly: `sync_all_connections` (each connection at most every `BANK_SYNC_MIN_INTERVAL_HOURS`, default 4, with backoff), `generate_all_recurring`, `generate_recurring_invoices`, `apply_asset_growth_rules`;
  - daily: `refresh_market_prices`;
  - every 12 hours: `sync_fx_rates`, `restamp_recurring_fx`, `restamp_fallback_fx` (no-ops unless `FX_SYNC_MODE=scheduled`);
  - every 5 minutes, only when `PLUGINS_ENABLED`: `sync_plugin_connections`.
- A task runs its async body with `asyncio.run`, creates its own engine and session maker, sets the timezone context (ADR 0009), and disposes the engine in `finally`. It never reuses the request path's engine.
- Scheduled tasks are idempotent: running one twice produces the same rows.

## Consequences

- Redis is a required service (it also backs rate limiting, ADR 0012).
- The worker does not hot reload; after changing task code, restart `celery-worker`.
- A new periodic job is added to the beat schedule, gated by its capability's flag when it belongs to one (ADR 0004).

## Enforced by

`tests/test_sync_tasks.py`, `tests/test_bank_sync_interval.py`, `tests/test_sync_backoff.py`. Idempotence is tested per task, not as a general rule.
