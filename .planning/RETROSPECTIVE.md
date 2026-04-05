# Project Retrospective

*A living document updated after each milestone. Lessons feed forward into future planning.*

## Milestone: v1.0 — Flux v2

**Shipped:** 2026-04-03
**Phases:** 7 | **Plans:** 7 | **Sessions:** 1

### What Was Built
- Rebranded the shipped product shell, setup copy, and export filenames from Securo to Flux.
- Moved active budget ownership onto categories and kept compatibility readers working during the transition.
- Consolidated category and budget editing into one mobile-safe flow, then removed the standalone budgets surface.
- Finished the flat-category migration through reader cutover, runtime contract cleanup, and setup seeding changes.

### What Worked
- The phase order stayed coherent: migration first, unified editor second, cutover and removal work last.
- Verification stayed close to delivery, so each phase closed with concrete build or targeted test evidence.
- Narrow milestone scope prevented churn and kept the flat-category direction consistent through all seven phases.

### What Was Inefficient
- Requirement-level verification still had to be inferred during the milestone audit because phase verification files do not list REQ IDs directly.
- Nyquist validation discovery was enabled, but no `*-VALIDATION.md` artifacts were produced during execution.
- Some cleanup debt remained intentionally deferred, including dormant internal category-group code and compatibility export payloads.

### Patterns Established
- Use category-owned budget state as the source of truth and keep compatibility readers as thin adapters during migrations.
- Remove legacy UI surfaces only after the supported replacement path is already shipped and verified.
- Treat phase summaries and verification artifacts as milestone inputs, then collapse active planning docs once a release ships.

### Key Lessons
1. Brownfield migrations move faster when the user-facing write path is unified before downstream reader cutover and legacy removal.
2. Milestone audits are much cheaper when verification artifacts carry requirement IDs directly instead of relying on roadmap inference.

### Cost Observations
- Model mix: balanced profile configured; no per-model usage split was recorded in repo artifacts.
- Sessions: 1 milestone delivery session recorded on 2026-04-03.
- Notable: one-day milestone execution worked because the scope stayed narrow and phase boundaries were explicit.

---

## Milestone: v1.2 — Gestão de contas e faturas

**Shipped:** 2026-04-05
**Phases:** 4 | **Plans:** 4 | **Sessions:** 1

### What Was Built
- Replaced the supported `Contas` experience with Pluggy-connected account management, including rename, delete, and bill-import toggle flows.
- Changed Pluggy sync to import only credit-card bill data and tied bill selection to the month after the active `Mês Atual`.
- Moved imported bill persistence onto a daily schedule and stored bill rows against Flux-owned monthly records with rule-only categorization.
- Updated dashboard and report surfaces so the supported financial summary now centers on `receitas - despesas` instead of account balances.

### What Worked
- The phase split matched the domain seams cleanly: account model, provider ingestion, persistence, then summary UX hardening.
- Reusing the explicit monthly-period model from `v1.1` kept the bill-import work focused and avoided a second data-model rewrite.
- Single-plan phases kept the milestone easy to reason about and archive.

### What Was Inefficient
- The archive CLI failed to extract milestone accomplishments and task counts from the phase artifacts, so completion still required manual cleanup.
- The checked-in planning state referenced a passed milestone audit, but no `v1.2-MILESTONE-AUDIT.md` artifact existed on disk.
- Backend verification remained weaker than frontend verification because no runnable local `pytest` environment was available in the shell.

### Patterns Established
- Treat third-party sync as a narrow ingestion source and persist normalized monthly records inside Flux-owned models.
- Keep user-facing financial summaries aligned to the monthly period model instead of leaking account-ledger concepts back into the UI.
- Archive milestone execution artifacts promptly so the live planning surface stays small and milestone-scoped.

### Key Lessons
1. A monthly-control architecture makes finance-domain scope cuts easier because new import flows can target one explicit active period.
2. Milestone automation needs artifact validation, not just file copying, or completion scripts will silently archive incomplete metadata.

### Cost Observations
- Model mix: balanced profile configured; no per-model usage split was recorded in repo artifacts.
- Sessions: 1 milestone completion session recorded on 2026-04-05.
- Notable: four focused phases were enough to replace the account model without reopening the month-state architecture.

---

## Cross-Milestone Trends

### Process Evolution

| Milestone | Sessions | Phases | Key Change |
|-----------|----------|--------|------------|
| v1.0 | 1 | 7 | Established the archive-and-reset milestone lifecycle after a tightly scoped brownfield migration |
| v1.2 | 1 | 4 | Shifted account management and imports onto a bill-only, month-owned model without reopening period architecture |

### Cumulative Quality

| Milestone | Tests | Coverage | Zero-Dep Additions |
|-----------|-------|----------|-------------------|
| v1.0 | Targeted backend suites plus frontend builds per phase | Not recorded | 0 |
| v1.2 | Frontend builds per phase; backend shell verification only | Not recorded | 0 |

### Top Lessons (Verified Across Milestones)

1. Build the supported replacement path before removing legacy surfaces.
2. Keep milestone scope narrow when refactoring core domain concepts in a brownfield app.
3. Completion automation needs post-run validation or archive metadata quality will drift.
