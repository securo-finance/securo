# Roadmap: Flux

## Milestones

- ✅ **v1.0 Flux v2** - Phases 1-7 shipped on 2026-04-03
- 🚧 **v1.1 Gestão Mensal** - Phases 8-11 in progress

## Overview

Flux is moving from freeform period navigation to a monthly control model where one editable `Mês Atual` coexists with preserved closed-month snapshots. This milestone delivers that shift without changing the existing Flux layout pattern, while keeping historical months trustworthy and blocking financial mutations until the active month is defined.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

<details>
<summary>✅ v1.0 Flux v2 (Phases 1-7) - SHIPPED 2026-04-03</summary>

Archived roadmap: `.planning/milestones/v1.0-ROADMAP.md`

</details>

- [x] **Phase 8: Current Month Setup & Guards** - Define `Mês Atual` explicitly and block sync or manual entry until the active month exists.
- [ ] **Phase 9: Period-Linked Records & Cleanup** - Make monthly period identity part of supported financial records while removing remaining category-group dependencies.
- [ ] **Phase 10: Month Closure & Next Month Start** - Close the active month into a period-bound snapshot and immediately bootstrap the next editable month.
- [ ] **Phase 11: Snapshot Navigation & Protected History** - Let users browse current and closed months clearly while guarding snapshot control mutations.

## Phase Details

### Phase 8: Current Month Setup & Guards
**Goal**: Users can define the editable `Mês Atual` period and cannot perform sync or manual financial entry before that monthly context exists.
**Depends on**: Phase 7
**Requirements**: PERIOD-01, PERIOD-03, GUARD-02, GUARD-03, UX-01
**Success Criteria** (what must be TRUE):
  1. User can define the `Mês Atual` period using a month/year competency format.
  2. User sees only closed monthly snapshots when no current-month period has been defined yet.
  3. User cannot sync external data while `Mês Atual` has no defined monthly period.
  4. User cannot manually create transactions, accounts, or categories while `Mês Atual` has no defined monthly period.
  5. User sees the new or updated month-selection and guard states inside the existing Flux layout pattern.
**Plans**: 1 complete
**UI hint**: yes

### Phase 9: Period-Linked Records & Cleanup
**Goal**: Users work with financial records that belong to a monthly period, without depending on legacy category-group concepts in supported flows.
**Depends on**: Phase 8
**Requirements**: DATA-01, DATA-02, CLEAN-01, CLEAN-02
**Success Criteria** (what must be TRUE):
  1. User sees transactions queried and rendered from the correct current month or closed snapshot because each transaction is linked to a monthly period.
  2. User can create a new account with the intended `Mês Atual` period recorded as part of the supported flow.
  3. User no longer encounters category-group concepts in supported APIs, sync behavior, or monthly-finance workflows.
  4. User no longer depends on category-group fields or relationships in the supported backend and database model.
**Plans**: TBD
**UI hint**: yes

### Phase 10: Month Closure & Next Month Start
**Goal**: Users can close the current editable month into a preserved snapshot and immediately continue with a newly opened `Mês Atual`.
**Depends on**: Phase 9
**Requirements**: SNAP-01, PERIOD-04, DATA-03
**Success Criteria** (what must be TRUE):
  1. User can trigger `Fechar Mês` for the active `Mês Atual` and receive a historical snapshot for that month.
  2. User can close a month only when the created snapshot is linked to the exact monthly period being closed.
  3. User is prompted to define the next `Mês Atual` period immediately after the previous month is closed.
**Plans**: TBD
**UI hint**: yes

### Phase 11: Snapshot Navigation & Protected History
**Goal**: Users can move between `Mês Atual` and closed snapshots, understand when history is selected, and face confirmation before control-state changes on closed months.
**Depends on**: Phase 10
**Requirements**: PERIOD-02, SNAP-02, SNAP-03, GUARD-01
**Success Criteria** (what must be TRUE):
  1. User can switch the main financial context between `Mês Atual` and any previously closed monthly snapshot.
  2. User can clearly identify from the UI when the selected context is a closed monthly snapshot instead of `Mês Atual`.
  3. User can review the preserved financial state of a closed monthly snapshot without converting it back into the editable month.
  4. User must confirm before executing an action that changes the control state of a closed monthly snapshot.
**Plans**: TBD
**UI hint**: yes

## Progress

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 8. Current Month Setup & Guards | 1/1 | Complete | 2026-04-04 |
| 9. Period-Linked Records & Cleanup | 0/TBD | Ready to plan | - |
| 10. Month Closure & Next Month Start | 0/TBD | Not started | - |
| 11. Snapshot Navigation & Protected History | 0/TBD | Not started | - |
