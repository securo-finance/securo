# Feature Research

**Domain:** self-hosted personal finance web app v2; category and budgeting UX simplification
**Researched:** 2026-04-03
**Confidence:** MEDIUM

## Feature Landscape

### Table Stakes (Users Expect These)

Features users assume exist. Missing these = product feels incomplete.

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| Category create/edit with inline budget toggle and amount | Current finance apps commonly let category settings carry budget behavior directly or expose budget inclusion at the category level; users expect one place to decide whether a category participates in the budget. | MEDIUM | Flux should treat `has_budget` plus budget settings as first-class category fields in create/edit, not force a second trip to a separate budgets screen. Keep the form short on mobile: name, icon/color, budget on/off, amount, optional recurring behavior. |
| Categories remain easy to organize after groups are removed | Even apps that keep groups still support ordering, hiding, and collapsing because users need visual organization. If Flux removes groups, users will still expect scannable lists, stable order, and archived/hidden categories. | MEDIUM | Replace groups with a flat but intentionally ordered list, optional type split (income/expense/transfer), search, and hide/archive. Do not leave users with an unstructured alphabetical dump. |
| Transaction recategorization updates budget impact immediately | Users expect category changes to reflect in budget/spending views right away, especially when correcting overspending. | MEDIUM | Category-owned budget state must propagate everywhere budgets are read today: dashboard, reports, rules, recurring items, and transaction detail/edit flows. |
| Rule-assisted categorization from transaction edits | Monarch and Actual both reinforce the expectation that when a user corrects a category, the app should help them save that logic for future transactions. | MEDIUM | When a transaction category is changed, prompt for “apply to future similar transactions” or keep the current rules workflow tightly adjacent. This matters more once categories become the primary budgeting unit. |
| Budget inclusion can be explicit per category | Users expect some categories to be tracked but excluded from budget totals, especially transfers, reimbursements, passthrough items, and informational categories. | MEDIUM | Flux should preserve the concept of a category existing without an active budget. “No budget” must be a first-class state, not equivalent to `0`. |
| Fast mobile category/budget editing | Mobile-first finance users expect common category edits to work in one or two taps from a responsive sheet/dialog, without horizontal tables or deep navigation. | MEDIUM | The current app already prioritizes responsive SPA flows. Preserve that by using stacked fields, large tap targets, and no dependency on drag-and-drop or hover-only affordances. |
| Safe migration from grouped categories to flat categories | Existing users expect existing categories, rules, reports, and historical transactions to survive a simplification pass without manual cleanup. | HIGH | At minimum: preserve category IDs where possible, map group metadata out cleanly, keep historical transactions categorized, and keep budget/report totals stable after migration. Users will tolerate UI simplification, not data drift. |
| Clear overspending and reallocation feedback | Budgeting users expect overspent categories to be obvious and fixable, often by editing the category budget or moving money mentally between categories. | MEDIUM | Even if Flux does not add explicit “move money” UX yet, category detail and budget surfaces must clearly show actual vs planned and allow immediate correction from the category itself. |

### Differentiators (Competitive Advantage)

Features that set the product apart. Not required, but valuable.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| Single-flow category setup for both metadata and budgeting | Reduces mental overhead versus separate “categories” and “budgets” management models. | MEDIUM | This is the strongest UX advantage of the v2 simplification if done consistently across desktop and mobile. |
| Migration preview and post-migration explanation | Existing users will trust the simplification more if they can see what changed and what did not. | MEDIUM | Show how groups were flattened, which categories still have budgets, and confirm that rules/history remain intact. This is especially important in a brownfield personal-finance app where trust is core product value. |
| Smart defaults from history when enabling a category budget | Users expect the app to help choose a starting number instead of making them guess. | MEDIUM | Pre-fill from recent spending averages or last configured budget when toggling budgeting on for a category. Keep editable. |
| Mobile-first quick actions from category and transaction views | Lets users fix budget/category issues at the point of pain instead of navigating to a dedicated management page. | MEDIUM | Examples: “Enable budget for this category,” “Edit planned amount,” “Recategorize and save rule.” This aligns with responsive SPA usage and reduces friction on smaller screens. |

### Anti-Features (Commonly Requested, Often Problematic)

Features that seem good but create problems.

| Feature | Why Requested | Why Problematic | Alternative |
|---------|---------------|-----------------|-------------|
| Replacing removed groups with a new hidden pseudo-group layer | Users may ask for “basically groups, just called something else” to recover old organization. | Recreates the same mental model and migration surface the project is trying to simplify; it adds complexity back into APIs, reports, and mobile forms. | Keep a flat ordered list plus category type, hide/archive, filtering, and tags/notes if needed later. |
| Forcing every category to have a budget amount | Seems simpler for implementation and reporting. | Breaks expectations for transfers, rare-use categories, reimbursements, and categories users want to track without planning monthly spend. Also confuses `0 budget` with “not budgeted.” | Use explicit optional budget settings on categories: on/off plus amount/settings only when enabled. |
| Separate category screen and budget screen that edit the same concept independently | Familiar to the current app, and may look safer during migration. | Creates duplicate entry points, conflicting mental models, and higher mobile friction. Users will not understand why category budget state lives in two places. | Make categories the source of truth. If a budgets page remains, it should be a filtered planning view over category-owned data, not a second CRUD surface. |
| Deep multi-step mobile forms for category setup | Can feel “complete” because every option gets its own step. | Too slow for common finance corrections; users often edit categories in the middle of transaction cleanup on mobile. | Keep the core edit path compact. Move advanced fields behind progressive disclosure. |
| Destructive migration that deletes group context with no fallback or explanation | Simplifies implementation. | High trust risk: users may feel the app lost their structure or changed reports unexpectedly. | Provide migration notes, preserve ordering where possible, and keep hidden migration metadata only as long as needed to stabilize rollout. |
| Mandatory drag-and-drop as the only organization method | Popular in some apps. | Poor on mobile web, inaccessible, and frustrating in long category lists. | Offer explicit move up/down, sort controls, or lightweight reorder UI that works with touch and keyboard. |

## Feature Dependencies

```text
Flat category model
    requires --> category migration mapping
    requires --> updated API/schema contracts
    requires --> budget reads moved to category-owned settings

Category-owned budget settings
    requires --> create/edit category UX
    requires --> dashboard/report/budget screen refactor
    enhances --> quick mobile budget edits

Transaction recategorization
    enhances --> accurate budget vs actual
    enhances --> rules/save-for-future prompts

Hide/archive categories
    protects --> flat list usability after group removal

Separate budget CRUD surface
    conflicts --> single source of truth on categories
```

### Dependency Notes

- **Flat category model requires migration mapping:** removing groups is not only a UI change; existing category/group relationships, sort order, and historical references must survive the transition.
- **Category-owned budget settings require screen refactors:** any page reading `Budget` as a separate concept must switch to category-backed data or derived views.
- **Transaction recategorization enhances budget accuracy:** users fix budget errors by fixing category assignment, so the recategorization flow is part of budgeting UX whether or not it lives on the budget page.
- **Hide/archive protects flat-list usability:** once groups are gone, hiding inactive categories becomes more important, not less.
- **Separate budget CRUD conflicts with category-owned budgets:** one concept should have one authority.

## MVP Definition

### Launch With (v2)

- [ ] Category create/edit supports optional budget enablement, amount, and clear “not budgeted” state
- [ ] Flat category list remains searchable, ordered, and mobile-usable after group removal
- [ ] Existing transactions, rules, recurring items, and reports continue to resolve categories correctly after migration
- [ ] Budget surfaces read category-owned budget settings consistently
- [ ] Mobile category and budget edits work in a compact responsive flow

### Add After Validation (v2.x)

- [ ] Historical-spend suggestions when enabling or editing a category budget
- [ ] Inline “save rule for future transactions” prompt after recategorization
- [ ] Lightweight archive/hide filters and improved list segmentation for larger category sets

### Future Consideration (v3+)

- [ ] Tags or focused views for cross-cutting organization without reintroducing groups
- [ ] More advanced budget strategies such as rollover or bucket/flex budgeting
- [ ] Guided “move money between categories” workflow if users need active envelope-style reallocation

## Feature Prioritization Matrix

| Feature | User Value | Implementation Cost | Priority |
|---------|------------|---------------------|----------|
| Category-owned optional budget settings | HIGH | MEDIUM | P1 |
| Migration-safe removal of category groups | HIGH | HIGH | P1 |
| Mobile-first category edit flow | HIGH | MEDIUM | P1 |
| Consistent budget/report reads from categories | HIGH | MEDIUM | P1 |
| Hide/archive and stable ordering in flat list | MEDIUM | MEDIUM | P2 |
| Historical-spend budget suggestions | MEDIUM | MEDIUM | P2 |
| Inline rule prompt after category correction | MEDIUM | MEDIUM | P2 |
| Advanced budget reallocation workflow | MEDIUM | HIGH | P3 |

**Priority key:**
- P1: Must have for launch
- P2: Should have, add when possible
- P3: Nice to have, future consideration

## Competitor Feature Analysis

| Feature | Competitor A | Competitor B | Our Approach |
|---------|--------------|--------------|--------------|
| Category organization | Monarch keeps a 3-layer type/group/category model and lets users create categories on desktop and mobile. | Actual also keeps category groups, plus collapse/hide/merge behaviors. | Flux is intentionally simplifying beyond both. That means it must replace groups with flat-list affordances: order, search, hide/archive, and clean type separation. |
| Budget participation at category level | Monarch lets users toggle whether a category is excluded from the budget and choose budget-related behavior when creating categories. | YNAB centers planning around per-category targets and progress states. | Flux should make “has budget” explicit on the category and expose planned amount in the same edit flow. |
| Mobile category workflow | Monarch documents mobile category creation directly in settings with only a few required fields. | YNAB emphasizes mobile category suggestions and mobile target management. | Flux should keep category/budget edits compact and touch-friendly, avoiding hover-only or table-heavy interactions. |
| Budget correction behavior | Monarch supports moving money between categories when overspending occurs. | Actual guidance emphasizes recategorizing or moving funds when a category is short. | Flux should at minimum make overspending visible and editable from category-owned budget settings; explicit reallocation can come later if needed. |

## Sources

- Flux project context: [.planning/PROJECT.md](/workspaces/flux-pluggy/securo/.planning/PROJECT.md)
- Flux codebase context: [.planning/codebase/ARCHITECTURE.md](/workspaces/flux-pluggy/securo/.planning/codebase/ARCHITECTURE.md)
- Current category UX: [frontend/src/pages/categories.tsx](/workspaces/flux-pluggy/securo/frontend/src/pages/categories.tsx)
- Current budget UX: [frontend/src/pages/budgets.tsx](/workspaces/flux-pluggy/securo/frontend/src/pages/budgets.tsx)
- Monarch Money, “Creating Custom Categories and Groups” (updated 2025-09-05): https://help.monarch.com/hc/en-us/articles/360048883771-Creating-Custom-Categories-and-Groups
- Monarch Money, “Creating Your Budget in Monarch” (updated 2025-09-05): https://help.monarch.com/hc/en-us/articles/360048883631-Creating-Your-Budget-in-Monarch
- Monarch Money, “Organizing Transactions with Tags” (updated 2025-12-03): https://help.monarch.com/hc/en-us/articles/4409690120596-Organizing-Transactions-with-Tags
- Monarch Money, “Moving Money Between Categories to Accommodate Overspending” (updated 2025-09-29): https://help.monarch.com/hc/en-us/articles/360048883591-Moving-Money-Between-Categories-to-Accommodate-Overspending
- Actual Budget docs, “Categories”: https://actualbudget.org/docs/budgeting/categories/
- Actual Budget docs, “Starting Fresh”: https://actualbudget.org/docs/getting-started/starting-fresh/
- YNAB, “Category Suggestions” (2023-05-31): https://www.ynab.com/whats-new/category-suggestions
- YNAB, “Goal Tracking”: https://www.ynab.com/features/goal-tracking

---
*Feature research for: self-hosted personal finance web app v2*
*Researched: 2026-04-03*
