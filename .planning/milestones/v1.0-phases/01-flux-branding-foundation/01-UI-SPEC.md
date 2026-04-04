---
phase: 1
slug: flux-branding-foundation
status: approved
shadcn_initialized: true
preset: existing-securo-shell
created: 2026-04-03
---

# Phase 1 — UI Design Contract

> Visual and interaction contract for frontend phases. Generated during autonomous workflow and aligned to the existing product shell.

---

## Design System

| Property | Value |
|----------|-------|
| Tool | shadcn |
| Preset | existing app tokens in `frontend/src/index.css` |
| Component library | radix |
| Icon library | lucide-react |
| Font | Geist |

---

## Spacing Scale

Declared values (must be multiples of 4):

| Token | Value | Usage |
|-------|-------|-------|
| xs | 4px | Icon gaps, compact inline spacing |
| sm | 8px | Field grouping, small stack gaps |
| md | 16px | Default component spacing |
| lg | 24px | Section padding and card spacing |
| xl | 32px | Auth page and shell layout gaps |
| 2xl | 48px | Major section breaks |
| 3xl | 64px | Full-page vertical spacing |

Exceptions: none

---

## Typography

| Role | Size | Weight | Line Height |
|------|------|--------|-------------|
| Body | 14px | 400 | 1.5 |
| Label | 13px | 500 | 1.4 |
| Heading | 24px | 700 | 1.2 |
| Display | 32px | 700 | 1.1 |

---

## Color

| Role | Value | Usage |
|------|-------|-------|
| Dominant (60%) | `#F8F9FB` / `#0C0D12` | App background and page canvas |
| Secondary (30%) | `#FFFFFF` / `#16171F` | Cards, sidebar, menus |
| Accent (10%) | `#6366F1` / `#818CF8` | Logo mark, active nav, focus accents, primary actions |
| Destructive | `#F43F5E` / `#FB7185` | Destructive actions only |

Accent reserved for: shell logo mark, active navigation state, primary buttons, and small highlight treatments already present in the existing UI

---

## Copywriting Contract

| Element | Copy |
|---------|------|
| Primary CTA | Keep existing action verbs; branding work should not rename functional CTAs |
| Empty state heading | Preserve current product language unless it contains `Securo`, then replace with `Flux` |
| Empty state body | Preserve existing instructional tone and next-step guidance |
| Error state | Keep concise problem-first wording; no brand flourish in errors |
| Destructive confirmation | Preserve current action-specific confirmation copy |

---

## Registry Safety

| Registry | Blocks Used | Safety Gate |
|----------|-------------|-------------|
| shadcn official | existing primitives only | not required |

---

## Checker Sign-Off

- [x] Dimension 1 Copywriting: PASS
- [x] Dimension 2 Visuals: PASS
- [x] Dimension 3 Color: PASS
- [x] Dimension 4 Typography: PASS
- [x] Dimension 5 Spacing: PASS
- [x] Dimension 6 Registry Safety: PASS

**Approval:** approved 2026-04-03
