---
phase: 3
slug: unified-category-budget-editing
status: approved
shadcn_initialized: true
preset: existing-securo-shell
created: 2026-04-03
---

# Phase 3 — UI Design Contract

> Keep the current app shell intact while making category and budget editing one mobile-friendly flow.

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

| Token | Value | Usage |
|-------|-------|-------|
| xs | 4px | Inline icon spacing |
| sm | 8px | Field help text and small gaps |
| md | 16px | Default form spacing |
| lg | 24px | Card and section padding |
| xl | 32px | Page-level gaps |

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
| Dominant (60%) | `#F8F9FB` / `#0C0D12` | Background |
| Secondary (30%) | `#FFFFFF` / `#16171F` | Cards and dialogs |
| Accent (10%) | `#6366F1` / `#818CF8` | Active buttons, switch state, links |
| Destructive | `#F43F5E` / `#FB7185` | Delete actions only |

Accent reserved for: the budget toggle active state, primary save actions, and page CTA buttons

---

## Copywriting Contract

| Element | Copy |
|---------|------|
| Primary CTA | Keep action verbs short: save, manage, edit |
| Empty state heading | Keep existing concise tone |
| Empty state body | Explain that budget editing now lives in categories |
| Error state | Problem-first, no extra flourish |
| Destructive confirmation | Preserve current delete wording |

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
