# Architecture decision records

Each file here records one decision about how Securo is built: what we chose, why, and what it asks of the code that comes after. They are short on purpose. Read the ones that touch the area you are changing before you change it, and cite them (`ADR 0002`) in code comments and pull requests when the reason for a piece of code lives here.

## Rules

- **One decision per file**, numbered in order: `NNNN-short-title.md`. Copy `0000-template.md`.
- **A merged ADR is not rewritten.** When a decision changes, add a new ADR that supersedes the old one and set the old one's status to `Superseded by NNNN`. Fixing a typo or a stale file path is fine.
- **A new architecture decision, or a change to one, comes with an ADR in the same pull request.** That covers a new table that breaks a pattern below, a new external call, a new background job family, a new optional service, a change to how money, time, access or storage work.
- **Name the code.** An ADR points at the files and functions that carry it, and at the tests that keep it true. A decision no test enforces says so under "Enforced by".
- Cited paths are checked in CI (`.github/ci/check_doc_paths.py`): a renamed file fails the build until the ADR names its new place.
- ADRs 0001 to 0018 were written after the fact, from decisions already in the code. Their date is when the decision landed; the pull request that brought it is listed as its origin.

## Index

| ADR | Decision |
|-----|----------|
| [0001](0001-record-architecture-decisions.md) | Record architecture decisions |
| [0002](0002-workspace-is-the-data-boundary.md) | The workspace is the data boundary |
| [0003](0003-workspace-roles-and-write-gates.md) | Workspace roles, and a gate on every write route |
| [0004](0004-optional-capabilities.md) | Optional capabilities: one switch, off by default, free when off |
| [0005](0005-modules-per-workspace.md) | Modules are resolved per workspace |
| [0006](0006-bank-sync-providers.md) | Bank sync providers behind one interface, available when configured |
| [0007](0007-background-work-on-celery.md) | Background work on Celery and Redis |
| [0008](0008-money-and-currencies.md) | Money is exact, every row keeps its own currency |
| [0009](0009-time-and-today.md) | Timestamps in UTC, "today" in the workspace's timezone |
| [0010](0010-linear-numbered-migrations.md) | One linear chain of numbered migrations, applied before anything starts |
| [0011](0011-logic-lives-in-services.md) | Logic lives in services, routers stay thin |
| [0012](0012-authentication.md) | Authentication: sessions, OIDC, passkeys, TOTP and scoped access tokens |
| [0013](0013-file-storage.md) | Files go through a storage provider (superseded by 0019) |
| [0014](0014-ai-agents-module.md) | AI agents are an optional module |
| [0015](0015-plugins.md) | Plugins: data, sandboxed code and themes |
| [0016](0016-outbound-calls-and-no-telemetry.md) | Every outbound call is gated, and there is no telemetry |
| [0017](0017-category-lines.md) | Category lines split a transaction's category, the row stays as the bank reports it |
| [0018](0018-transfers-and-card-accounting.md) | Transfers are matched by a visible policy; card accounting mode moves reports, never balances |
| [0019](0019-s3-storage.md) | Files can live in an S3-compatible bucket, from the regular image |
| [0020](0020-instance-secrets-in-the-database.md) | An install with no secret of its own gets one, kept in the database |
| [0021](0021-plugin-icons.md) | Plugin icons come from the catalog by checksum, served by Securo |
| [0022](0022-provider-reserved-balances.md) | Provider reserved balances are shown, never summed into a total |
| [0023](0023-new-bank-providers-are-plugins.md) | New bank sync providers are plugins; the core keeps the pipeline |
| [0024](0024-client-ip-behind-trusted-proxies.md) | The client IP is read through trusted proxies only |
| [0025](0025-performance-benchmark-sources.md) | Performance benchmarks come from Yahoo Finance, the Central Bank of Brazil and Ipeadata |
| [0026](0026-plugin-icons-outside-the-catalog.md) | Installed plugins show their catalog icon, read from the catalog only while one is on screen |
| [0027](0027-amount-in-account-currency.md) | A row's value in its account's currency has its own column |
| [0028](0028-plugin-reports.md) | Plugins add reports: a query Securo runs, or their own code in the runner, drawn by Securo |
| [0029](0029-plugin-report-total-chart-points.md) | A plugin report view is bounded in its total chart points |
| [0030](0030-plugin-report-period-window.md) | A plugin report reads at most 731 days, and the tab trims its window to that |
| [0031](0031-typed-primary-amount.md) | A converted amount the user typed is kept apart from the display-currency cache |
| [0032](0032-viewer-display-currency.md) | Members of a shared workspace read stamps in their own display currency |
| [0034](0034-plugin-dashboard-widgets.md) | Plugins add dashboard widgets: a report sized for a card, over the month on screen |
| [0035](0035-plugin-file-fields-and-routines.md) | Plugin integrations take files in their form and run routines on schedules of their own |
| [0036](0036-xml-attachments.md) | XML files can be attached, and anything that isn't an image or a PDF is served as a download |
| [0037](0037-document-reading.md) | Documents are read into transactions by an LLM, reviewed before anything is saved |
| [0038](0038-plugin-connection-recovers-with-new-values.md) | A plugin connection is fixed in place with new values, and a routine-only one proves them at once |
| [0039](0039-date-move-keeps-typed-stamp.md) | A move of the date alone keeps the amount the owner typed |
| [0040](0040-viewer-currency-reads-own-amount.md) | A row already in the viewer's currency reads as its own amount |
| [0041](0041-in-app-notifications.md) | Notifications live inside the app, one row per person, and the first kinds are read from stored state |
| [0042](0042-admin-actions-on-accounts-leave-a-record.md) | An administrator manages accounts, never becomes one, and every action leaves a record the affected side can read |
| [0043](0043-row-security.md) | Postgres can hold the workspace boundary too |
| [0044](0044-email-password-recovery.md) | Password recovery goes out over the instance's own SMTP server, behind one switch |
| [0045](0045-managed-instance.md) | A managed instance keeps its own settings read-only for the customer's admin |
| [0046](0046-email-provider-and-templates.md) | The mail path is SMTP or a hosted API, and an admin may rewrite the recovery message |
| [0047](0047-deleting-a-user-keeps-workspace-data.md) | Deleting a user keeps what they added to other people's workspaces |
| [0048](0048-required-secret-key.md) | An operator can require a secret key of their own |
| [0049](0049-hosted-usage-quotas.md) | A managed instance can limit each account with four neutral counters |
| [0050](0050-instance-settings-leave-a-record.md) | Instance settings an administrator changes leave a record, with no account as its target |
