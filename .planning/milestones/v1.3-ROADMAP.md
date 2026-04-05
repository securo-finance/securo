# Roadmap: Flux

## Milestones

- ✅ **v1.0 Flux v2** - Phases 1-7 shipped on 2026-04-03. Archive: `.planning/milestones/v1.0-ROADMAP.md`
- ✅ **v1.1 Gestão Mensal** - Phases 8-12 shipped on 2026-04-04. Archive: `.planning/milestones/v1.1-ROADMAP.md`
- ✅ **v1.2 Gestão de contas e faturas** - Phases 1-4 shipped on 2026-04-05. Archive: `.planning/milestones/v1.2-ROADMAP.md`
- 🚧 **v1.3 Gestão de Cartões** - Phases 1-3 planned

## Current Status

`v1.3 Gestão de Cartões` is active. Phase numbering was reset to `1` for this milestone because previous live phase directories were already archived.

## Current Roadmap

**3 phases** | **9 requirements mapped** | All covered ✓

| # | Phase | Goal | Requirements | Success Criteria | Status |
|---|-------|------|--------------|------------------|--------|
| 1 | Cards Navigation Surface | Add the `Cartões` entry and a browsable list of imported Pluggy cards | CARD-01, CARD-02, CARD-03 | 3 | Complete |
| 2 | Card Transaction Browsing | Open a card-scoped transaction experience that mirrors the main `Transações` flow | CARD-04, CARD-05, CARD-06 | 4 | Complete |
| 3 | Snapshots and Category Filters | Keep card browsing compatible with snapshot selection and category filtering | CARD-07, CARD-08, CARD-09 | 4 | Complete |

## Phase Details

### Phase 1: Cards Navigation Surface

**Goal**: Users can discover imported Pluggy cards from a dedicated `Cartões` area in the main navigation.
**Depends on**: None
**Requirements**: CARD-01, CARD-02, CARD-03

**Success criteria:**
1. The main navigation shows `Cartões` below `Transações`.
2. The `Cartões` screen renders the imported Pluggy cards available in Flux.
3. Each listed card exposes enough existing metadata for the user to recognize which card it is.

### Phase 2: Card Transaction Browsing

**Goal**: Users can open one card and inspect only that card's imported transactions in a familiar transaction workflow.
**Depends on**: Phase 1
**Requirements**: CARD-04, CARD-05, CARD-06

**Success criteria:**
1. Selecting a card from `Cartões` opens a dedicated card transaction screen.
2. The card transaction screen shows only transactions associated with the selected card.
3. The card transaction screen preserves the main `Transações` list behaviors where those behaviors are compatible with card-scoped browsing.
4. Navigation into and out of the card transaction screen keeps the selected card context clear to the user.

### Phase 3: Snapshots and Category Filters

**Goal**: The card transaction workflow remains useful across monthly snapshots and category-focused review.
**Depends on**: Phase 2
**Requirements**: CARD-07, CARD-08, CARD-09

**Success criteria:**
1. The selected card transaction screen can switch between available monthly snapshots without dropping card context.
2. Category filters apply correctly to the selected card's transactions.
3. Snapshot selection and category filtering can be combined in the same card transaction view.
4. Empty or no-match states remain understandable when a snapshot or category filter produces no transactions.

## Next Step

Implementation is complete. Run manual validation on the new `Cartões` flow, then decide whether to archive the milestone.
