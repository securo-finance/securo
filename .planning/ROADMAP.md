# Roadmap: Flux

## Milestones

- ✅ **v1.0 Flux v2** - Phases 1-7 shipped on 2026-04-03. Archive: `.planning/milestones/v1.0-ROADMAP.md`
- ✅ **v1.1 Gestão Mensal** - Phases 8-12 shipped on 2026-04-04. Archive: `.planning/milestones/v1.1-ROADMAP.md`
- ◆ **v1.2 Gestão de contas e faturas** - Phases 1-4 planned on 2026-04-04

## Current Status

`v1.2 Gestão de contas e faturas` is active. Phase numbering was reset to `1` for the new milestone after archiving the previous live phase directories.

## Current Roadmap

**4 phases** | **12 requirements mapped** | All covered ✓

| # | Phase | Goal | Requirements | Success Criteria |
|---|-------|------|--------------|------------------|
| 1 | Connected Accounts & Model Simplification | Replace legacy wallets/account-balance assumptions with a connected-account management surface | CONN-01, CONN-02, CONN-03, CONN-04, MODEL-01, MODEL-02 | 4 |
| 2 | Bill Retrieval Pipeline | Change Pluggy integration to retrieve only current-month credit-card bill data | BILL-01, BILL-02 | 3 |
| 3 | Scheduled Bill Persistence & Categorization Baseline | Persist imported bill rows into Flux-owned monthly storage on a daily schedule with uncategorized defaults | BILL-03, BILL-04, BILL-05 | 4 |
| 4 | Monthly Totals Integration & Milestone Hardening | Make the month summary run on `receitas - despesas` and remove residual balance-based behavior | MODEL-03 | 4 |

## Phase Details

### Phase 1: Connected Accounts & Model Simplification

**Goal**: Users manage only Pluggy-connected accounts in `Contas`, while wallets and per-account balances disappear from the supported runtime.
**Depends on**: None
**Requirements**: CONN-01, CONN-02, CONN-03, CONN-04, MODEL-01, MODEL-02

**Success criteria:**
1. `Contas` exposes only Pluggy-connected account records in supported UI and API flows.
2. User can rename, toggle bill import, and delete a connected account without orphaning related integration metadata.
3. Supported runtime paths no longer expose wallet management.
4. Supported runtime paths no longer display or depend on per-account balances.

### Phase 2: Bill Retrieval Pipeline

**Goal**: Flux fetches only credit-card bill data from Pluggy for the current monthly workflow.
**Depends on**: Phase 1
**Requirements**: BILL-01, BILL-02

**Success criteria:**
1. Pluggy sync paths stop importing general account transactions in the supported runtime.
2. Bill retrieval uses `GET /accounts/{accountId}/bills`.
3. Bill selection for `Mês Atual` filters by `dueDate` in the month after the active monthly period.

### Phase 3: Scheduled Bill Persistence & Categorization Baseline

**Goal**: Enabled connections ingest new bill transactions daily into app-owned monthly storage with predictable categorization behavior.
**Depends on**: Phase 2
**Requirements**: BILL-03, BILL-04, BILL-05

**Success criteria:**
1. A daily scheduled job checks enabled connected accounts for new bill data.
2. Imported bill transactions are persisted in Flux-owned storage associated with the source card and `Mês Atual`.
3. Imported bill transactions default to no category when no saved rule matches.
4. Existing categorization rules can assign a category during import when a match exists.

### Phase 4: Monthly Totals Integration & Milestone Hardening

**Goal**: The active month computes its result from `receitas - despesas` and the application no longer leaks the old balance-based model.
**Depends on**: Phase 3
**Requirements**: MODEL-03

**Success criteria:**
1. Monthly summaries and related reads derive the month result from `receitas - despesas`.
2. Imported credit-card bill transactions participate correctly in current-month totals.
3. Residual UI or API assumptions about account balances are removed or guarded.
4. The milestone ships without reintroducing wallets or general transaction sync as supported behavior.

## Next Step

Run `$gsd-discuss-phase 1` to clarify the connected-account model and implementation approach for Phase 1, or `$gsd-plan-phase 1` to plan it directly.
