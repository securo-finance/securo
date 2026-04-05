# Requirements: Flux

**Defined:** 2026-04-05
**Core Value:** Manage personal finances clearly on a self-hosted app that remains practical to use on mobile.

## v1 Requirements

### Cards Navigation

- [ ] **CARD-01**: User can access a `Cartões` menu entry positioned below `Transações` in the main navigation.
- [ ] **CARD-02**: User can view a list of cards imported from Pluggy in the `Cartões` screen.
- [ ] **CARD-03**: User can identify each imported card in the `Cartões` list using the existing card metadata available in Flux.

### Card Transaction Browsing

- [ ] **CARD-04**: User can open a dedicated transaction view for a selected card from the `Cartões` list.
- [ ] **CARD-05**: User can view only transactions associated with the selected card in that card transaction view.
- [ ] **CARD-06**: User can use a card transaction view that preserves the main `Transações` screen behavior where it remains compatible with card-scoped browsing.

### Snapshots and Filters

- [ ] **CARD-07**: User can switch between available monthly snapshots while staying in the selected card transaction context.
- [ ] **CARD-08**: User can filter the selected card's transactions by category.
- [ ] **CARD-09**: User can combine snapshot selection and category filtering without leaving the selected card transaction context.

## v2 Requirements

### Card Management

- **CARDM-01**: User can rename imported cards from the `Cartões` area.
- **CARDM-02**: User can enable or disable bill import for a card from the `Cartões` area.
- **CARDM-03**: User can remove a card or connection from the `Cartões` area.

### Expanded Analysis

- **CARDA-01**: User can review card-specific totals, limits, or invoice summaries in the `Cartões` area.
- **CARDA-02**: User can compare multiple cards side by side.

## Out of Scope

| Feature | Reason |
|---------|--------|
| Renaming, enabling/disabling import, or deleting cards from `Cartões` | This milestone is limited to visualizing and navigating card transactions. |
| Importing non-card Pluggy transactions | The current product scope remains centered on credit-card bill imports. |
| Redesigning the application's visual language or global navigation beyond adding `Cartões` | The milestone adds a new workflow without broad UI redesign. |
| New categorization-rule authoring flows | This milestone only needs category filtering on imported card transactions. |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| CARD-01 | Phase 1 | Complete |
| CARD-02 | Phase 1 | Complete |
| CARD-03 | Phase 1 | Complete |
| CARD-04 | Phase 2 | Complete |
| CARD-05 | Phase 2 | Complete |
| CARD-06 | Phase 2 | Complete |
| CARD-07 | Phase 3 | Complete |
| CARD-08 | Phase 3 | Complete |
| CARD-09 | Phase 3 | Complete |

**Coverage:**
- v1 requirements: 9 total
- Mapped to phases: 9
- Unmapped: 0

---
*Requirements defined: 2026-04-05*
*Last updated: 2026-04-05 after milestone v1.3 autonomous execution*
