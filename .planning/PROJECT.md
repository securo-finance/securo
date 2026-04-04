# Flux

## What This Is

Flux is a self-hosted personal finance web application for personal use, with responsive mobile usage treated as a first-class constraint. The shipped `v1.0` milestone completed the visible Flux rebrand and flattened the category and budget model so categories are now the supported place to manage budget state.

## Core Value

Manage personal finances clearly on a self-hosted app that remains practical to use on mobile.

## Current State

- Shipped milestone: `v1.0 Flux v2` on 2026-04-03
- Active product shape: Flux-branded shell, flat categories, category-owned budget state, retired standalone budgets surface
- Planning state: defining `v1.1 Gestão Mensal`

## Requirements

### Validated

- ✓ Flux branding is consistent across the application shell, setup/auth copy, and export filenames — `v1.0`
- ✓ Categories can be created and edited without category-group dependency in the supported UI or API contract — `v1.0`
- ✓ Budget enablement and amount are managed from the category flow with an explicit unbudgeted state — `v1.0`
- ✓ Budget readers, reports, and exports consume the category-owned budget model consistently — `v1.0`
- ✓ Legacy standalone budget and category-group user flows are removed from the supported runtime path — `v1.0`
- ✓ Mobile-width category and budget flows remain supported — `v1.0`

### Active

- [ ] Remove all remaining category-group references from the supported runtime, backend contract, and database model
- [ ] Let the app operate on a monthly competency model where `Mês Atual` is explicitly identified by period
- [ ] Allow users to close the current month into an immutable historical snapshot
- [ ] Let users browse and compare saved monthly snapshots and the current open month
- [ ] Block sync and manual financial data entry while the current month period is undefined

### Out of Scope

- Native or hybrid mobile app — the responsive web application remains the supported product surface
- Broad visual redesign — preserve the existing visual language unless a future milestone makes design a primary goal
- Multi-user productization — the target remains personal self-hosted usage
- Reintroducing groups through a renamed or hidden grouping layer — conflicts with the simplification goal shipped in `v1.0`
- Forcing every category to have a budget — categories must continue to support an explicit non-budgeted state

## Context

The current codebase is a brownfield React SPA plus FastAPI application with PostgreSQL, Redis, and Celery. After `v1.0`, the active runtime no longer depends on category groups or standalone budget CRUD for supported user workflows. Remaining known debt is mostly cleanup and artifact quality: verification files do not map REQ IDs directly, `*-VALIDATION.md` artifacts are still missing, legacy compatibility export payloads remain intentionally present for recovery, and dormant internal group code still exists outside the supported path.

The next milestone shifts the domain model from date-range period navigation to monthly competencies. The product now needs a clear distinction between the editable `Mês Atual` and closed monthly snapshots, with transactions and month-level operations linked directly to a declared period so historical lookup and closure stay consistent.

## Constraints

- **Product scope**: Self-hosted personal finance app for personal use in this version
- **UX**: Preserve the existing design language unless a milestone explicitly changes that goal
- **Mobile**: New work must remain usable on mobile-width screens
- **Architecture**: Continue brownfield evolution within the current React/FastAPI/PostgreSQL stack
- **Scope control**: Future milestones should add value without reviving category-group or standalone-budget concepts
- **Period model**: The active editable workspace must always resolve to one monthly period — monthly closure and snapshot retrieval depend on it
- **Historical integrity**: Closed months must preserve historical state and require explicit confirmation before any mutating action

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Keep the product as a responsive web app for v2 | The current design already works on mobile and did not justify a platform split | ✓ Good |
| Rebrand from `Securo` to `Flux` in v2 | Establish the new product identity without expanding platform scope | ✓ Good |
| Remove category groups from the supported model | Flat categories are simpler for the intended personal-finance workflow | ✓ Good |
| Store optional budget settings directly on each category | Budget ownership belongs with the category and simplifies editing flows | ✓ Good |
| Preserve the existing visual design language during the migration | The current UI was already responsive and the milestone goal was behavioral simplification, not redesign | ✓ Good |
| Retire the standalone Budgets screen after category editing shipped | One supported write path reduces drift and dead-end navigation | ✓ Good |
| Model operational periods as monthly competencies with a distinct editable `Mês Atual` | Monthly closing and historical review need first-class period identity instead of freeform date ranges | — Pending |
| Treat closed months as snapshots with guarded mutations | Historical views must stay trustworthy while still allowing explicitly confirmed corrective actions | — Pending |

## Next Milestone Goals

## Current Milestone: v1.1 Gestão Mensal

**Goal:** Evolve Flux into a monthly financial control model where `Mês Atual` is the editable working month and closed months are preserved as snapshots.

**Target features:**
- Remove all remaining category-group references, including database and backend remnants
- Replace date-range period selection with selection between `Mês Atual` and saved monthly snapshots
- Add a `Fechar Mês` action that creates a snapshot for the closed month
- Clearly mark closed snapshots and require confirmation for actions that affect month control
- Require period assignment for the current month before sync or manual creation of transactions, accounts, and categories
- Automatically open a new `Mês Atual` after month closure and prompt for its period
- Link transactions directly to the relevant monthly period for historical lookup

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
*Last updated: 2026-04-04 after v1.1 milestone start*
