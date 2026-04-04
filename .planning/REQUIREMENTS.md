# Requirements: Flux

**Defined:** 2026-04-04
**Core Value:** Manage personal finances clearly on a self-hosted app that remains practical to use on mobile.

## v1 Requirements

### Period Management

- [x] **PERIOD-01**: User can define the monthly period identifier for `Mês Atual` using a month/year competency format.
- [ ] **PERIOD-02**: User can switch the main financial context between `Mês Atual` and any previously closed monthly snapshot.
- [x] **PERIOD-03**: User sees only closed monthly snapshots when no current-month period has been defined yet.
- [ ] **PERIOD-04**: User is prompted to define the next `Mês Atual` period immediately after closing the current month.

### Month Closure and Snapshots

- [ ] **SNAP-01**: User can close the current month with a `Fechar Mês` action that creates a historical snapshot for the active monthly period.
- [ ] **SNAP-02**: User can identify from the UI when the selected context is a closed monthly snapshot instead of `Mês Atual`.
- [ ] **SNAP-03**: User can review the preserved financial state of a closed monthly snapshot without converting it back into the editable month.

### Mutation Guards

- [ ] **GUARD-01**: User must confirm before executing an action that changes the control state of a closed monthly snapshot.
- [x] **GUARD-02**: User cannot sync external data while `Mês Atual` has no defined monthly period.
- [x] **GUARD-03**: User cannot manually create transactions, accounts, or categories while `Mês Atual` has no defined monthly period.

### UX Consistency

- [x] **UX-01**: User sees any new or updated screens in this milestone follow the existing Flux layout pattern instead of a new visual structure.

### Period-Linked Data Model

- [ ] **DATA-01**: User has transactions linked to a monthly period so records can be queried and rendered from the correct current month or snapshot.
- [ ] **DATA-02**: User can create a new account with the intended `Mês Atual` period recorded as part of the flow.
- [ ] **DATA-03**: User can close a month only when the resulting snapshot is linked to the monthly period being closed.

### Category Group Removal

- [ ] **CLEAN-01**: User no longer depends on category-group fields or relationships in the supported backend and database model.
- [ ] **CLEAN-02**: User no longer encounters category-group concepts in supported APIs, sync flows, or monthly-finance workflows.

## v2 Requirements

### Category Workflow Enhancements

- **CAT-01**: User can archive or hide categories to improve organization in the flat list.
- **CAT-02**: User can reorder categories with explicit mobile-friendly controls.

### Budget Assistance

- **BUD-01**: User receives suggested budget amounts based on recent spending history.
- **BUD-02**: User can save a categorization rule from budget-related workflows.
- **BUD-03**: User can reallocate budget amounts through a dedicated workflow.

## Out of Scope

| Feature | Reason |
|---------|--------|
| Native or hybrid mobile app | Responsive web remains the supported product surface. |
| Broad visual redesign | This milestone changes period behavior and historical controls, not the product's visual language. |
| Multi-user productization | The target remains personal self-hosted usage. |
| Reintroducing category groups through a hidden compatibility layer | Conflicts with the simplification baseline and with the explicit cleanup goal of this milestone. |
| Freeform date-range period selection as the primary navigation model | The milestone is explicitly moving to monthly competency selection. |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| PERIOD-01 | Phase 8 | Complete |
| PERIOD-02 | Phase 11 | Pending |
| PERIOD-03 | Phase 8 | Complete |
| PERIOD-04 | Phase 10 | Pending |
| SNAP-01 | Phase 10 | Pending |
| SNAP-02 | Phase 11 | Pending |
| SNAP-03 | Phase 11 | Pending |
| GUARD-01 | Phase 11 | Pending |
| GUARD-02 | Phase 8 | Complete |
| GUARD-03 | Phase 8 | Complete |
| UX-01 | Phase 8 | Complete |
| DATA-01 | Phase 9 | Pending |
| DATA-02 | Phase 9 | Pending |
| DATA-03 | Phase 10 | Pending |
| CLEAN-01 | Phase 9 | Pending |
| CLEAN-02 | Phase 9 | Pending |

**Coverage:**
- v1 requirements: 16 total
- Mapped to phases: 16
- Unmapped: 0 ✓

---
*Requirements defined: 2026-04-04*
*Last updated: 2026-04-04 after Phase 8 completion*
