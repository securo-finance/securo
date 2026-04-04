# Flux

## What This Is

Flux is a self-hosted personal finance web application for personal use, with responsive mobile usage treated as a first-class constraint. After shipping `v1.1`, the product now operates on an explicit monthly-control model built around one editable `Mês Atual` plus preserved closed-month snapshots.

## Core Value

Manage personal finances clearly on a self-hosted app that remains practical to use on mobile.

## Current State

- Shipped milestone: `v1.1 Gestão Mensal` on 2026-04-04
- Current milestone: `v1.2 Gestão de contas e faturas`
- Active product shape: explicit `Mês Atual`, durable `monthly_periods`, preserved `monthly_snapshots`, protected-history navigation, and categories available as setup metadata even before `Mês Atual` exists
- Planning state: requirements and roadmap defined for the account-and-bill-management milestone

## Current Milestone: v1.2 Gestão de contas e faturas

**Goal:** Rework account and Pluggy connection management so Flux imports only credit-card bill transactions into the current month model.

**Target features:**
- Remove wallets from the supported product model
- Remove account balances from the supported runtime and treat the monthly result as `receitas - despesas`
- Redefine `Contas` as Pluggy-connected accounts only
- Allow connected-account management with rename, bill-import toggle, and delete actions
- Import only credit-card bill transactions from Pluggy
- Run daily bill ingestion for the current month
- Persist imported bill transactions in app-owned storage linked to card and `Mês Atual`
- Keep imported bill transactions uncategorized unless a user-defined rule applies

## Requirements

### Validated

- ✓ Flux branding is consistent across the application shell, setup/auth copy, and export filenames — `v1.0`
- ✓ Categories can be created and edited without category-group dependency in the supported UI or API contract — `v1.0`
- ✓ Budget enablement and amount are managed from the category flow with an explicit unbudgeted state — `v1.0`
- ✓ Budget readers, reports, and exports consume the category-owned budget model consistently — `v1.0`
- ✓ Legacy standalone budget and category-group user flows are removed from the supported runtime path — `v1.0`
- ✓ Mobile-width category and budget flows remain supported — `v1.0`
- ✓ The app now uses an explicit monthly competency model with a first-class `Mês Atual` — `v1.1`
- ✓ Users can close a month into a preserved snapshot and continue in the next editable month — `v1.1`
- ✓ Users can browse closed snapshots in a protected read-only context — `v1.1`
- ✓ Sync and period-bound manual financial writes are blocked while `Mês Atual` is undefined — `v1.1`
- ✓ Accounts and transactions now carry durable monthly-period identity in the supported runtime — `v1.1`

### Active

- [ ] User manages only Pluggy-connected accounts from `Contas`, including rename, bill-import toggle, and connection deletion
- [ ] User imports only credit-card bill transactions into the active monthly workflow instead of importing all account transactions
- [ ] User has imported bill transactions persisted against a card and `Mês Atual`, with categorization applied only when a matching rule exists
- [ ] User no longer sees wallets or account balances as part of the supported financial model, with the month total derived from `receitas - despesas`

### Out of Scope

- Native or hybrid mobile app — the responsive web application remains the supported product surface
- Broad visual redesign — preserve the existing visual language unless a milestone explicitly changes that goal
- Multi-user productization — the target remains personal self-hosted usage
- Reintroducing groups through a renamed or hidden grouping layer — conflicts with the simplification baseline already shipped
- Returning to freeform date-range period navigation as the primary workflow — conflicts with the shipped monthly-control model
- Importing all bank-account transactions from Pluggy in this milestone — this scope is intentionally limited to card bills
- Restoring wallets or per-account balances as user-facing bookkeeping concepts — conflicts with the new month-total model

## Context

The current codebase is a brownfield React SPA plus FastAPI application with PostgreSQL, Redis, and Celery. The supported runtime now includes explicit month-state APIs, durable monthly period and snapshot persistence, protected-history read routing, and milestone-aligned guard behavior across transactions, accounts, imports, and recurring flows.

This milestone changes the meaning of `Contas` and Pluggy connections: connected accounts become bill-import sources instead of general transaction-sync sources. Imported credit-card bill rows should be stored in app-owned monthly data, associated with the relevant card and current month, and should only receive a category automatically when a saved user rule matches.

Known remaining debt is mostly process and verification quality rather than feature completeness. The milestone audit passed all functional categories, but Nyquist `*-VALIDATION.md` artifacts are still missing for phases `08-12`.

## Constraints

- **Product scope**: Self-hosted personal finance app for personal use in this version
- **UX**: Preserve the existing design language unless a milestone explicitly changes that goal
- **Mobile**: New work must remain usable on mobile-width screens
- **Architecture**: Continue brownfield evolution within the current React/FastAPI/PostgreSQL stack
- **Scope control**: Future milestones should add value without reviving category-group or standalone-budget concepts
- **Period model**: The active editable workspace must always resolve to one monthly period
- **Historical integrity**: Closed months must preserve historical state and require explicit confirmation before mutating control-state actions
- **Pluggy sync**: This milestone must use `GET /accounts/{accountId}/bills` and filter by `dueDate` against the month after `Mês Atual`
- **Import timing**: Bill ingestion should run automatically on a daily schedule for enabled connections
- **Financial model**: The supported runtime should not depend on wallets or per-account balances after this milestone

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Keep the product as a responsive web app for v2 | The current design already works on mobile and did not justify a platform split | ✓ Good |
| Rebrand from `Securo` to `Flux` in v2 | Establish the new product identity without expanding platform scope | ✓ Good |
| Remove category groups from the supported model | Flat categories are simpler for the intended personal-finance workflow | ✓ Good |
| Store optional budget settings directly on each category | Budget ownership belongs with the category and simplifies editing flows | ✓ Good |
| Preserve the existing visual design language during the migration | The current UI was already responsive and the milestone goal was behavioral simplification, not redesign | ✓ Good |
| Retire the standalone Budgets screen after category editing shipped | One supported write path reduces drift and dead-end navigation | ✓ Good |
| Model operational periods as monthly competencies with a distinct editable `Mês Atual` | Monthly closing and historical review need first-class period identity instead of freeform date ranges | ✓ Good |
| Treat closed months as snapshots with guarded mutations | Historical views must stay trustworthy while still allowing explicitly confirmed corrective actions | ✓ Good |
| Keep categories available without `Mês Atual` while blocking period-bound financial writes | Categories act as setup metadata; transactions, accounts, sync, and recurring flows remain period-bound | ✓ Good |
| Treat Pluggy connections as bill-import sources instead of full transaction-sync sources in v1.2 | The milestone is focused on credit-card invoice tracking, not broad bank ingestion | — Pending |
| Remove wallets and per-account balances from the supported product model | The month-level result should be derived from `receitas - despesas` rather than account-ledger balances | — Pending |

<details>
<summary>Archived v1.1 Planning Notes</summary>

Previous milestone focus:

- Replace date-range period selection with explicit monthly competency management
- Add close-month snapshot creation and protected-history navigation
- Remove remaining supported category-group dependencies
- Guard period-bound writes while the editable month is undefined

</details>

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `$gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `$gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-04-04 after milestone v1.2 definition*
