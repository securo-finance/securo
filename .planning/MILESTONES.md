# Milestones

## v1.0 Flux v2 (Shipped: 2026-04-03)

**Phases completed:** 7 phases, 7 plans, 9 tasks

**Key accomplishments:**

- Flux now appears in the SPA shell title, auth/setup copy, and downloaded backup or transaction export filenames without changing compatibility-facing infrastructure identifiers
- Categories now own the current budget state directly, legacy budget history is flattened to one active budget row per category, and the compatibility budget API keeps the app working against the simplified model
- Category create and edit now include explicit budget enablement and amount controls in one mobile-friendly form, while the budgets page shifts to a read-only companion surface
- Categories now render as a flat management list, budget comparison readers expose only category-owned state, and backup exports declare the v2 compatibility model explicitly
- The public product no longer exposes group-based or standalone budget editing paths, and the remaining user-facing release/setup copy now uses Flux
- The app no longer exposes a standalone Budgets screen, and legacy budget navigation now resolves into Categories
- The shipped category flow is now flat end to end: `/api/categories` no longer exposes `group_id`, and setup no longer creates category groups behind the scenes

---

## v1.1 Gestão Mensal (Shipped: 2026-04-04)

**Phases completed:** 5 phases, 5 plans, 21 tasks

**Key accomplishments:**

- Users must define `Mês Atual` explicitly before syncing or creating manual financial data
- Records now persist durable monthly-period identity across current-month and historical flows
- Closing the active month creates a preserved snapshot and immediately opens the next editable month
- The app now supports explicit closed-history navigation with read-only UI and backend mutation guards
- Closed-history browsing, remaining guard coverage, and dashboard month-selection UX were hardened after the milestone audit

**Known debt:**

- Nyquist validation artifacts are still missing for phases `08-12`

---
