# Securo

An open-source, self-hosted personal finance manager. This file is for contributors and their coding agents: the rules the code keeps and where each feature lives. Start with `README.md` and `CONTRIBUTING.md`. The reasons behind the rules are in `docs/adr/` (cited below as ADR NNNN); read the ones that touch the area you are changing.

## Stack

FastAPI, SQLAlchemy (async), Alembic and Celery on Redis in `backend/`; React 19, TypeScript, Vite, Tailwind 4, TanStack Query and react-router in `frontend/`; PostgreSQL with the `vector` extension. Docker Compose runs everything: frontend on :3000, backend on :8000.

## Rules the code keeps

- **The workspace is the data boundary** (ADR 0002). Financial tables carry `workspace_id`; routes take `WorkspaceContext` from `current_workspace` or `current_writable_workspace`; services filter by `ctx.id`. Queries that read rows shared from other workspaces add `in_bound_workspace(...)`. New code never relies on `workspace_autostamp`.
- **Every mutating route has a write gate** (ADR 0003): it depends on `current_writable_workspace`, directly or through `require_module_write`, or sits in the test's `ALLOWLIST` with a reason. `tests/test_write_permission_coverage.py` fails otherwise. Roles are `owner`, `editor`, `viewer`, plus the virtual `manager`.
- **Optional means one switch, off by default, free when off** (ADR 0004): a setting in `backend/app/core/config.py`, no imports, routes, jobs or containers when off (Compose profiles for services), documented in `.env.example` and the README.
- **Feature areas are modules** (ADR 0005), listed in `module_service.py` and `frontend/src/lib/modules.ts`; their routes use `require_module` or `require_module_write`.
- **Logic lives in services** (ADR 0011). Routers parse, check access, call a service, return a schema. What counts as income or expense is defined once, in `backend/app/services/_query_filters.py`.
- **Money is exact** (ADR 0008): `Numeric(15, 2)` and `Decimal`, never `float`. Every row keeps its `currency`; cross-currency sums use `amount_primary`.
- **"Today" comes from `app_today()`** (ADR 0009), never `date.today()`. Stored timestamps are UTC.
- **Migrations are one numbered line** (ADR 0010): next number after the head, `down_revision` the previous one; a merged migration is never edited.
- **Every outbound call is gated, and there is no telemetry** (ADR 0016). A new external call needs a switch and a superseding ADR for the list.
- **Files go through `get_storage_provider()`** with keys that start with the workspace id (ADR 0013).
- **New routes are closed to access tokens** until mapped in `backend/app/core/access_token_scopes.py` (ADR 0012).
- **Every user-facing string is translated.** Add each key to `frontend/src/locales/en.json` and every other locale file; `src/locales/i18n.test.ts` fails on a missing or extra key or a placeholder mismatch.
- **A new architecture decision, or a change to one, comes with an ADR** (`docs/adr/README.md`).
- **Conventional commits** (`feat:`, `fix:`, `docs:`, `refactor:`), one feature or fix per pull request, backend tests for new behavior.

## Commands

```bash
docker compose up --build                 # full stack; migrations run in the one-shot `migrate` service
docker compose run --rm migrate           # apply a new migration to a running stack
docker compose restart celery-worker      # the worker does not hot reload

cd backend && uv sync --all-extras        # first time
uv run pytest                             # CI requires coverage >= 60%
uv run pytest tests/test_some_file.py     # one file while fixing
uv run pytest tests/api_contract -n auto  # route contract sweep
uv run ruff check . && uv run ty check .
./scripts/lock.sh                         # after changing dependencies; commit uv.lock

cd ../frontend && npm run lint && npm run build && npm test
```

Run lint, type check and the tests you touched before calling work done; CI runs the rest.

## Tests

- **Backend** (`backend/tests/conftest.py`): in-memory SQLite by default, schema from `Base.metadata.create_all` (not from migrations), pgvector shimmed to JSON, Redis and external FX sync mocked, `AGENTS_ENABLED` forced on. Fixtures: `session`, `client`, `test_user`, `test_workspace`, `auth_headers`, `viewer_auth_headers`, `admin_auth_headers`, `test_account`, `test_transactions`, `test_categories`, `test_rules`. Files matching `tests/*_postgres.py` use `postgres_sessions` and need `POSTGRES_TEST_URL` (skipped locally without it, required in CI).
- **Contract sweep** (`backend/tests/api_contract/`): every OpenAPI route and method against the expected status in `baseline.json`, as owner and as viewer.
- **Frontend** (`frontend/vitest.config.ts`): a `node` project for `src/lib/*.test.ts` and a jsdom `dom` project for the rest, setup in `src/test/setup.ts`. Render with `renderWithProviders` from `src/test/utils.tsx` (real English strings). The API is stubbed with `vi.mock('@/lib/api', ...)`; there is no MSW. `src/pages/pages.contract.test.ts` and `src/components/components.contract.test.ts` import every page and component to catch modules that break on import.

## Map: backend

Paths are under `backend/`.

- **Wiring**: routers included in `app/main.py`; Celery app and beat schedule in `app/worker.py` (ADR 0007); CLI `app/cli.py`; settings `app/core/config.py` (`get_settings`), flags `app/core/feature_flags.py`; database `app/core/database.py` (`Base`, `get_async_session`); rate limits `app/core/rate_limit.py`.
- **Workspace context**: `app/core/workspace_context.py` (`current_workspace`, `current_writable_workspace`, `WorkspaceContext`), `app/core/shared_scope.py` (`in_bound_workspace`), `app/core/module_gate.py`, `app/core/app_clock.py` (`app_today`, `today_in`).
- **Workspaces, members, modules**: router `app/api/workspaces.py`; `app/services/workspace_service.py` (`require_membership`, `add_member`, `update_member_role`, `archive_workspace`), `app/services/module_service.py` (`resolve_modules`); model `app/models/workspace.py`. Tests `tests/test_workspace*.py`, `tests/test_module_*.py`, `tests/test_write_permission_*.py`.
- **Auth**: `app/core/auth.py` (fastapi-users, JWT, `current_active_user`); routers `app/api/custom_auth.py` (login, 2FA step), `oidc_auth.py`, `passkeys.py` (+ `app/core/webauthn.py`), `two_factor.py`, `access_tokens.py`, `setup.py`; `app/services/access_token_service.py`, scopes `app/core/access_token_scopes.py`, OIDC-only switch `app/core/auth_policy.py`. Tests `tests/test_auth_api.py`, `test_oidc_auth.py`, `test_passkeys.py`, `test_two_factor.py`, `test_access_tokens.py`.
- **Accounts and balances**: router `app/api/accounts.py`; `app/services/account_service.py` (`get_account_with_balances`, `get_running_balances`, `get_account_balance_history`, `close_account`); model `app/models/account.py`. (`balance_service.py` is group balances, not accounts.) Tests `tests/test_account*.py`.
- **Credit cards and bills**: `app/services/credit_card_service.py` (`get_cycle_dates`, `compute_effective_date`), bills through `account_service.get_credit_card_bills`; model `app/models/credit_card_bill.py`; accounting mode in `app/services/admin_service.py` (ADR 0018). Tests `tests/test_credit_card_accounting_mode.py`, `tests/test_accounts_shared_line_bills.py`.
- **Loans and mortgages**: router `app/api/mortgage_payments.py`; `app/services/loan_schedule.py` (`build_schedule`), `app/services/mortgage_service.py`, `account_service.get_loan_schedule`. Tests `tests/test_loan_*.py`, `tests/test_mortgage_payments_*.py`.
- **Transactions**: router `app/api/transactions.py` (CRUD, calendar, transfers, counterparts); `app/services/transaction_service.py` (`create_transaction`, `update_transaction`, `create_transfer`, `create_installment_series`, `bulk_update_category`); model `app/models/transaction.py`. Tests `tests/test_transaction*.py`, `tests/test_transfer_api.py`.
  - Splits between people: `app/services/split_service.py` (`replace_splits`), model `transaction_split.py`.
  - Category lines (ADR 0017): `app/services/category_line_service.py` (`replace_lines`, `materialize`), models `transaction_category_line.py`, `recurring_transaction_category_line.py`. Tests `tests/test_category_lines_*.py`, `tests/test_category_parts_structure.py`.
  - Transfer detection (ADR 0018): `app/services/transfer_detection_service.py` (`detect_transfer_pairs`), exposed in `app/api/connections.py`; policy `app/services/reconciliation_policy.py`.
  - Attachments: router `app/api/attachments.py`, `app/services/attachment_service.py`. Calendar: `transaction_calendar_service.py`. Matching: `transaction_match_service.py`, `text_similarity.py`.
- **Import**: router `app/api/import_transactions.py` (`/api/transactions/import/*`); `app/services/import_service.py` (`parse_csv`, `parse_ofx`, `parse_qif`, `parse_camt`, `import_transactions`); undo through `app/api/import_logs.py`. Tests `tests/test_import_*.py`.
- **Categories**: routers `app/api/categories.py`, `category_groups.py`; `app/services/category_service.py`, `category_group_service.py`, `category_defaults.py` (localized defaults), `category_statement_service.py`. Tests `tests/test_categor*.py`.
- **Payees**: router `app/api/payees.py`; `app/services/payee_service.py` (`get_or_create_payee`, `merge_payees`). Tests `tests/test_payee*.py`.
- **Rules**: router `app/api/rules.py`; `app/services/rule_service.py` (`apply_rules_to_transaction`, `apply_all_rules`, `preview_rule`), `rule_engine.py` (`evaluate_conditions`, `apply_rule_actions`), `rule_packs.py` (packs in `app/data/rule_packs/`). Tests `tests/test_rule*.py`.
- **Recurring**: router `app/api/recurring_transactions.py`; `app/services/recurring_transaction_service.py` (`generate_pending`), `recurring_detector_service.py`, `recurring_match_service.py`; task `app/tasks/recurring_tasks.py`. Tests `tests/test_recurring*.py`.
- **Budgets and goals**: routers `app/api/budgets.py`, `goals.py`; `app/services/budget_service.py` (`get_budget_vs_actual`, `get_budget_rollover_summary`, `get_budget_suggestions`), `goal_service.py`. Tests `tests/test_budget*.py`, `tests/test_goal*.py`.
- **Reports, dashboard, forecast**: routers `app/api/reports.py`, `dashboard.py`, `forecast.py`; `app/services/report_service.py`, `dashboard_service.py`, `cashflow_forecast_service.py`; income and expense semantics in `app/services/_query_filters.py`. Tests `tests/test_report*.py`, `tests/test_dashboard*.py`, `tests/test_cashflow_forecast.py`.
- **Search**: router `app/api/search.py`, `app/services/search_service.py` (`search_all`).
- **Assets**: routers `app/api/assets.py`, `asset_groups.py`; `app/services/asset_service.py`, `asset_transaction_service.py`, `asset_import_service.py`; prices `app/providers/market_price.py`, `tesouro_direto.py`; task `app/tasks/asset_tasks.py`. Tests `tests/test_asset*.py`, `tests/test_tesouro_direto_*.py`.
- **Collections**: router `app/api/collections.py`, `app/services/collection_service.py`.
- **Shared expense groups**: router `app/api/groups.py`; `app/services/group_service.py`, `settlement_service.py`, `group_summary_service.py`, `balance_service.py` (`compute_balances`). Tests `tests/test_group*.py`, `tests/test_settlement_service*.py`.
- **Bank connections and sync** (ADR 0006): router `app/api/connections.py`; `app/services/connection_service.py` (`sync_connection`, `handle_oauth_callback`), `provider_settings.py`; providers `app/providers/base.py`, `pluggy.py`, `enable_banking.py`, `simplefin.py`, registry `app/providers/__init__.py`; task `app/tasks/sync_tasks.py`. Tests `tests/test_connection*.py`, `tests/test_providers*.py`, `tests/test_sync_*.py`.
- **FX rates** (ADR 0008): routers `app/api/fx_rates.py`, `currencies.py`; `app/services/fx_rate_service.py` (`sync_rates`, `convert`, `stamp_primary_amount`); provider `app/providers/openexchangerates.py`; task `app/tasks/fx_rate_tasks.py`. Tests `tests/test_fx_rates.py`.
- **Reconciliation**: router `app/api/reconciliation.py`; `app/services/reconciliation_engine.py` (`evaluate`, pure), `reconciliation_service.py`, `reconciliation_policy.py`, `reconciliation_rule_service.py`, `reconciliation_suggestion_service.py`, `reconciliation_history_service.py`. Tests `tests/test_reconciliation*.py`.
- **Invoices**: routers `app/api/invoices.py`, `invoice_attachments.py`, `invoice_schedules.py`, `products.py`, `public_invoices.py` (shared link, no auth); `app/services/invoice_service.py` (`issue_invoice`, `allocate`, `derive_state`), `invoice_schedule_service.py` (`generate_due`), `product_service.py`, `invoice_document.py`, `invoice_pdf.py`; task `app/tasks/invoice_schedule_tasks.py`. Tax ids by country: `app/fiscal/` (packs in `app/fiscal/jurisdictions/*.toml`). Tests `tests/test_invoice*.py`, `tests/test_fiscal_*.py`.
- **Admin and settings**: router `app/api/admin.py` (users, instance settings, provider settings, timezone); `app/services/admin_service.py`; widget layouts `app/api/ui_layouts.py`. Tests `tests/test_admin_*.py`.
- **Export and backup**: router `app/api/export.py`, `app/services/backup_service.py`. Test `tests/test_export_api.py`.
- **Storage** (ADR 0013): `app/providers/storage.py`, `local_storage.py`, `get_storage_provider` in `app/providers/__init__.py`.
- **Plugins** (ADR 0015): router `app/api/plugins.py`; `app/plugins/` (`service.py`, `manifest.py`, `index.py`, `sync.py`, `runner.py`, `vault.py`, `provenance.py`); sandbox `plugin_runner/`; task `app/tasks/plugin_tasks.py`. Tests `tests/test_*_plugins.py`, `tests/test_plugin_runner.py`.
- **AI agents** (ADR 0014): `app/agents/` (`app/agents/api/`, `app/agents/services/`, `app/agents/runtime/executor.py`, `app/agents/providers/`, `app/agents/mcp/client.py`, `app/agents/tasks/ingest.py`); MCP server `mcp_server/` (`mcp_server/registry.py`, `mcp_server/auth.py`, `mcp_server/tools/`). Tests `tests/test_agents_*.py`, `tests/test_mcp_*.py`.
- **Migrations**: `alembic/versions/NNN_name.py`; chain check `scripts/check_migration_chain.py`.

Every path cited in this file and in `docs/adr/` is checked by `.github/ci/check_doc_paths.py` in CI: when you rename or move a file, update the map in the same pull request.

## Map: frontend

Paths are under `frontend/src/`.

- **Routes**: all in `App.tsx`, pages lazy loaded. Guards in `components/`: `protected-route.tsx`, `admin-route.tsx`, `module-route.tsx`, `agents-route.tsx`, `plugins-route.tsx`.
- **Shell**: `components/app-layout.tsx` (sidebar, header, user menu, command palette, global chat, onboarding tour, update banner); sidebar items `lib/nav-items.ts` (`visibleNavItems`); `components/workspace-switcher.tsx`, `collection-selector.tsx`, `command-palette.tsx`.
- **Contexts** (`contexts/`): `useAuth()` (user, token, login, logout), `useWorkspace()` (current workspace, role, `canWrite`, `hasModule`, `switchWorkspace`), `useCollectionFilter()` (active collection's accounts).
- **API client**: `lib/api.ts`, one axios instance on `/api` with `Authorization` and `X-Workspace-Id` headers, grouped by resource (`api.transactions.*`, `api.budgets.*`, ...). DTO types in `types/index.ts`. Error messages in `lib/api-errors.ts`, `lib/api-error-translations.ts`.
- **Queries**: keys are inline arrays named after the resource (`['accounts']`, `['groups', id, 'balances']`); shared invalidation in `lib/invalidate-queries.ts` (`invalidateFinancialQueries`, `invalidateCategoryQueries`). A workspace switch resets the whole cache.
- **Formatting**: money and numbers `lib/format.ts` (`formatCurrency`, `parseAmountInput`); dates `lib/date-utils.ts`, `lib/report-period.ts`; privacy mode `hooks/use-privacy-mode.ts`.
- **i18n**: setup `lib/i18n.ts` (en and pt-BR bundled, others lazy); strings in `locales/*.json`, one `translation` namespace keyed by feature; parity test `locales/i18n.test.ts`.
- **UI kit**: `components/ui/` (shadcn style on Radix: button, dialog, select, popover, table, tabs, tooltip, sonner toasts, ...); shared pickers `category-select.tsx`, `currency-select.tsx`, `payee-select.tsx`, `icon-picker.tsx`. Theme `components/theme-provider.tsx`, tokens `index.css` and `lib/themes.ts`. Icon-only buttons need an accessible label (jsx-a11y rule is an error).
- **Charts**: Recharts in the pages that use them and `components/dashboard-widgets.tsx`; Sankey `components/reports/CashflowSankey.tsx`. Widget catalog and layouts `lib/library.ts`, `hooks/use-layout.ts`.
- **Pages** (`pages/`), each with its components in `components/`:
  - Dashboard `dashboard.tsx` (`widget-grid.tsx`, `dashboard-widgets.tsx`, `transaction-drill-down.tsx`).
  - Transactions `transactions.tsx` (`transaction-dialog.tsx`, `transactions-filter-bar.tsx`, `transfer-dialog.tsx`, `transaction-category-lines-section.tsx`, `transaction-attachments.tsx`).
  - Accounts `accounts.tsx`, `account-detail.tsx` (`bank-connect-dialog.tsx`, `loan-schedule-card.tsx`); bank OAuth return `oauth-callback.tsx`.
  - Import `import.tsx` (`import-review-table.tsx`, `import-history.tsx`, `asset-import-panel.tsx`).
  - Rules and reconciliation `rules.tsx` (`rule-dialog.tsx`, `components/reconciliation-*.tsx`).
  - Categories `categories.tsx`; payees `payees.tsx`; collections `collections.tsx`; budgets `budgets.tsx`; goals `goals.tsx`; recurring `recurring.tsx`.
  - Assets `assets.tsx`; reports `reports.tsx` (`components/reports/`).
  - Shared expense groups `groups.tsx`, `group-detail.tsx`.
  - Invoices `invoices.tsx`, `invoice-detail.tsx`, `invoice-schedules.tsx`, `invoice-schedule-detail.tsx`, `products.tsx` (`invoice-ui.tsx`, `invoice-line-editor.tsx`, `invoice-document.tsx`); public shared invoice `shared-invoice.tsx` (`/i/:token`, no auth).
  - Workspace settings `workspace-settings.tsx`; admin `pages/admin/settings.tsx` (`components/provider-settings*.tsx`).
  - Plugins `plugins.tsx`, `plugin-detail.tsx` (`components/plugin-*.tsx`); agents `agents-list.tsx`, `agent-detail.tsx`, `agent-connections.tsx` (`components/agents/`, streaming `lib/agents-stream.ts`).
  - Auth `login.tsx`, `register.tsx`, `setup.tsx` (first run), `oidc-callback.tsx`.
- **Onboarding tour**: `components/onboarding-tour.tsx`, targets marked with `data-tour="..."`.
