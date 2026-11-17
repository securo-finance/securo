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
| [0013](0013-file-storage.md) | Files go through a storage provider |
| [0014](0014-ai-agents-module.md) | AI agents are an optional module |
| [0015](0015-plugins.md) | Plugins: data, sandboxed code and themes |
| [0016](0016-outbound-calls-and-no-telemetry.md) | Every outbound call is gated, and there is no telemetry |
| [0017](0017-category-lines.md) | Category lines split a transaction's category, the row stays as the bank reports it |
| [0018](0018-transfers-and-card-accounting.md) | Transfers are matched by a visible policy; card accounting mode moves reports, never balances |
