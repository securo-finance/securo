# 0001. Record architecture decisions

- Status: Accepted
- Date: 2026-11-17
- Origin: #1470

## Context

Securo's structural decisions (how data is scoped per workspace, how optional features stay free when off, how money is stored) lived in code, in pull request threads and in maintainers' heads. A contributor, human or coding agent, who meets a piece of code that looks odd has no way to tell a deliberate choice from an accident, and "simplifying" a deliberate choice is how regressions start.

## Decision

We keep architecture decision records in `docs/adr/`, one decision per file, in the format of `0000-template.md`. The rules for writing them are in `docs/adr/README.md`. `AGENTS.md` at the repository root points at them and maps each feature to its files.

The first records (0002 to 0018) describe decisions already in the code, written after the fact.

## Consequences

- A pull request that makes or changes an architecture decision adds or supersedes an ADR.
- Code comments cite the ADR (`ADR 0008`) instead of repeating the reasoning.
- Records go stale only through supersession, so the history of why stays readable.

## Enforced by

Review only.
