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

## Cross-Milestone Trends

### Process Evolution

| Milestone | Sessions | Phases | Key Change |
|-----------|----------|--------|------------|
| v1.0 | 1 | 7 | Established the archive-and-reset milestone lifecycle after a tightly scoped brownfield migration |

### Cumulative Quality

| Milestone | Tests | Coverage | Zero-Dep Additions |
|-----------|-------|----------|-------------------|
| v1.0 | Targeted backend suites plus frontend builds per phase | Not recorded | 0 |

### Top Lessons (Verified Across Milestones)

1. Build the supported replacement path before removing legacy surfaces.
2. Keep milestone scope narrow when refactoring core domain concepts in a brownfield app.
