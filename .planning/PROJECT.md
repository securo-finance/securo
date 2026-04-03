# Flux

## What This Is

Flux is a self-hosted personal finance web application for personal use, with responsive mobile usage treated as a first-class constraint for all new work. It already handles account aggregation, transaction tracking, budgeting, recurring finance workflows, and reporting; this v2 focuses on rebranding the product from Securo to Flux and simplifying how categories and budgets work.

## Core Value

Manage personal finances clearly on a self-hosted app that remains practical to use on mobile.

## Requirements

### Validated

- ✓ User can authenticate and maintain a session — existing
- ✓ User can connect financial accounts and sync account/transaction data — existing
- ✓ User can categorize transactions and apply category rules — existing
- ✓ User can manage budgets, payees, assets, recurring items, and reports — existing
- ✓ User can use the application as a responsive web SPA with mobile support — existing

### Active

- [ ] Rebrand the product from `Securo` to `Flux` across the user-facing product and planning artifacts
- [ ] Remove category groups from the domain and UI
- [ ] Move budget configuration into the category itself, with an explicit flag indicating whether the category has a budget
- [ ] Allow budget value creation and editing directly from category create/edit flows
- [ ] Update existing budget-related screens and flows to read from the category-owned budget model

### Out of Scope

- Native or hybrid mobile app — v2 remains the current responsive web application
- Broad visual redesign — preserve the current design language and responsive behavior
- Multi-user productization — current target remains personal self-hosted usage, with possible future use by friends
- New finance domains beyond rebranding and category/budget changes — not part of this v2 scope

## Context

The existing codebase is a brownfield React SPA + FastAPI application with PostgreSQL, Redis, and Celery. It already supports personal-finance workflows including account sync, transaction management, categories, budgets, payees, assets, recurring items, import/export, dashboard views, and reports. The user wants to keep the current design system because it is already responsive on mobile, but all new features in this version should continue to work well in mobile layouts. The v2 is intentionally narrow: establish the Flux name and simplify category budgeting by removing category groups and attaching optional budget data directly to each category.

## Constraints

- **Product scope**: Self-hosted personal finance app — built primarily for personal use in this version
- **UX**: Preserve existing design language — current UI is already responsive and should stay visually consistent
- **Mobile**: New features must work well on mobile layouts — mobile remains a first-class usage mode
- **Architecture**: Brownfield evolution of the current stack — changes should fit the existing React/FastAPI/PostgreSQL architecture
- **Scope control**: v2 is limited to rebranding and category/budget model changes — avoid opportunistic feature expansion

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Keep the product as a responsive web app for v2 | The current design already works on mobile; the user does not want a separate native app in this round | — Pending |
| Rebrand from `Securo` to `Flux` in v2 | Establishes the new product identity without expanding scope beyond the current platform | — Pending |
| Remove category groups | The user no longer wants grouped categories in the product model | — Pending |
| Store optional budget settings directly on each category | Budget ownership should live with the category itself and be editable in category flows | — Pending |
| Preserve the existing visual design language | The current UI is already responsive and should remain familiar while v2 updates behavior | — Pending |

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
*Last updated: 2026-04-03 after initialization*
