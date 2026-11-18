# 0011. Logic lives in services, routers stay thin

- Status: Accepted
- Date: 2026-03-10

## Context

The same rule (what counts as income, when a bill is due, how a transfer is matched) is needed by routes, scheduled tasks, imports, the MCP server and plugins. Logic written inside a route can't be reused by any of them, and gets written again slightly differently.

## Decision

- `backend/app/api/*.py` routers parse the request, resolve the workspace and permissions (ADR 0002, 0003), call a service, and shape the response with a schema from `backend/app/schemas/`.
- Business rules live in `backend/app/services/`. Tasks, the MCP server and plugins call the same services.
- Shared definitions have one home. What counts toward income and expenses (confirmed, not a transfer, not future, which date it reports on, category lines) is in `backend/app/services/_query_filters.py`, and reports, the dashboard and budgets import it from there.

## Consequences

- A new rule goes in a service, with a test at the service level.
- Changing what a report counts means changing `_query_filters.py`, and every screen moves together.
- Some older routers still query the database directly. New code doesn't follow them; moving them is welcome in focused pull requests.

## Enforced by

Not mechanically: ruff doesn't check imports between layers. `tests/test_category_parts_structure.py` checks that aggregation call sites choose between the line category and the row category (ADR 0017).
