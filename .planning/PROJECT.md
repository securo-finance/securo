# Flux

## What This Is

Flux is a self-hosted personal finance web application for personal use, with responsive mobile usage treated as a first-class constraint. After shipping `v1.1`, the product now operates on an explicit monthly-control model built around one editable `Mês Atual` plus preserved closed-month snapshots.

## Core Value

Manage personal finances clearly on a self-hosted app that remains practical to use on mobile.

## Current State

- Shipped milestone: `v1.1 Gestão Mensal` on 2026-04-04
- Active product shape: explicit `Mês Atual`, durable `monthly_periods`, preserved `monthly_snapshots`, protected-history navigation, and categories available as setup metadata even before `Mês Atual` exists
- Planning state: next milestone not yet defined

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

- [ ] User can archive or hide categories to improve organization in the flat list
- [ ] User can reorder categories with explicit mobile-friendly controls
- [ ] User receives suggested budget amounts based on recent spending history
- [ ] User can save a categorization rule from budget-related workflows
- [ ] User can reallocate budget amounts through a dedicated workflow

### Out of Scope

- Native or hybrid mobile app — the responsive web application remains the supported product surface
- Broad visual redesign — preserve the existing visual language unless a milestone explicitly changes that goal
- Multi-user productization — the target remains personal self-hosted usage
- Reintroducing groups through a renamed or hidden grouping layer — conflicts with the simplification baseline already shipped
- Returning to freeform date-range period navigation as the primary workflow — conflicts with the shipped monthly-control model

## Context

The current codebase is a brownfield React SPA plus FastAPI application with PostgreSQL, Redis, and Celery. The supported runtime now includes explicit month-state APIs, durable monthly period and snapshot persistence, protected-history read routing, and milestone-aligned guard behavior across transactions, accounts, imports, and recurring flows.

Known remaining debt is mostly process and verification quality rather than feature completeness. The milestone audit passed all functional categories, but Nyquist `*-VALIDATION.md` artifacts are still missing for phases `08-12`.

## Constraints

- **Product scope**: Self-hosted personal finance app for personal use in this version
- **UX**: Preserve the existing design language unless a milestone explicitly changes that goal
- **Mobile**: New work must remain usable on mobile-width screens
- **Architecture**: Continue brownfield evolution within the current React/FastAPI/PostgreSQL stack
- **Scope control**: Future milestones should add value without reviving category-group or standalone-budget concepts
- **Period model**: The active editable workspace must always resolve to one monthly period
- **Historical integrity**: Closed months must preserve historical state and require explicit confirmation before mutating control-state actions

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

## Next Milestone Goals

- Define the next milestone explicitly before creating new planning artifacts
- Prioritize category workflow improvements or budget-assistance capabilities from the archived v2 candidate list
- Close process debt around Nyquist validation artifacts while preserving delivery speed

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
*Last updated: 2026-04-04 after v1.1 milestone completion*
