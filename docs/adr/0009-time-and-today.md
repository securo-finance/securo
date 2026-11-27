# 0009. Timestamps in UTC, "today" in the workspace's timezone

- Status: Accepted
- Date: 2026-09-23
- Origin: #909 (instance timezone), #1010 (workspace timezone)

## Context

Recurring entries, bill cycles and "this month" depend on what day it is, and a server in UTC disagrees with a person in São Paulo for three hours every night. A job that asks for "today" twice can also see the date change between the two calls.

## Decision

- Persisted timestamps are UTC.
- "Today" comes from `backend/app/core/app_clock.py` (`app_today`, `today_in`), resolved in this order: the workspace's timezone, the instance timezone set by an admin, the `TZ` variable, the host, UTC.
- The resolved timezone is kept in a context variable for the whole operation: the request (set by `current_workspace`) or the task (set at its start, ADR 0007).

## Consequences

- Code never calls `date.today()` or `datetime.now()` to decide which day it is; it calls `app_today()`.
- Tests that depend on the date set the timezone context explicitly.

## Enforced by

`tests/test_app_timezone.py`, `tests/test_invoice_schedule_timezone.py`. Nothing forbids `date.today()` in services.
